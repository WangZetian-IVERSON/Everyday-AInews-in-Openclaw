from __future__ import annotations

import argparse
import html
import json
import re
import time
from pathlib import Path

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from src.common import REPORTS_DIR, get_settings, log_event, now_in_tz


def _load_raw(date_str: str) -> dict:
    path = Path(__file__).resolve().parents[2] / "data" / "raw" / f"{date_str}.json"
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
def _call_llm(client: OpenAI, model_id: str, system_prompt: str, user_content: str, max_tokens: int) -> str:
    resp = client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        max_tokens=max_tokens,
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()


def _clean_summary_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return text


def _extract_json_object(text: str) -> dict:
    cleaned = (text or "").strip()
    if not cleaned:
        return {}

    cleaned = cleaned.replace("```json", "").replace("```", "").strip()

    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = cleaned[start : end + 1]
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict):
                return payload
        except Exception:
            return {}
    return {}


def _llm_full_summary(
    client: OpenAI, model_id: str, items: list[dict], max_tokens: int
) -> dict:
    """Single LLM call producing key_points, risks, and per-item Chinese summaries."""
    if not items:
        return {"key_points": [], "risks": [], "items": {}}

    lines = []
    for idx, item in enumerate(items, start=1):
        title = " ".join((item.get("title") or "").split())
        source = (item.get("source") or "").strip()
        url = (item.get("url") or "").strip()
        existing = _clean_summary_text(item.get("summary") or "")
        lines.append(f"[{idx}] 标题: {title}")
        lines.append(f"来源: {source}")
        lines.append(f"链接: {url}")
        lines.append(f"原文摘要: {existing if existing else '(空)'}")
        lines.append("")

    system_prompt = (
        "你是中文 AI 新闻日报编辑。基于给定的英文/中文新闻条目，"
        "产出结构化的 JSON：包含全局要点、风险与不确定性、以及逐条中文摘要。"
        "全部内容必须为简体中文（专有名词如 OpenAI/Claude/GPT-5/NVIDIA 等可保留英文）。"
        "只输出 JSON，不要解释，不要使用 markdown 代码块。"
    )

    user_prompt = (
        "请阅读以下新闻条目，输出一个 JSON 对象，结构如下：\n"
        "{\n"
        '  "key_points": ["要点1", "要点2", "要点3"],\n'
        '  "risks": ["风险1", "风险2"],\n'
        '  "items": [{"idx": 1, "summary": "中文摘要"}, ...]\n'
        "}\n\n"
        "要求：\n"
        "1. key_points：3 条，每条 30-80 字，综合当日最重要的趋势/事件，"
        "禁止使用占位文案，必须基于实际条目内容。\n"
        "2. risks：2-3 条，每条 20-60 字，指出当日信息中的风险或不确定性"
        "（例如来源偏向、未独立验证、早期发布稿等），禁止套话。\n"
        "3. items：必须覆盖每一个输入条目，idx 与输入编号一致；"
        "summary 为 24-60 字简体中文，准确翻译/改写标题与原文摘要中的关键事实"
        "（产品、机构、数字、动作）；信息不足的条目也要给出不超过 30 字的具体改写，"
        "禁止整句照抄英文，禁止输出“根据标题信息，具体细节请见原文”之类的占位语。\n"
        "4. 不要编造未在输入中出现的链接或事实。\n\n"
        "新闻条目：\n" + "\n".join(lines)
    )

    resp_text = _call_llm(
        client=client,
        model_id=model_id,
        system_prompt=system_prompt,
        user_content=user_prompt,
        max_tokens=max_tokens,
    )
    payload = _extract_json_object(resp_text)

    key_points: list[str] = []
    for kp in payload.get("key_points", []) or []:
        kp_text = " ".join(str(kp).split())
        if kp_text:
            key_points.append(kp_text)

    risks: list[str] = []
    for r in payload.get("risks", []) or []:
        r_text = " ".join(str(r).split())
        if r_text:
            risks.append(r_text)

    item_summaries: dict[int, str] = {}
    for row in payload.get("items", []) or []:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("idx", 0))
        except Exception:
            continue
        summary = " ".join(str(row.get("summary", "")).split())
        if idx >= 1 and summary:
            item_summaries[idx] = summary

    return {"key_points": key_points, "risks": risks, "items": item_summaries}


def _derive_key_points_fallback(items: list[dict], item_summaries: dict[int, str]) -> list[str]:
    points = []
    for idx in range(1, len(items) + 1):
        value = (item_summaries.get(idx) or "").strip()
        if value and value not in points:
            points.append(value)
        if len(points) >= 3:
            break
    return points[:3]


