from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

from src.common import get_settings, log_event, now_in_tz


def _extract_message_id(payload: dict) -> str:
    for key in ("message_id", "messageId", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value

    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("message_id", "messageId", "id"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _parse_json_output(text: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
            if isinstance(payload, dict):
                return payload
        except Exception:
            continue
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _parse_text_message_id(text: str) -> str:
    patterns = [
        r"(?im)^\s*message\s*[_ ]?id\s*[:=]\s*([^\s]+)\s*$",
        r"(?im)^\s*message\s+id\s*[:=]\s*([^\s]+)\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            value = (match.group(1) or "").strip()
            if value:
                return value
    return ""


def _load_report(date_str: str) -> str:
    path = Path(__file__).resolve().parents[2] / "data" / "reports" / f"{date_str}.md"
    return path.read_text(encoding="utf-8")


def _report_pdf_path(date_str: str) -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "reports" / f"{date_str}.pdf"


def _build_short_preview(report_text: str, date_str: str, item_count: int = 0) -> str:
    """Pull the first few '今日要点' bullets out of the full markdown to use
    as a short text-message preview that accompanies the PDF attachment."""
    highlights: list[str] = []
    in_section = False
    for raw_line in (report_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            if in_section and highlights:
                break
            continue
        if line.startswith("今日要点"):
            in_section = True
            continue
        if in_section:
            if re.match(r"^[一-龥A-Za-z].*[:：]\s*$", line) and "今日要点" not in line:
                break
            m = re.match(r"^(?:\d+[\.、]\s*|[-*•]\s*)+(.+)$", line)
            if m:
                highlights.append(m.group(1).strip())
            elif len(line) > 4 and "：" not in line[-2:]:
                highlights.append(line)
            if len(highlights) >= 3:
                break

    lines = [f"📰 AI 新闻日报 · {date_str}"]
    if item_count:
        lines.append(f"今日共 {item_count} 条新闻，PDF 完整版见附件。")
    else:
        lines.append("PDF 完整版见附件。")
    if highlights:
        lines.append("")
        lines.append("今日要点：")
        for i, h in enumerate(highlights, start=1):
            lines.append(f"{i}. {h}")
    return "\n".join(lines)


def _run_cli_send(report_text: str, media_path: str | None = None) -> tuple[str, str, str]:
    settings = get_settings()

    if not settings.openclaw_cli_bin:
        raise ValueError("OPENCLAW_CLI_BIN is empty")
    if not settings.openclaw_channel:
        raise ValueError("OPENCLAW_CHANNEL is empty")

    cmd = [
        settings.openclaw_cli_bin,
        "message",
        "send",
        "--channel",
        settings.openclaw_channel,
        "--target",
        settings.wechat_target_id,
        "--message",
        report_text,
        "--json",
    ]

    if media_path:
        cmd.extend(["--media", media_path])

    if settings.openclaw_account:
        cmd.extend(["--account", settings.openclaw_account])

    timeout_sec = 180 if media_path else 60
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        error = f"exit_code={completed.returncode} stderr={stderr[:500]} stdout={stdout[:500]}"
        return "failed", "", error

    stdout_text = completed.stdout or ""
    stderr_text = completed.stderr or ""

    body = _parse_json_output(stdout_text)
    message_id = _extract_message_id(body)
    if not message_id:
        message_id = _parse_text_message_id(stdout_text)
    if not message_id:
        message_id = _parse_text_message_id(stderr_text)
    return "success", message_id, ""


def _run_http_send(report_text: str) -> tuple[str, str, str]:
    settings = get_settings()

    if not settings.openclaw_base_url:
        raise ValueError("OPENCLAW_BASE_URL is empty")

    headers = {
        "Authorization": f"Bearer {settings.openclaw_api_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "target_id": settings.wechat_target_id,
        "content": report_text,
        "msg_type": "text",
    }

    url = urljoin(settings.openclaw_base_url.rstrip("/") + "/", settings.openclaw_send_path.lstrip("/"))
    response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)

    if response.status_code < 200 or response.status_code >= 300:
        error = f"http_status={response.status_code} body={response.text[:500]}"
        return "failed", "", error

    message_id = ""
    try:
        body = response.json()
        message_id = _extract_message_id(body)
    except Exception:
        message_id = ""

    return "success", message_id, ""


def run(date_str: str, dry_run: bool = False, send_pdf: bool = True):
    settings = get_settings()
    started = time.perf_counter()
    report_text = _load_report(date_str)

    pdf_path: Path | None = None
    item_count = 0
    if send_pdf:
        try:
            from src.render_report import render as render_pdf
            result = render_pdf(date_str)
            pdf_path = Path(result["pdf"])
            item_count = int(result.get("items") or 0)
        except Exception as exc:
            # Don't block the daily message if PDF rendering fails — fall back to text.
            print(f"WARNING: PDF render failed, falling back to text: {exc}")
            pdf_path = None

    if pdf_path and pdf_path.exists():
        message_body = _build_short_preview(report_text, date_str, item_count)
        media_arg: str | None = str(pdf_path)
    else:
        message_body = report_text
        media_arg = None

    if dry_run:
        target_display = settings.wechat_target_id or "<empty>"
        print(
            "dry_run=true, send skipped "
            f"mode={settings.openclaw_send_mode} "
            f"channel={settings.openclaw_channel} target={target_display} "
            f"media={'<pdf>' if media_arg else '<none>'}"
        )
        print("--- preview body ---")
        print(message_body)
        return {"status": "success", "message_id": "dry-run", "media": media_arg}

    if not settings.wechat_target_id:
        raise ValueError("WECHAT_TARGET_ID is empty")

    mode = (settings.openclaw_send_mode or "cli").strip().lower()
    if mode == "cli":
        status, message_id, error = _run_cli_send(message_body, media_path=media_arg)
        source = "openclaw-cli"
    elif mode == "http":
        # HTTP path does not yet support media — send text only.
        status, message_id, error = _run_http_send(message_body)
        source = "openclaw-http"
    else:
        raise ValueError("OPENCLAW_SEND_MODE must be 'cli' or 'http'")

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    log_event(
        {
            "ts": now_in_tz(settings.timezone).isoformat(),
            "run_id": "unknown",
            "step": "send_wechat",
            "source": source,
            "count": 1,
            "elapsed_ms": elapsed_ms,
            "status": status,
            "error": error,
        }
    )

    if status != "success":
        raise RuntimeError(error)

    print(f"message_id={message_id}")
    return {"status": status, "message_id": message_id}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send daily report to WeChat via OpenClaw")
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format")
    parser.add_argument("--dry-run", action="store_true", help="Validate payload without sending")
    parser.add_argument("--no-pdf", action="store_true", help="Send plain markdown only, skip PDF rendering/attachment")
    args = parser.parse_args()
    run(args.date, dry_run=args.dry_run, send_pdf=not args.no_pdf)
