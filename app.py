from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict
import os
import re
import html
import httpx
from datetime import datetime
from email.utils import parsedate_to_datetime

app = FastAPI(title="Daily Briefing API")

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
SERVICE_ACCESS_TOKEN = os.getenv("SERVICE_ACCESS_TOKEN")

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"

CATEGORY_QUERIES = {
    "agriculture": [
        "농업",
        "스마트농업",
        "축산",
        "농업기술",
        "농촌진흥청",
        "농림축산식품부",
	"원예",
	"과수",
	"스마트팜",
	"스마트 농업",
	"가축",
	"농업위성",
	"기상재해",
	"폭염",
    ],
    "it": [
        "AI",
        "반도체",
        "클라우드",
        "사이버보안",
        "빅테크",
	"gpu",
	"엔비디아",
	"테슬라",
	"인공지능",
	"gpt",
	"클로드",
	"챗지피티",
	"제미나이",
	"라이다",
	"애플",
	"삼성전자",
	"하이닉스",
	"피지컬 ai",
	"IT쇼",
	"초거대 AI",
	"AX",
	"네이버",
	"카카오",
	"앤스로픽",
	

    ],
    "science": [
        "과학기술",
        "연구개발",
        "R&D",
        "우주",
        "바이오",
        "로봇",
	"보안",
	"NASA",
	"아르테미스",
	"양자컴퓨터",
	"우주정거장",
	"스마트홈",
    ],
}

class BriefingRequest(BaseModel):
    include_schedule: bool = False
    include_news: bool = True
    news_categories: List[str] = ["agriculture", "it", "science"]
    news_count_per_category: int = 3
    language: str = "ko"

def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<.*?>", "", text)
    return text.strip()

def normalize_title(title: str) -> str:
    title = clean_text(title).lower()
    title = re.sub(r"[^a-z0-9가-힣\s]", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title

def parse_pub_date(pub_date: str) -> str:
    try:
        dt = parsedate_to_datetime(pub_date)
        return dt.isoformat()
    except Exception:
        return pub_date

async def fetch_news(query: str, display: int = 10, sort: str = "date") -> List[dict]:
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {
        "query": query,
        "display": display,
        "start": 1,
        "sort": sort,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(NAVER_NEWS_URL, headers=headers, params=params)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Naver API error: {resp.text}")
        data = resp.json()
        return data.get("items", [])

def dedupe_items(items: List[dict]) -> List[dict]:
    seen = set()
    result = []
    for item in items:
        norm = normalize_title(item.get("title", ""))
        if norm in seen:
            continue
        seen.add(norm)
        result.append(item)
    return result

def convert_item(item: dict) -> dict:
    return {
        "title": clean_text(item.get("title", "")),
        "summary": clean_text(item.get("description", "")),
        "naver_link": item.get("link", ""),
        "original_link": item.get("originallink", ""),
        "pub_date": parse_pub_date(item.get("pubDate", "")),
    }

@app.get("/")
def root():
    return {"message": "Daily Briefing API is running"}

@app.post("/briefing")
async def briefing(
    req: BriefingRequest,
    authorization: Optional[str] = Header(default=None)
):
    if authorization != f"Bearer {SERVICE_ACCESS_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET or not SERVICE_ACCESS_TOKEN:
        raise HTTPException(status_code=500, detail="Missing environment variables")

    news_result: Dict[str, List[dict]] = {}

    if req.include_news:
        for category in req.news_categories:
            queries = CATEGORY_QUERIES.get(category, [])
            collected = []
            for q in queries:
                items = await fetch_news(q, display=10, sort="date")
                collected.extend(items)

            deduped = dedupe_items(collected)
            converted = [convert_item(x) for x in deduped]
            converted.sort(key=lambda x: x.get("pub_date", ""), reverse=True)
            news_result[category] = converted[: req.news_count_per_category]

    return {
        "date": datetime.now().date().isoformat(),
        "schedule": None,
        "workload_summary": None,
        "news": news_result,
    }

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

async def fetch_google_calendar_list(access_token: str):
    headers = {"Authorization": f"Bearer {access_token}"}
    url = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Google Calendar list error: {resp.text}")
        return resp.json().get("items", [])

async def fetch_google_events(access_token: str, calendar_id: str, time_min: str, time_max: str):
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
    params = {
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": "true",
        "orderBy": "startTime",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            return []
        return resp.json().get("items", [])

def summarize_workload(events):
    count = len(events)
    if count == 0:
        return "오늘 확인 가능한 일정이 없습니다. 비교적 집중 업무를 진행하기 좋은 흐름입니다."
    if count <= 2:
        return "오늘은 일정이 많지 않아 연구·분석·보고서 작성 같은 집중 업무를 병행하기 좋습니다."
    if count <= 5:
        return "오늘은 일정과 집중 업무를 함께 운영해야 하는 날입니다."
    return "오늘은 일정이 많은 편이므로 즉시 대응 업무 중심으로 운영하는 것이 좋습니다."

def build_todo(events):
    todos = []
    if events:
        todos.append("오전 첫 일정 전에 회의 안건과 준비자료 확인")
    if len(events) <= 2:
        todos.append("집중 시간이 확보되는 구간에 보고서 또는 분석 업무 진행")
    else:
        todos.append("일정 사이 자투리 시간을 활용해 즉시 처리할 업무 우선 정리")
    todos.append("오늘 브리핑된 뉴스 중 업무 관련 기사 1~2건 우선 검토")
    return todos[:4]

@app.post("/calendar/today")
async def calendar_today(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")

    access_token = authorization.replace("Bearer ", "", 1).strip()

    now = datetime.now(KST)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)

    calendar_list = await fetch_google_calendar_list(access_token)

    selected_calendars = []
    for cal in calendar_list:
        if cal.get("selected", True):
            selected_calendars.append({
                "id": cal.get("id"),
                "summary": cal.get("summary", "")
            })

    all_events = []
    for cal in selected_calendars:
        items = await fetch_google_events(
            access_token,
            cal["id"],
            start_of_day.isoformat(),
            end_of_day.isoformat()
        )
        for item in items:
            start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
            end = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
            all_events.append({
                "start": start,
                "end": end,
                "title": item.get("summary", "(제목 없음)"),
                "location": item.get("location", ""),
                "calendar_name": cal["summary"]
            })

    all_events.sort(key=lambda x: x["start"] or "")

    return {
        "date": start_of_day.date().isoformat(),
        "events": all_events,
        "workload_summary": summarize_workload(all_events),
        "todo_suggestions": build_todo(all_events)
    }