from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
import os
import re
import html
import httpx
import base64
import hmac
import hashlib
import json
import time
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

app = FastAPI(title="Daily Briefing API")

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
SERVICE_ACCESS_TOKEN = os.getenv("SERVICE_ACCESS_TOKEN")

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"


GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
OAUTH_SIGNING_SECRET = os.getenv("OAUTH_SIGNING_SECRET")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_OAUTH_CALLBACK_URL = "https://rda-briefing.vercel.app/oauth/google/callback"
GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly "
    "https://www.googleapis.com/auth/calendar.events.readonly"
)

KST = timezone(timedelta(hours=9))





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
async def briefing(req: BriefingRequest):



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

def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")

def b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)

def sign_payload(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sig = hmac.new(OAUTH_SIGNING_SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{b64url_encode(raw)}.{b64url_encode(sig)}"

def verify_payload(token: str) -> dict:
    try:
        raw_b64, sig_b64 = token.split(".", 1)
        raw = b64url_decode(raw_b64)
        sig = b64url_decode(sig_b64)
        expected = hmac.new(OAUTH_SIGNING_SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError("Invalid signature")
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid signed payload: {str(e)}")


@app.get("/oauth/google/authorize")
async def google_authorize(request: Request):
    redirect_uri_from_chatgpt = request.query_params.get("redirect_uri")
    state_from_chatgpt = request.query_params.get("state")

    if not redirect_uri_from_chatgpt or not state_from_chatgpt:
        raise HTTPException(status_code=400, detail="Missing redirect_uri or state")

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not OAUTH_SIGNING_SECRET:
        raise HTTPException(status_code=500, detail="Missing Google OAuth environment variables")

    relay_state = sign_payload({
        "chatgpt_redirect_uri": redirect_uri_from_chatgpt,
        "chatgpt_state": state_from_chatgpt,
        "ts": int(time.time())
    })

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_OAUTH_CALLBACK_URL,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": relay_state,
    }

    return RedirectResponse(url=f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@app.get("/oauth/google/callback")
async def google_callback(code: str, state: str):
    relay = verify_payload(state)

    chatgpt_redirect_uri = relay["chatgpt_redirect_uri"]
    chatgpt_state = relay["chatgpt_state"]

    async with httpx.AsyncClient(timeout=20.0) as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": GOOGLE_OAUTH_CALLBACK_URL,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if token_resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Google token exchange error: {token_resp.text}")

    token_data = token_resp.json()

    broker_code = sign_payload({
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "expires_in": token_data.get("expires_in", 3600),
        "created_at": int(time.time()),
    })

    redirect_back = f"{chatgpt_redirect_uri}?code={broker_code}&state={chatgpt_state}"
    return RedirectResponse(url=redirect_back)


@app.post("/oauth/google/token")
async def google_token(request: Request):
    form = await request.form()
    grant_type = form.get("grant_type")

    if grant_type == "authorization_code":
        code = form.get("code")
        if not code:
            raise HTTPException(status_code=400, detail="Missing code")

        payload = verify_payload(code)

        return JSONResponse({
            "access_token": payload["access_token"],
            "token_type": "Bearer",
            "expires_in": payload.get("expires_in", 3600),
            "refresh_token": payload.get("refresh_token"),
        })

    if grant_type == "refresh_token":
        refresh_token = form.get("refresh_token")
        if not refresh_token:
            raise HTTPException(status_code=400, detail="Missing refresh_token")

        async with httpx.AsyncClient(timeout=20.0) as client:
            token_resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if token_resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Google refresh error: {token_resp.text}")

        token_data = token_resp.json()

        return JSONResponse({
            "access_token": token_data.get("access_token"),
            "token_type": "Bearer",
            "expires_in": token_data.get("expires_in", 3600),
            "refresh_token": refresh_token,
        })

    raise HTTPException(status_code=400, detail="Unsupported grant_type")


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
        "maxResults": 250,
        "timeZone": "Asia/Seoul",
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
    access_role = cal.get("accessRole")
    if access_role in {"owner", "writer", "reader", "freeBusyReader"}:
        selected_calendars.append({
            "id": cal.get("id"),
            "summary": cal.get("summary", "")
        })

# 혹시 primary가 빠지는 경우를 대비
if not any(c["id"] == "primary" for c in selected_calendars):
    selected_calendars.insert(0, {
        "id": "primary",
        "summary": "Primary"
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
    "todo_suggestions": build_todo(all_events),
    "debug": {
        "calendar_count": len(calendar_list),
        "selected_calendar_count": len(selected_calendars),
        "selected_calendars": selected_calendars,
        "event_count": len(all_events),
        "time_min": start_of_day.isoformat(),
        "time_max": end_of_day.isoformat(),
    }
}
