# AI 新闻日报 · 微信自动推送 / AI News Daily · WeChat Auto Push

每天定时抓取 AI 新闻 → LLM 生成中文日报 → 渲染 PDF → 通过 OpenClaw CLI 推送到微信。

A daily pipeline: fetch AI news → summarize into Chinese report via LLM → render PDF → push to WeChat through OpenClaw CLI.

## 流程 / Pipeline

```
fetch_news → summarize_news → render_report → send_wechat
```

入口：`python -m src.run_pipeline`，由 cron 每天触发一次。

Entry point: `python -m src.run_pipeline`, triggered once a day by cron.

## 目录结构 / Layout

```
ai-news-bot/
├── config/.env                # 运行配置 / runtime config (API key, WeChat target, ...)
├── venv/                      # Python 虚拟环境 / virtualenv
├── data/
│   ├── raw/<date>.json        # 抓取到的原始条目 / raw fetched items
│   ├── reports/<date>.md      # LLM 生成的中文日报 / generated Chinese report
│   ├── reports/<date>.html    # 渲染中间产物 / intermediate HTML
│   ├── reports/<date>.pdf     # 推送给微信的附件 / PDF sent to WeChat
│   ├── state/<date>.sent      # 已发送标记 / sent marker, prevents duplicates
│   └── logs/pipeline.jsonl    # 每一步执行日志 / per-step JSONL log
└── repo/
    ├── prompts/
    └── src/
        ├── run_pipeline.py    # 串联四个步骤 / chains the four steps
        ├── fetch_news.py      # RSS / 网页抓取 / RSS & web fetch
        ├── summarize_news.py  # 单次 LLM 调用产出要点+风险+逐条中文摘要
        │                      # single LLM call → key_points + risks + per-item zh summary
        ├── render_report.py   # markdown → HTML → PDF (WeasyPrint)
        └── send_wechat.py     # 调用 openclaw CLI 推送 / push via openclaw CLI
```

## 环境配置 / Configuration

`config/.env`（属主 `wangzetian1`，权限 `600`）关键字段：

`config/.env` (owner `wangzetian1`, mode `600`) — key fields:

```ini
# LLM（任何 OpenAI 兼容端点都可，如 Fireworks / OpenRouter）
# LLM (any OpenAI-compatible endpoint, e.g. Fireworks / OpenRouter)
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.fireworks.ai/inference/v1
MODEL_ID=fw_JTEMiPhkR8hjPEEx663tRH
MAX_OUTPUT_TOKENS=1200
MAX_INPUT_CHARS=20000
MAX_NEWS_ITEMS=20

# 微信推送（通过 OpenClaw CLI） / WeChat push via OpenClaw CLI
OPENCLAW_SEND_MODE=cli
OPENCLAW_CLI_BIN=openclaw
OPENCLAW_CHANNEL=openclaw-weixin
OPENCLAW_ACCOUNT=...
WECHAT_TARGET_ID=...

TZ=Asia/Shanghai
SEND_HOUR=22
SEND_MINUTE=0
```

`OPENAI_API_KEY` 只是变量名，可填任何兼容服务的 key；端点由 `OPENAI_BASE_URL` 决定。

`OPENAI_API_KEY` is just a variable name — any OpenAI-compatible API key works. The actual endpoint is set by `OPENAI_BASE_URL`.

## 定时任务 / Cron

以用户 `wangzetian1` 安装的 crontab：

Crontab installed under user `wangzetian1`:

```cron
0 10 * * * export PATH="/home/wangzetian1/.nvm/versions/node/v24.15.0/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"; \
  cd /home/wangzetian1/ai-news-bot/repo && \
  echo "[cron trigger] $(date -Is)" >> /home/wangzetian1/ai-news-bot/data/logs/cron.log && \
  /home/wangzetian1/ai-news-bot/venv/bin/python -m src.run_pipeline \
  >> /home/wangzetian1/ai-news-bot/data/logs/cron.log 2>&1
```

要点 / Notes:
- 必须显式 `export PATH=...nvm/.../bin`，否则 `send_wechat` 找不到 `node` / `openclaw`，会报 `exit_code=127`。
  Must explicitly `export PATH=...nvm/.../bin`, otherwise `send_wechat` cannot find `node` / `openclaw` and fails with `exit_code=127`.
