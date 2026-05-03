# Everyday AI News Bot

Automates daily AI news aggregation → LLM summarization → PDF report → WeChat delivery via OpenClaw CLI.

## Setup

```bash
bash repo/install.sh
```

Then set these in `.env`:

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | ✓ | LLM API key |
| `MODEL_ID` | ✓ | Model to use |
| `OPENCLAW_CHANNEL` | ✓ | Target channel |
| `WECHAT_TARGET_ID` | one of | Single recipient |
| `WECHAT_FANOUT` | one of | Broadcast |

## Pipeline

```
fetch_news.py → summarize_news.py → render_report.py → send_wechat.py
```

Runs daily via cron. Logs to `data/logs/pipeline.jsonl`.

## News Sources

Configured in `news_sources.json`. Supported types: RSS (default), HTML scraping, Hacker News (Algolia API with score filtering).

## Troubleshooting

Most issues are caused by missing nvm Node in PATH or `.env` permission errors — the installer handles both.

---

> For internal use only. Respect OpenClaw and news source terms of service.
