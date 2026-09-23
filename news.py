"""Current context: recent headlines from Google News RSS (no API key needed)."""

import calendar
import logging
import time
from dataclasses import dataclass
from urllib.parse import quote_plus

import feedparser
import httpx

log = logging.getLogger(__name__)


@dataclass
class NewsItem:
    title: str
    source: str
    published: str
    link: str

    def as_line(self) -> str:
        return f"- {self.title} ({self.source}, {self.published})"


async def fetch_news(query: str, region: str = "IN", max_items: int = 5, max_age_days: int = 30) -> list[NewsItem]:
    if not query:
        return []
    url = (
        f"https://news.google.com/rss/search?q={quote_plus(query + f' when:{max_age_days}d')}"
        f"&hl=en-{region}&gl={region}&ceid={region}:en"
    )
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (meera-linkedin-bot)"})
            resp.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("News fetch failed: %s", e)
        return []

    feed = feedparser.parse(resp.text)
    cutoff = time.time() - max_age_days * 86400
    items: list[NewsItem] = []
    for entry in feed.entries:
        ts = calendar.timegm(entry.published_parsed) if entry.get("published_parsed") else None
        if ts and ts < cutoff:
            continue
        source = entry.get("source", {}).get("title", "") if isinstance(entry.get("source"), dict) else ""
        title = entry.get("title", "")
        # Google News appends " - Source" to titles
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        items.append(
            NewsItem(
                title=title,
                source=source or "unknown source",
                published=time.strftime("%d %b %Y", time.gmtime(ts)) if ts else "date unknown",
                link=entry.get("link", ""),
            )
        )
        if len(items) >= max_items:
            break
    return items
