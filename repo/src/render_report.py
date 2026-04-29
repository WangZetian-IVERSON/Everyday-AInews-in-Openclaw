"""Render the daily news report as a polished, magazine-style PDF.

Reads:
  - data/raw/{date}.json     : structured items (title/url/source/summary/...)
  - data/reports/{date}.md   : LLM-curated report (today's highlights + risks)

Writes:
  - data/reports/{date}.html : standalone HTML (for debugging/preview)
  - data/reports/{date}.pdf  : print-ready PDF rendered by WeasyPrint
"""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from src.common import RAW_DIR, REPORTS_DIR, REPO_ROOT, get_settings, log_event, now_in_tz


# --- Source pretty names + accent colors (magazine palette) ----------------- #

SOURCE_META: dict[str, dict[str, str]] = {
    "anthropic_news":         {"label": "Anthropic",        "accent": "#c84b31"},
    "openai_blog":            {"label": "OpenAI",           "accent": "#10a37f"},
    "deepmind_blog":          {"label": "Google DeepMind",  "accent": "#4285f4"},
    "huggingface_blog":       {"label": "Hugging Face",     "accent": "#ff9d00"},
    "huggingface_papers":     {"label": "HF Papers",        "accent": "#e8a317"},
    "arxiv_cs_ai":            {"label": "arXiv cs.AI",      "accent": "#b31b1b"},
    "arxiv_cs_cl":            {"label": "arXiv cs.CL",      "accent": "#8b1a1a"},
    "marktechpost":           {"label": "MarkTechPost",     "accent": "#3a6df0"},
    "google_research":        {"label": "Google Research",  "accent": "#1a73e8"},
    "meta_ai_blog":           {"label": "Meta AI",          "accent": "#0866ff"},
    "techcrunch_ai":          {"label": "TechCrunch AI",    "accent": "#0a9c39"},
    "the_verge_ai":           {"label": "The Verge",        "accent": "#5200ff"},
    "venturebeat_ai":         {"label": "VentureBeat",      "accent": "#ed3833"},
    "mit_news_ai":            {"label": "MIT News",         "accent": "#a31f34"},
    "reddit_localllama":      {"label": "Reddit · LocalLLaMA",     "accent": "#ff4500"},
    "reddit_machinelearning": {"label": "Reddit · MachineLearning", "accent": "#ff4500"},
    "github_trending_python": {"label": "GitHub Trending",  "accent": "#1f1b16"},
    "hn_ai":                  {"label": "Hacker News",      "accent": "#ff6600"},
}


def _source_label(key: str) -> str:
    meta = SOURCE_META.get(key)
    if meta:
        return meta["label"]
    return key.replace("_", " ").title()


def _source_accent(key: str) -> str:
    meta = SOURCE_META.get(key)
    if meta:
        return meta["accent"]
    palette = ["#c84b31", "#3a6df0", "#10a37f", "#ff9d00",
               "#5200ff", "#ed3833", "#0a9c39", "#a31f34"]
    h = sum(ord(c) for c in key)
    return palette[h % len(palette)]


# --- Markdown report parsing ------------------------------------------------- #

_SECTION_NAMES = ["今日要点", "新闻详情", "风险与不确定性", "信息来源"]


