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

    ],
    "science": [
        "과학기술",
        "연구개발",
        "R&D",
        "우주",
        "바이오",
        "로봇",
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