def _build_report(
    date_str: str,
    items: list[dict],
    key_points: list[str],
    risks: list[str],
    item_summaries: dict[int, str],
) -> str:
    if not key_points:
        key_points = _derive_key_points_fallback(items, item_summaries)
    if not key_points:
        key_points = [
            "今日新闻较多，重点关注模型发布、平台合作与工程实践。",
            "部分条目来自厂商公告，结论以原始链接为准。",
            "建议优先阅读近 24 小时内发布的核心条目。",
        ]

    if not risks:
        risks = [
            "部分信息来自早期发布稿，后续可能更新。",
            "不同来源的技术指标口径可能不一致。",
        ]

    lines = [
        f"标题：AI 新闻日报 - {date_str}",
        "",
        "今日要点：",
    ]

    for idx, point in enumerate(key_points[:3], start=1):
        lines.append(f"{idx}. {point}")

    lines.extend(["", "新闻详情："])

    for idx, item in enumerate(items, start=1):
        title = " ".join((item.get("title") or "Untitled").split())
        source = (item.get("source") or "").strip()
        url = (item.get("url") or "").strip()

        en_summary = _clean_summary_text(item.get("summary") or "")
        if not en_summary:
            en_summary = "(原文未提供摘要，请参考链接)"
        if len(en_summary) > 260:
            en_summary = en_summary[:260].rstrip() + "..."

        zh_summary = " ".join((item_summaries.get(idx) or "").split())
        if not zh_summary:
            zh_summary = "中文摘要生成失败，请参考英文摘要。"
        if len(zh_summary) > 220:
            zh_summary = zh_summary[:220].rstrip() + "..."

        lines.append(f"{idx}. 标题：{title}")
        lines.append(f"来源：{source}")
        lines.append(f"链接：{url}")
        lines.append(f"摘要：{en_summary}")
        lines.append(f"中文摘要：{zh_summary}")
        lines.append("")

    lines.append("风险与不确定性：")
    for risk in risks[:3]:
        lines.append(f"- {risk}")

    lines.extend(["", "信息来源："])
    seen_sources = []
    for item in items:
        source = (item.get("source") or "").strip()
        if source and source not in seen_sources:
            seen_sources.append(source)
    for source in seen_sources:
        lines.append(f"- {source}")

    return "\n".join(lines)


def _fallback_report(date_str: str, items: list[dict], reason: str) -> str:
    lines = [
        f"标题：AI 新闻日报 - {date_str}",
        "",
        "今日要点：",
        "1. 今日摘要生成失败，以下为原始新闻链接。",
        "",
        "新闻详情：",
    ]
    for idx, item in enumerate(items, start=1):
        lines.append(f"{idx}. {item.get('title', 'Untitled')}")
        lines.append(f"来源：{item.get('source', '')}")
        lines.append(f"链接：{item.get('url', '')}")
        lines.append("")

    lines.extend(
        [
            "风险与不确定性：",
            f"- 摘要模型调用失败：{reason}",
            "- 本报告未进行自动归纳，仅提供原始链接。",
            "",
            "信息来源：",
            "- 自动抓取新闻源",
        ]
    )
    return "\n".join(lines)


def run(date_str: str):
    settings = get_settings()
    raw_payload = _load_raw(date_str)
    run_id = raw_payload.get("run_id", "unknown")
    items = raw_payload.get("items", [])

    started = time.perf_counter()
    status = "success"
    error = ""

    try:
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is empty")

        client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
        result = _llm_full_summary(
            client=client,
            model_id=settings.model_id,
            items=items,
            max_tokens=settings.max_output_tokens,
        )
        report = _build_report(
            date_str,
            items,
            result.get("key_points", []),
            result.get("risks", []),
            result.get("items", {}),
        )
    except Exception as exc:
        status = "failed"
        error = str(exc)
        report = _fallback_report(date_str, items, error)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{date_str}.md"
    report_path.write_text(report, encoding="utf-8")

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    log_event(
        {
            "ts": now_in_tz(settings.timezone).isoformat(),
            "run_id": run_id,
            "step": "summarize_news",
            "source": "llm",
            "count": len(items),
            "elapsed_ms": elapsed_ms,
            "status": "success" if status == "success" else "fallback",
            "error": error,
        }
    )

    print(f"saved_report={report_path}")
    print(f"status={status}")
    return {"run_id": run_id, "report_path": str(report_path), "status": status, "error": error}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Summarize raw news into daily markdown report")
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format")
    args = parser.parse_args()
    run(args.date)
