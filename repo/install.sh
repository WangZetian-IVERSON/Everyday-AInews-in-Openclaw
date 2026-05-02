#!/usr/bin/env bash
# ai-news-bot one-shot installer (C-3 minimal).
#
# Usage:
#   bash install.sh              # interactive
#   bash install.sh --no-cron    # skip crontab registration
#   bash install.sh --force      # overwrite existing config/.env
#
# Layout produced (relative to project root, the parent of this repo dir):
#   <root>/repo/         this checkout
#   <root>/venv/         Python 3.11+ virtualenv
#   <root>/config/.env   runtime secrets (created from .env.example)
#   <root>/data/{raw,reports,state,logs}

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$REPO_DIR/.." && pwd)"
VENV_DIR="$ROOT_DIR/venv"
CONFIG_DIR="$ROOT_DIR/config"
DATA_DIR="$ROOT_DIR/data"
ENV_FILE="$CONFIG_DIR/.env"
ENV_EXAMPLE="$REPO_DIR/.env.example"

REGISTER_CRON=1
FORCE_ENV=0
for arg in "$@"; do
  case "$arg" in
    --no-cron) REGISTER_CRON=0 ;;
    --force)   FORCE_ENV=1 ;;
    -h|--help) sed -n '1,12p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;36m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. Python --------------------------------------------------------------
say "Checking Python..."
PY_BIN="$(command -v python3.11 || command -v python3 || true)"
[ -n "$PY_BIN" ] || die "python3 not found. Install Python 3.11+ first."
PY_VER="$("$PY_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
case "$PY_VER" in
  3.1[1-9]|3.[2-9][0-9]) ;;
  *) warn "Detected Python $PY_VER; project targets 3.11+. Continuing anyway." ;;
esac

# --- 2. System libs reminder (WeasyPrint) -----------------------------------
if ! ldconfig -p 2>/dev/null | grep -qi 'libpango'; then
  warn "WeasyPrint needs Pango/Cairo. On Debian/Ubuntu run:"
  warn "    sudo apt-get install -y libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 fonts-noto-cjk"
fi

# --- 3. venv + pip ----------------------------------------------------------
say "Creating venv at $VENV_DIR ..."
mkdir -p "$ROOT_DIR"
[ -d "$VENV_DIR" ] || "$PY_BIN" -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --upgrade pip >/dev/null
say "Installing Python deps..."
pip install -r "$REPO_DIR/requirements.txt"

# --- 4. Data dirs -----------------------------------------------------------
say "Creating data dirs under $DATA_DIR ..."
mkdir -p "$DATA_DIR/raw" "$DATA_DIR/reports" "$DATA_DIR/state" "$DATA_DIR/logs"

# --- 5. config/.env ---------------------------------------------------------
mkdir -p "$CONFIG_DIR"
if [ -f "$ENV_FILE" ] && [ "$FORCE_ENV" -ne 1 ]; then
  say "Keeping existing $ENV_FILE (use --force to overwrite)."
else
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  say "Wrote template $ENV_FILE — edit it before first run:"
  say "    OPENAI_API_KEY, MODEL_ID, OPENCLAW_CLI_BIN,"
  say "    OPENCLAW_CHANNEL, WECHAT_TARGET_ID (or WECHAT_FANOUT)"
fi

# --- 6. OpenClaw CLI sanity check ------------------------------------------
if ! command -v openclaw >/dev/null 2>&1; then
  warn "openclaw CLI not on PATH."
  warn "Install Node via nvm and run: npm i -g openclaw @tencent-weixin/openclaw-weixin-cli"
else
  say "openclaw: $(command -v openclaw) ($(openclaw --version 2>/dev/null | head -1))"
fi

# --- 7. Crontab -------------------------------------------------------------
if [ "$REGISTER_CRON" -eq 1 ]; then
  HOUR="${SEND_HOUR:-10}"
  MINUTE="${SEND_MINUTE:-0}"
  NODE_BIN_DIR=""
  if command -v openclaw >/dev/null 2>&1; then
    NODE_BIN_DIR="$(dirname "$(command -v openclaw)")"
  fi
  CRON_PATH="${NODE_BIN_DIR:+$NODE_BIN_DIR:}/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  CRON_LINE="$MINUTE $HOUR * * * export PATH=\"$CRON_PATH\"; cd $REPO_DIR && echo \"[cron trigger] \$(date -Is)\" >> $DATA_DIR/logs/cron.log && $VENV_DIR/bin/python -m src.run_pipeline >> $DATA_DIR/logs/cron.log 2>&1"

  EXISTING="$(crontab -l 2>/dev/null || true)"
  if printf '%s\n' "$EXISTING" | grep -Fq "src.run_pipeline"; then
    say "crontab already has an ai-news-bot entry — leaving it alone."
    say "  Inspect: crontab -l | grep run_pipeline"
  else
    say "Registering crontab entry: $MINUTE $HOUR daily"
    { printf '%s\n' "$EXISTING"; printf '%s\n' "$CRON_LINE"; } \
      | sed '/^$/d' | crontab -
    say "  Inspect:  crontab -l"
    say "  Logs:     tail -f $DATA_DIR/logs/cron.log"
  fi
else
  say "Skipping crontab registration (--no-cron). Suggested line:"
  echo "  0 10 * * * cd $REPO_DIR && $VENV_DIR/bin/python -m src.run_pipeline >> $DATA_DIR/logs/cron.log 2>&1"
fi

# --- 8. Next steps ----------------------------------------------------------
say "Done. Next steps:"
cat <<EOF
  1. Edit secrets:        \$EDITOR $ENV_FILE
  2. Login WeChat once:   openclaw channels login --channel openclaw-weixin
  3. Dry-run pipeline:    cd $REPO_DIR && $VENV_DIR/bin/python -m src.run_pipeline --dry-run
  4. Watch live cron:     tail -f $DATA_DIR/logs/cron.log
EOF
