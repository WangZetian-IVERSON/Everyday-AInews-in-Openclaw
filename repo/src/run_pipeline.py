from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.common import STATE_DIR, get_settings, now_in_tz
from src.fetch_news import run as run_fetch
from src.send_wechat import run as run_send
from src.summarize_news import run as run_summarize


def _state_path(date_str: str) -> Path:
    return STATE_DIR / f"{date_str}.sent"


def run(date_str: str | None = None, force: bool = False, dry_run: bool = False):
    settings = get_settings()
    now_dt = now_in_tz(settings.timezone)
    target_date = date_str or now_dt.strftime("%Y-%m-%d")

    state_path = _state_path(target_date)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    if state_path.exists() and not force:
        print(f"skip: already sent for {target_date}")
        return 0

    fetch_result = run_fetch(date_str=target_date)
    summarize_result = run_summarize(date_str=target_date)

    if dry_run:
        send_result = run_send(date_str=target_date, dry_run=True)
    else:
        send_result = run_send(date_str=target_date, dry_run=False)

    if not dry_run:
        state_payload = {
            "date": target_date,
            "run_id": fetch_result.get("run_id", ""),
            "report_path": summarize_result.get("report_path", ""),
            "message_id": send_result.get("message_id", ""),
            "sent_at": now_in_tz(settings.timezone).isoformat(),
            "dry_run": False,
        }
        state_path.write_text(json.dumps(state_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"done: {target_date}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run full daily AI-news pipeline")
    parser.add_argument("--date", default=None, help="Target date in YYYY-MM-DD format")
    parser.add_argument("--force", action="store_true", help="Force resend even if state exists")
    parser.add_argument("--dry-run", action="store_true", help="Run pipeline without actual send")
    args = parser.parse_args()

    code = run(date_str=args.date, force=args.force, dry_run=args.dry_run)
    raise SystemExit(code)