def _split_sections(md: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not md:
        return out
    pattern = "|".join(re.escape(s) for s in _SECTION_NAMES)
    parts = re.split(rf"^({pattern})\s*[:：]?\s*$", md, flags=re.MULTILINE)
    if len(parts) < 3:
        return out
    for i in range(1, len(parts), 2):
        name = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        out[name] = body.strip()
    return out


def _bullet_lines(body: str) -> list[str]:
    points: list[str] = []
    for line in (body or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(?:\d+[\.、]\s*|[-*•]\s*)+(.+)$", line)
        if m:
            value = m.group(1).strip().lstrip("•").strip()
            if value:
                points.append(value)
        elif len(line) > 4:
            points.append(line)
    return points


# --- Item enrichment from raw + summarized report --------------------------- #

def _clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return text


def _shorten_title(title: str, max_chars: int = 110) -> str:
    title = _clean_text(title)

    # Strip common feed-injected prefixes like "Apr 28, 2026 Announcements " or
    # "Product Apr 17, 2026 " — these clutter Anthropic news titles especially.
    months = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    categories = r"(?:Announcements|Product|Research|News|Engineering|Policy)"
    prefix_patterns = [
        rf"^{categories}\s+{months}\s+\d{{1,2}},\s*\d{{4}}\s+",
        rf"^{months}\s+\d{{1,2}},\s*\d{{4}}\s+{categories}\s+",
        rf"^{months}\s+\d{{1,2}},\s*\d{{4}}\s+",
        rf"^{categories}\s+",
    ]
    for pat in prefix_patterns:
        new = re.sub(pat, "", title, count=1)
        if new != title:
            title = new
            break

    # Strip a trailing date-injected category as well.
    title = re.sub(rf"\s+{categories}\s+{months}\s+\d{{1,2}},\s*\d{{4}}.*$", "", title)

    cut = title.strip()
    for sep in ["。", ". ", "! ", "? "]:
        idx = cut.find(sep)
        if 12 < idx < max_chars:
            cut = cut[: idx + len(sep)].strip()
            break
    if len(cut) > max_chars:
        cut = cut[: max_chars - 1].rstrip() + "…"
    return cut


def _domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc
        return netloc.replace("www.", "")
    except Exception:
        return ""


def _extract_summaries_from_md(md_body: str) -> dict[str, dict[str, str]]:
    """Parse the '新闻详情' body to map url -> {"en": ..., "zh": ...}."""
    if not md_body:
        return {}
    out: dict[str, dict[str, str]] = {}
    blocks = re.split(r"\n\s*\d+\.\s", "\n" + md_body)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        url_m = re.search(r"链接\s*[:：]\s*(\S+)", block)
        en_m = re.search(
            r"(?<!中文)摘要\s*[:：]\s*(.+?)(?:\n\s*(?:中文摘要|链接|来源)\s*[:：]|\n\s*\n|\Z)",
            block, flags=re.DOTALL,
        )
        zh_m = re.search(
            r"中文摘要\s*[:：]\s*(.+?)(?:\n\s*(?:链接|来源|摘要)\s*[:：]|\n\s*\n|\Z)",
            block, flags=re.DOTALL,
        )
        if not url_m:
            continue
        url = url_m.group(1).strip()
        entry: dict[str, str] = {}
        if en_m:
            v = " ".join(en_m.group(1).split()).strip()
            if v and "具体细节请见原文" not in v and "原文未提供摘要" not in v:
                entry["en"] = v
        if zh_m:
            v = " ".join(zh_m.group(1).split()).strip()
            if v and "中文摘要生成失败" not in v:
                entry["zh"] = v
        if entry:
            out[url] = entry
    return out


# --- HTML rendering ---------------------------------------------------------- #

CSS = r"""
@page {
  size: A4;
  margin: 18mm 16mm 18mm 16mm;
  @bottom-center {
    content: "AI 新闻日报 · " string(report-date) " · 第 " counter(page) " / " counter(pages) " 页";
    font-family: "Noto Sans CJK SC", "Noto Sans", sans-serif;
    font-size: 8.5pt;
    color: #8a8275;
  }
}
@page :first {
  margin: 0;
  @bottom-center { content: none; }
}

html, body {
  font-family: "Noto Serif CJK SC", "Noto Serif", "DejaVu Serif", serif;
  color: #1f1b16;
  background: #faf8f3;
  font-size: 10.5pt;
  line-height: 1.65;
}

h1, h2, h3, h4 { font-family: "Noto Serif CJK SC", serif; font-weight: 700; }
.sans { font-family: "Noto Sans CJK SC", "Noto Sans", sans-serif; }

/* ---- Cover page ---- */
.cover {
  position: relative;
  height: 297mm;
  width: 210mm;
  background: linear-gradient(160deg, #1f1b16 0%, #2c2520 55%, #4a2418 100%);
  color: #faf8f3;
  padding: 36mm 24mm 28mm 24mm;
  string-set: report-date var(--cover-date, "");
  page-break-after: always;
  box-sizing: border-box;
}
.cover::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 8mm;
  background: linear-gradient(90deg, #c84b31, #ff9d00, #f5d76e);
}
.cover .eyebrow {
  font-family: "Noto Sans CJK SC", sans-serif;
  font-size: 11pt;
  letter-spacing: 0.45em;
  text-transform: uppercase;
  color: #f5d76e;
  margin-bottom: 14mm;
}
.cover h1 {
  font-size: 56pt;
  line-height: 1.05;
  margin: 0 0 8mm 0;
  letter-spacing: 0.02em;
}
.cover .date {
  font-family: "Noto Sans CJK SC", sans-serif;
  font-size: 18pt;
  color: #faf8f3;
  opacity: 0.9;
  margin-bottom: 22mm;
}
.cover .meta {
  font-family: "Noto Sans CJK SC", sans-serif;
  font-size: 10.5pt;
  color: #f5d76e;
  letter-spacing: 0.06em;
}
.cover .meta strong { color: #faf8f3; font-weight: 600; }
.cover .meta-row { display: flex; gap: 18mm; margin-top: 4mm; }
.cover .footer {
  position: absolute;
  bottom: 18mm;
  left: 24mm;
  right: 24mm;
  border-top: 1px solid rgba(245, 215, 110, 0.35);
  padding-top: 6mm;
  font-family: "Noto Sans CJK SC", sans-serif;
  font-size: 9pt;
  color: rgba(250, 248, 243, 0.7);
  display: flex;
  justify-content: space-between;
}

/* ---- Section headers ---- */
.section-eyebrow {
  font-family: "Noto Sans CJK SC", sans-serif;
  font-size: 9pt;
  letter-spacing: 0.45em;
  text-transform: uppercase;
  color: #c84b31;
  margin-bottom: 2mm;
  page-break-after: avoid;
  break-after: avoid;
}
h2.section-title {
  font-size: 22pt;
  margin: 0 0 6mm 0;
  border-bottom: 2px solid #1f1b16;
  padding-bottom: 3mm;
  page-break-after: avoid;
  break-after: avoid;
}

/* ---- Highlights ---- */
.highlights {
  background: #fff;
  border: 1px solid #ece5d6;
  border-left: 4px solid #c84b31;
  padding: 8mm 10mm;
  margin: 0 0 10mm 0;
  border-radius: 2px;
}
.highlights ol { margin: 0; padding-left: 6mm; }
.highlights li {
  margin-bottom: 3mm;
  font-size: 11pt;
  line-height: 1.6;
}
.highlights li::marker { color: #c84b31; font-weight: 700; }

/* ---- Source group ---- */
.source-group { margin-bottom: 8mm; }
.source-header {
  page-break-after: avoid;
  break-after: avoid;
  display: flex;
  align-items: center;
  gap: 4mm;
  margin: 6mm 0 4mm 0;
  padding-bottom: 2mm;
  border-bottom: 1px dashed #c8bda4;
}
.source-pill {
  display: inline-block;
  padding: 1mm 4mm;
  border-radius: 999px;
  background: #c84b31;
  color: #fff;
  font-family: "Noto Sans CJK SC", sans-serif;
  font-size: 9.5pt;
  font-weight: 600;
  letter-spacing: 0.05em;
}
.source-count {
  font-family: "Noto Sans CJK SC", sans-serif;
  font-size: 9pt;
  color: #8a8275;
}

/* ---- News card ---- */
.card {
  border: 1px solid #ece5d6;
  background: #fff;
  border-radius: 3px;
  padding: 6mm 7mm 5mm 7mm;
  margin-bottom: 4mm;
  page-break-inside: avoid;
  position: relative;
  border-left: 3px solid #c84b31;
}
.card .number {
  position: absolute;
  top: 5mm;
  right: 7mm;
  font-family: "Noto Sans CJK SC", sans-serif;
  font-size: 9pt;
  color: #b9ad92;
  letter-spacing: 0.08em;
}
.card h3 {
  font-size: 13.5pt;
  margin: 0 14mm 3mm 0;
  line-height: 1.35;
  color: #1f1b16;
}
.card .summary {
  font-size: 10.5pt;
  line-height: 1.7;
  color: #3a342c;
  margin: 0 0 4mm 0;
}
.card .summary-en {
  font-style: italic;
  color: #4a4238;
  margin-bottom: 2mm;
}
.card .summary-zh {
  font-family: "Noto Serif CJK SC", serif;
  color: #1f1b16;
  border-left: 2px solid #c84b31;
  padding-left: 3mm;
  margin: 0 0 4mm 0;
}
.card .meta {
  font-family: "Noto Sans CJK SC", sans-serif;
  font-size: 8.5pt;
  color: #8a8275;
  display: flex;
  gap: 4mm;
  align-items: center;
  flex-wrap: wrap;
}
.card .meta a {
  color: #6b5d44;
  text-decoration: none;
  border-bottom: 1px dotted #c8bda4;
  word-break: break-all;
}
.card .domain {
  background: #f3ecdb;
  padding: 0.5mm 2mm;
  border-radius: 2px;
  color: #6b5d44;
  font-weight: 600;
}

/* ---- Risks block ---- */
.risks {
  margin-top: 8mm;
  background: #fff8e8;
  border: 1px solid #f5d76e;
  border-left: 4px solid #d49b13;
  padding: 6mm 8mm;
  border-radius: 2px;
  page-break-inside: avoid;
}
.risks ul { margin: 0; padding-left: 5mm; }
.risks li { margin-bottom: 2.5mm; font-size: 10pt; line-height: 1.6; }

/* ---- Source roster footer ---- */
.sources-footer {
  margin-top: 10mm;
  padding-top: 5mm;
  border-top: 1px solid #c8bda4;
  font-family: "Noto Sans CJK SC", sans-serif;
  font-size: 9pt;
  color: #6b5d44;
}
.sources-footer .roster { display: flex; flex-wrap: wrap; gap: 2mm 3mm; margin-top: 2mm; }
.sources-footer .chip {
  background: #fff;
  border: 1px solid #ece5d6;
  padding: 1mm 3mm;
  border-radius: 999px;
}
"""


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _render_html(date_str: str, items: list[dict], md_sections: dict[str, str],
                 meta: dict) -> str:
    highlights = _bullet_lines(md_sections.get("今日要点", ""))
    risks      = _bullet_lines(md_sections.get("风险与不确定性", ""))
    md_summaries = _extract_summaries_from_md(md_sections.get("新闻详情", ""))

    grouped: dict[str, list[dict]] = {}
    for it in items:
        grouped.setdefault(it.get("source") or "unknown", []).append(it)

    sources_present = list(grouped.keys())
    total_items = len(items)

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_pretty = dt.strftime("%Y 年 %m 月 %d 日")
        weekday_cn = "一二三四五六日"[dt.weekday()]
        weekday = f"星期{weekday_cn}"
    except Exception:
        date_pretty = date_str
        weekday = ""

    generated_at = meta.get("generated_at") or now_in_tz(get_settings().timezone).strftime("%Y-%m-%d %H:%M")

    parts: list[str] = []
    parts.append("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>")
    parts.append(f"<title>AI 新闻日报 · {_esc(date_str)}</title>")
    parts.append(f"<style>:root {{ --cover-date: '{_esc(date_str)}'; }}\n{CSS}</style>")
    parts.append("</head><body>")

    # ---- Cover ----
    parts.append("<section class='cover'>")
    parts.append("<div class='eyebrow'>DAILY · AI INTELLIGENCE</div>")
    parts.append("<h1>AI 新闻<br/>日报</h1>")
    parts.append(f"<div class='date'>{_esc(date_pretty)} &nbsp; · &nbsp; {_esc(weekday)}</div>")
    parts.append("<div class='meta'>")
    parts.append("<div class='meta-row'>")
    parts.append(f"<div>条目 &nbsp;&nbsp;<strong>{total_items}</strong></div>")
    parts.append(f"<div>来源 &nbsp;&nbsp;<strong>{len(sources_present)}</strong></div>")
    parts.append(f"<div>编号 &nbsp;&nbsp;<strong>{_esc((meta.get('run_id') or '')[:8] or '—')}</strong></div>")
    parts.append("</div></div>")
    parts.append("<div class='footer'>")
    parts.append("<div>由 OpenClaw + Anthropic Claude 自动生成</div>")
    parts.append(f"<div>{_esc(generated_at)}</div>")
    parts.append("</div>")
    parts.append("</section>")

    # ---- Highlights ----
    if highlights:
        parts.append("<div class='section-eyebrow'>TODAY · 今日要点</div>")
        parts.append("<h2 class='section-title'>编辑视角</h2>")
        parts.append("<div class='highlights'><ol>")
        for h in highlights:
            parts.append(f"<li>{_esc(h)}</li>")
        parts.append("</ol></div>")

    # ---- News by source ----
    parts.append("<div class='section-eyebrow'>NEWSROOM · 新闻详情</div>")
    parts.append("<h2 class='section-title'>今日新闻</h2>")

    counter = 0
    for src, src_items in grouped.items():
        accent = _source_accent(src)
        label  = _source_label(src)
        parts.append("<div class='source-group'>")
        parts.append("<div class='source-header'>")
        parts.append(f"<span class='source-pill' style='background:{accent}'>{_esc(label)}</span>")
        parts.append(f"<span class='source-count'>· {len(src_items)} 条</span>")
        parts.append("</div>")

        for it in src_items:
            counter += 1
            url = it.get("url") or ""
            title = _shorten_title(it.get("title") or "(无标题)")
            entry = md_summaries.get(url) or {}
            en_summary = entry.get("en") or _clean_text(it.get("summary") or "")
            if en_summary and ("具体细节请见原文" in en_summary or "原文未提供摘要" in en_summary):
                en_summary = ""
            zh_summary = entry.get("zh") or ""
            if zh_summary and "中文摘要生成失败" in zh_summary:
                zh_summary = ""
            dom = _domain(url)
            parts.append(f"<div class='card' style='border-left-color:{accent}'>")
            parts.append(f"<div class='number'>NO. {counter:02d}</div>")
            parts.append(f"<h3>{_esc(title)}</h3>")
            if en_summary:
                parts.append(f"<p class='summary summary-en'>{_esc(en_summary)}</p>")
            if zh_summary:
                parts.append(f"<p class='summary summary-zh'>{_esc(zh_summary)}</p>")
            parts.append("<div class='meta'>")
            if dom:
                parts.append(f"<span class='domain'>{_esc(dom)}</span>")
            if url:
                parts.append(f"<a href='{_esc(url)}'>{_esc(url)}</a>")
            parts.append("</div>")
            parts.append("</div>")
        parts.append("</div>")

    # ---- Risks ----
    if risks:
        parts.append("<div class='section-eyebrow'>CAVEATS · 风险与不确定性</div>")
        parts.append("<h2 class='section-title'>留意事项</h2>")
        parts.append("<div class='risks'><ul>")
        for r in risks:
            parts.append(f"<li>{_esc(r)}</li>")
        parts.append("</ul></div>")

    # ---- Sources footer ----
    parts.append("<div class='sources-footer'>")
    parts.append("信息来源")
    parts.append("<div class='roster'>")
    for src in sources_present:
        parts.append(f"<span class='chip'>{_esc(_source_label(src))}</span>")
    parts.append("</div></div>")

    parts.append("</body></html>")
    return "".join(parts)


# --- Public API -------------------------------------------------------------- #

def render(date_str: str) -> dict:
    settings = get_settings()
    raw_path = RAW_DIR / f"{date_str}.json"
    md_path  = REPORTS_DIR / f"{date_str}.md"
    html_path = REPORTS_DIR / f"{date_str}.html"
    pdf_path  = REPORTS_DIR / f"{date_str}.pdf"

    if not raw_path.exists():
        raise FileNotFoundError(f"raw not found: {raw_path}")

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    items: list[dict] = raw.get("items") if isinstance(raw, dict) else raw
    items = items or []
    meta = {
        "run_id": (raw.get("run_id") if isinstance(raw, dict) else "") or "",
        "generated_at": (raw.get("generated_at") if isinstance(raw, dict) else "") or "",
    }

    md_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    sections = _split_sections(md_text)

    html_text = _render_html(date_str, items, sections, meta)
    html_path.write_text(html_text, encoding="utf-8")

    from weasyprint import HTML  # imported lazily
    HTML(string=html_text, base_url=str(REPO_ROOT)).write_pdf(str(pdf_path))

    log_event(
        {
            "ts": now_in_tz(settings.timezone).isoformat(),
            "run_id": meta.get("run_id") or "unknown",
            "step": "render_report",
            "source": "weasyprint",
            "count": len(items),
            "elapsed_ms": 0,
            "status": "success",
            "html": str(html_path),
            "pdf": str(pdf_path),
            "pdf_bytes": pdf_path.stat().st_size,
        }
    )

    return {"html": str(html_path), "pdf": str(pdf_path), "items": len(items)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render the daily news PDF")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()
    out = render(args.date)
    print(json.dumps(out, ensure_ascii=False, indent=2))
