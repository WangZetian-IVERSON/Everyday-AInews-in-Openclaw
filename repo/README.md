# AI News Bot (Single-User MVP)

This project runs a daily AI-news pipeline:
1. Fetch news
2. Summarize into markdown
3. Send to WeChat via OpenClaw

## Quick install (recommended)

Clone the repo so the layout becomes `<root>/repo/<this checkout>`, then:

    bash repo/install.sh           # creates venv, installs deps, writes config/.env, registers daily cron
    bash repo/install.sh --no-cron # same but skip crontab

After the script finishes:

1. Edit `config/.env` (API keys, `WECHAT_FANOUT` or `WECHAT_TARGET_ID`).
2. `openclaw channels login --channel openclaw-weixin` (one-time WeChat QR scan).
3. Dry-run: `venv/bin/python -m src.run_pipeline --dry-run` from inside `repo/`.

The rest of this README documents the manual setup that `install.sh` automates.

## 1) Environment

Project root layout:
- ../config/.env (runtime secrets)
- ../data/raw
- ../data/reports
- ../data/logs
- ../data/state

Install dependencies:

source ../venv/bin/activate
pip install -r requirements.txt

Install Node.js (no sudo) and OpenClaw CLI once:

export NVM_DIR="$HOME/.nvm"
if [ ! -s "$NVM_DIR/nvm.sh" ]; then
	curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
fi
. "$NVM_DIR/nvm.sh"
nvm install --lts
nvm use --lts
npm install -g openclaw @tencent-weixin/openclaw-weixin-cli

Verify OpenClaw CLI:

openclaw --version

Tip: if a new shell says `openclaw: command not found`, run:

export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"

## 2) Configure .env

Edit ../config/.env and fill at least:
- OPENAI_API_KEY
- OPENAI_BASE_URL (Fireworks endpoint)
- MODEL_ID
- OPENCLAW_SEND_MODE (default: cli)
- OPENCLAW_CLI_BIN (recommended: absolute path from `command -v openclaw`)
- OPENCLAW_CHANNEL (WeChat plugin channel id: openclaw-weixin)
- WECHAT_TARGET_ID

Example for OPENCLAW_CLI_BIN when installed with nvm:

OPENCLAW_CLI_BIN=/home/wangzetian1/.nvm/versions/node/v24.15.0/bin/openclaw

Optional HTTP fallback fields (only when OPENCLAW_SEND_MODE=http):
- OPENCLAW_BASE_URL
- OPENCLAW_API_TOKEN
- OPENCLAW_SEND_PATH

## 3) OpenClaw WeChat setup (official plugin path)

Install and enable WeChat plugin:

npx -y @tencent-weixin/openclaw-weixin-cli install
openclaw gateway restart

Login WeChat (scan QR on this machine):

openclaw channels login --channel openclaw-weixin

Check channel status:

openclaw channels status --probe

Find a target id for single chat or group:

openclaw message channel list

Optional: resolve by name (replace the example name):

openclaw channels resolve --channel openclaw-weixin "Alice"

Send a manual test message:

openclaw message send --channel openclaw-weixin --target "$WECHAT_TARGET_ID" --message "OpenClaw test"

## 4) Run commands

Fetch only:

python -m src.fetch_news --date 2026-04-24

Summarize only:

python -m src.summarize_news --date 2026-04-24

Send only (safe test):

python -m src.send_wechat --date 2026-04-24 --dry-run

Full pipeline (safe test):

python -m src.run_pipeline --date 2026-04-24 --dry-run

Full pipeline (real send):

python -m src.run_pipeline --date 2026-04-24

## 5) Idempotency

Real send writes state file:
- ../data/state/YYYY-MM-DD.sent

Re-running same day skips sending unless force mode is used:

python -m src.run_pipeline --date 2026-04-24 --force

## 6) Cron (Beijing 22:00)

Example crontab entry:

0 22 * * * cd /home/wangzetian1/ai-news-bot/repo && /home/wangzetian1/ai-news-bot/venv/bin/python -m src.run_pipeline >> /home/wangzetian1/ai-news-bot/data/logs/cron.log 2>&1

## 7) OpenClaw send mode notes

Default mode is CLI, implemented via `openclaw message send` in src/send_wechat.py.

If you must use custom HTTP gateway sending, set:
- OPENCLAW_SEND_MODE=http
- OPENCLAW_BASE_URL / OPENCLAW_API_TOKEN / OPENCLAW_SEND_PATH
