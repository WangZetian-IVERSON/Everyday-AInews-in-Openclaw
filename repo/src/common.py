from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dateutil import tz
from dotenv import load_dotenv


BOT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = BOT_ROOT / "data"
LOGS_DIR = DATA_ROOT / "logs"
RAW_DIR = DATA_ROOT / "raw"
REPORTS_DIR = DATA_ROOT / "reports"
STATE_DIR = DATA_ROOT / "state"


def _env_file_path() -> Path:
    env_override = os.getenv("ENV_FILE")
    if env_override:
        return Path(env_override)
    return BOT_ROOT / "config" / ".env"


def load_runtime_env() -> None:
    load_dotenv(_env_file_path(), override=False)


@dataclass(frozen=True)
class Settings:
    timezone: str
    max_news_items: int
    max_input_chars: int
    max_output_tokens: int
    openai_api_key: str
    openai_base_url: str
    model_id: str
    openclaw_send_mode: str
    openclaw_cli_bin: str
    openclaw_channel: str
    openclaw_account: str
    openclaw_base_url: str
    openclaw_api_token: str
    wechat_target_id: str
    openclaw_send_path: str
    send_hour: int
    send_minute: int


def get_settings() -> Settings:
    load_runtime_env()
    return Settings(
        timezone=os.getenv("TZ", "Asia/Shanghai"),
        max_news_items=int(os.getenv("MAX_NEWS_ITEMS", "20")),
        max_input_chars=int(os.getenv("MAX_INPUT_CHARS", "20000")),
        max_output_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "1200")),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.fireworks.ai/inference/v1"),
        model_id=os.getenv("MODEL_ID", "fw_JTEMiPhkR8hjPEEx663tRH"),
        openclaw_send_mode=os.getenv("OPENCLAW_SEND_MODE", "cli"),
        openclaw_cli_bin=os.getenv("OPENCLAW_CLI_BIN", "openclaw"),
        openclaw_channel=os.getenv("OPENCLAW_CHANNEL", "openclaw-weixin"),
        openclaw_account=os.getenv("OPENCLAW_ACCOUNT", ""),
        openclaw_base_url=os.getenv("OPENCLAW_BASE_URL", ""),
        openclaw_api_token=os.getenv("OPENCLAW_API_TOKEN", ""),
        wechat_target_id=os.getenv("WECHAT_TARGET_ID", ""),
        openclaw_send_path=os.getenv("OPENCLAW_SEND_PATH", "/api/v1/messages/send"),
        send_hour=int(os.getenv("SEND_HOUR", "22")),
        send_minute=int(os.getenv("SEND_MINUTE", "0")),
    )


def ensure_data_dirs() -> None:
    for path in (LOGS_DIR, RAW_DIR, REPORTS_DIR, STATE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def now_in_tz(timezone_name: str) -> datetime:
    tzinfo = tz.gettz(timezone_name)
    return datetime.now(tz=tzinfo)


def new_run_id(timezone_name: str) -> str:
    stamp = now_in_tz(timezone_name).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def log_event(event: dict[str, Any]) -> None:
    ensure_data_dirs()
    path = LOGS_DIR / "pipeline.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def normalize_title(title: str) -> str:
    return " ".join((title or "").strip().lower().split())


def load_sources() -> list[dict[str, str]]:
    path = REPO_ROOT / "news_sources.json"
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("sources", [])
