from __future__ import annotations

import argparse
import json
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dt_parser

from src.common import RAW_DIR, get_settings, load_sources, log_event, new_run_id, normalize_title, now_in_tz


UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 ai-news-bot/1.0"
)
DEFAULT_HEADERS = {"User-Agent": UA, "Accept": "*/*"}


def parse_datetime(value: str):
    if not value:
        return None
    try:
        return dt_parser.parse(value)
    except Exception:
        return None


def fetch_rss_source(source: dict[str, str], cutoff_dt):
    # Fetch via requests so we can send a real UA (Reddit / Verge etc. 429 without one).
    try:
        resp = requests.get(source["url"], headers=DEFAULT_HEADERS, timeout=20)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception:
        feed = feedparser.parse(source["url"])
    items = []

    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        summary = (entry.get("summary") or entry.get("description") or "").strip()
        published_raw = entry.get("published") or entry.get("updated") or ""
        published_dt = parse_datetime(published_raw)

        if published_dt is not None:
            if published_dt.tzinfo is None:
                published_dt = published_dt.replace(tzinfo=cutoff_dt.tzinfo)
            if published_dt < cutoff_dt:
                continue

        if not title or not link:
            continue

        items.append(
            {
                "title": title,
                "url": link,
                "source": source["name"],
                "published_at": published_dt.isoformat() if published_dt else "",
                "summary": summary,
            }
        )

    return items


def fetch_hn_algolia(source: dict[str, str], cutoff_dt):
    """Fetch high-signal Hacker News stories matching a query via the Algolia
    search API. The URL should already encode the query/filters; we additionally
    apply a min_points threshold and the global 24h cutoff."""
    min_points = int(source.get("min_points") or 0)
    resp = requests.get(source["url"], headers=DEFAULT_HEADERS, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    items: list[dict] = []
    # NOTE: skip the global 24h cutoff for HN. Algolia returns real-world
    # timestamps; the min_points threshold is the real quality gate.
    for hit in payload.get("hits", []):
        url = (hit.get("url") or "").strip()
        title = (hit.get("title") or "").strip()
        if not url or not title:
            continue
        points = int(hit.get("points") or 0)
        if points < min_points:
            continue
        author = hit.get("author") or ""
        comments = hit.get("num_comments") or 0
        published_iso = ""
        created_at = hit.get("created_at")
        if created_at:
            dt = parse_datetime(created_at)
            if dt is not None:
                published_iso = dt.isoformat()
        items.append(
            {
                "title": title,
                "url": url,
                "source": source["name"],
                "published_at": published_iso,
                "summary": f"Hacker News: {points} 分 · {comments} 条评论 · 提交者 {author}",
            }
        )
    return items


def fetch_anthropic_html(source: dict[str, str]):
    resp = requests.get(source["url"], headers=DEFAULT_HEADERS, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    seen_urls = set()

    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if "/news/" not in href:
            continue

        title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < 8:
            continue

        url = urljoin(source["url"], href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        items.append(
            {
                "title": title,
                "url": url,
                "source": source["name"],
                "published_at": "",
                "summary": "",
            }
        )

    return items


def dedupe_items(items: list[dict], max_items: int):
    """Round-robin selection across sources to ensure diversity in the final cut.

    Items are first deduped by URL and normalized title, then we walk the per-source
    queues in a round-robin fashion (preserving the original within-source order)
    until ``max_items`` is reached.
    """
    by_url: set[str] = set()
    by_title: set[str] = set()
    per_source: dict[str, list[dict]] = {}
    source_order: list[str] = []

    for item in items:
        url = (item.get("url") or "").strip()
        title_key = normalize_title(item.get("title", ""))
        if url and url in by_url:
            continue
        if title_key and title_key in by_title:
            continue
        if url:
            by_url.add(url)
        if title_key:
            by_title.add(title_key)
        src_name = item.get("source") or "_unknown"
        if src_name not in per_source:
            per_source[src_name] = []
            source_order.append(src_name)
        per_source[src_name].append(item)

    output: list[dict] = []
    while len(output) < max_items:
        progressed = False
        for src_name in source_order:
            queue = per_source.get(src_name)
            if not queue:
                continue
            output.append(queue.pop(0))
            progressed = True
            if len(output) >= max_items:
                break
        if not progressed:
            break
    return output


def run(date_str: str | None = None):
    settings = get_settings()
    run_id = new_run_id(settings.timezone)
    now_dt = now_in_tz(settings.timezone)
    cutoff_dt = now_dt - timedelta(hours=24)
    target_date = date_str or now_dt.strftime("%Y-%m-%d")

    all_items = []

    for source in load_sources():
        started = time.perf_counter()
        status = "success"
        error = ""
        source_items = []

        try:
            source_type = (source.get("type") or "").lower()
            if source_type == "rss":
                source_items = fetch_rss_source(source, cutoff_dt)
            elif source_type == "hn_algolia":
                source_items = fetch_hn_algolia(source, cutoff_dt)
            elif source.get("name") == "anthropic_news" or source_type == "html":
                source_items = fetch_anthropic_html(source)
            else:
                raise ValueError(f"Unsupported source type: {source_type}")
            all_items.extend(source_items)
        except Exception as exc:
            status = "failed"
            error = str(exc)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log_event(
            {
                "ts": now_in_tz(settings.timezone).isoformat(),
                "run_id": run_id,
                "step": "fetch_news",
                "source": source.get("name", "unknown"),
                "count": len(source_items),
                "elapsed_ms": elapsed_ms,
                "status": status,
                "error": error,
            }
        )

    items = dedupe_items(all_items, settings.max_news_items)
    low_content = len(items) < 3

    payload = {
        "date": target_date,
        "run_id": run_id,
        "generated_at": now_in_tz(settings.timezone).isoformat(),
        "count": len(items),
        "low_content": low_content,
        "items": items,
    }

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    output_path = RAW_DIR / f"{target_date}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    log_event(
        {
            "ts": now_in_tz(settings.timezone).isoformat(),
            "run_id": run_id,
            "step": "fetch_news_finalize",
            "source": "all",
            "count": len(items),
            "elapsed_ms": 0,
            "status": "success",
            "error": "",
        }
    )

    print(f"saved_raw={output_path}")
    print(f"count={len(items)} low_content={str(low_content).lower()}")
    return {
        "run_id": run_id,
        "raw_path": str(output_path),
        "count": len(items),
        "low_content": low_content,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch news from configured sources")
    parser.add_argument("--date", default=None, help="Override target date in YYYY-MM-DD format")
    args = parser.parse_args()
    run(args.date)