- 用 `wangzetian1` 身份运行，才能读到 `config/.env`（属主 600 权限）。
  Must run as `wangzetian1` to read `config/.env` (owner-only, mode 600).

查看 / 编辑 — View / edit:
```bash
sudo -u wangzetian1 crontab -l
sudo -u wangzetian1 crontab -e
```

## 手动运行 / Manual run

必须以 `wangzetian1` 身份执行（否则 `.env` 权限不够）：

Must run as `wangzetian1` (otherwise `.env` is not readable):

```bash
# 跑完整流水线 / full pipeline
sudo -u wangzetian1 bash -lc '
  export PATH=$HOME/.nvm/versions/node/v24.15.0/bin:$PATH
  cd /home/wangzetian1/ai-news-bot && source venv/bin/activate
  cd repo && python -m src.run_pipeline
'

# 只重跑某一步（指定日期） / re-run a single step for a given date
sudo -u wangzetian1 bash -lc '
  cd /home/wangzetian1/ai-news-bot && source venv/bin/activate && cd repo
  python -m src.fetch_news      --date 2026-04-29
  python -m src.summarize_news  --date 2026-04-29
  python -m src.render_report   --date 2026-04-29
  python -m src.send_wechat     --date 2026-04-29
'
```

`send_wechat` 支持 `--dry-run`（不真发）和 `--no-pdf`（只发文本）。

`send_wechat` supports `--dry-run` (do not actually send) and `--no-pdf` (text only).

重发需要先删 `state/` 标记 / To resend, delete the state marker first:
```bash
rm /home/wangzetian1/ai-news-bot/data/state/2026-04-29.sent
```

## 报告格式 / Report format

`summarize_news` 单次 LLM 调用直接返回结构化 JSON，组装出固定模板：

`summarize_news` performs a single LLM call returning structured JSON, then assembles a fixed template:

```
标题：AI 新闻日报 - YYYY-MM-DD

今日要点：                       # Key points (3 items, synthesized over all news)
1. ...
2. ...
3. ...

新闻详情：                       # Per-item details
1. 标题：...                     # Title
   来源：...                     # Source
   链接：...                     # URL
   摘要：...                     # Original (English) summary from RSS
   中文摘要：...                  # LLM-generated Chinese summary
... 共 N 条 ... / N items total

风险与不确定性：                  # Risks & uncertainties
- ...

信息来源：                       # Deduplicated source list
- ...
```

LLM 失败时走 `_fallback_report`：仅输出标题 + 链接列表，不阻塞推送。

If the LLM call fails, `_fallback_report` kicks in: titles + links only, so the push is never blocked.

## 日志与排错 / Logs & troubleshooting

每一步在 `data/logs/pipeline.jsonl` 追加一行 JSON：

Each step appends one JSON line to `data/logs/pipeline.jsonl`:

```json
{"ts":"...","run_id":"...","step":"summarize_news","status":"success","elapsed_ms":54453,"error":""}
```

常见问题 / Common issues:

| 现象 / Symptom | 原因 / Cause | 处理 / Fix |
|---|---|---|
| `PermissionError: .../config/.env` | 不是 `wangzetian1` 用户 / not running as `wangzetian1` | 用 `sudo -u wangzetian1 ...` |
| `summarize_news` 频繁 `status=fallback`，error=`OPENAI_API_KEY is empty` / repeatedly falls back with empty key | `.env` 没读到或 key 为空 / `.env` not loaded or key empty | 检查 .env 路径与权限 / check `.env` path & perms |
| `send_wechat` 报 `exit_code=127 ... 'node': No such file` | PATH 缺少 nvm 的 node / nvm node not in PATH | `export PATH=$HOME/.nvm/versions/node/<ver>/bin:$PATH` |
| 微信收不到但日志 success / log says success but WeChat shows nothing | OpenClaw 账号/目标 ID 配置不对 / wrong OpenClaw account / target id | 校对 `OPENCLAW_ACCOUNT` / `WECHAT_TARGET_ID` |
| 当天重复推送被跳过 / duplicate push skipped | `data/state/<date>.sent` 已存在 / sent marker exists | 删除该文件后重跑 / delete it and rerun |

实时跟踪 / Tail logs:
```bash
tail -f /home/wangzetian1/ai-news-bot/data/logs/pipeline.jsonl
tail -f /home/wangzetian1/ai-news-bot/data/logs/cron.log
```
