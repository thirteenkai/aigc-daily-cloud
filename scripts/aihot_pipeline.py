#!/usr/bin/env python3
"""Deterministic AI HOT fetch, validation, card rendering, and Feishu delivery."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


AIHOT_DAILY_URL = "https://aihot.virxact.com/api/v1/dailies/latest"
AIHOT_DAILY_INDEX_URL = "https://aihot.virxact.com/api/v1/dailies?limit=7"
AIHOT_SELECTED_URL = "https://aihot.virxact.com/api/v1/items?mode=selected&window=24h&by=timeline&limit=50"
AIHOT_USER_AGENT = "aigc-daily-publisher/0.4 (+https://aihot.virxact.com/)"
EXPECTED_APP_ID = os.environ['EXPECTED_APP_ID']
EXPECTED_APP_NAME = "章北海"
EXPECTED_CHAT_ID = os.environ['DAILY_CHAT_ID']
DAILY_CHAT_ID = os.environ['DAILY_CHAT_ID']
OPENCLAW_HOME = os.environ.get('OPENCLAW_HOME', '/tmp/aigc-runtime')
SECTIONS = {
    "today": "📰 今日看点",
    "production": "🎬 内容与创作",
    "horizon": "🌐 行业视野",
}
TOPICS = {
    "AI 视频",
    "图像生成",
    "语音与音频",
    "多模态",
    "Agent 智能体",
    "MCP 与工具调用",
    "具身智能",
    "端侧 AI",
    "AI 编码",
    "推理能力",
    "开源生态",
    "数据与训练",
    "安全对齐",
    "部署工程",
    "模型发布",
    "产品更新",
    "论文研究",
    "教程实践",
    "行业动态",
    "现象与趋势",
    "政策监管",
    "版权与合规",
    "商业与创作者生态",
}
TOPIC_ALIASES = {
    "多模态生成": "多模态",
    "实时内容生成": "AI 视频",
    "基础模型": "模型发布",
    "创作者工具": "商业与创作者生态",
}
DAILY_PROHIBITED_PHRASES = ("必须跟进", "建议团队立即执行", "立即测试", "明日跟进")
ALERT_MAX_NEW_EVENT_AGE_HOURS = 48
ALERT_MAX_FUTURE_SKEW_HOURS = 6
ALERT_MATERIAL_UPDATE_PROHIBITED_PHRASES = ("刚发布", "刚刚发布", "首次发布", "刚上线", "刚刚上线")


class PipelineError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"无法读取 JSON：{path}: {exc}") from exc


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(payload)
        tmp = Path(handle.name)
    tmp.replace(path)


def fetch_json(url: str, *, allow_404: bool = False) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": AIHOT_USER_AGENT},
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = json.loads(response.read().decode("utf-8"))
                return body, {
                    "url": url,
                    "etag": response.headers.get("ETag", ""),
                    "fetchedAt": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                }
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and allow_404:
                return None, {
                    "url": url,
                    "status": 404,
                    "fetchedAt": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                }
            last_error = exc
            if attempt < 2 and exc.code >= 500:
                time.sleep(2**attempt)
                continue
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)
    raise PipelineError(f"AI HOT 请求失败：{last_error}")


def validate_daily_response(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schemaVersion") != 1:
        raise PipelineError("AI HOT 官方日报响应结构不符合 v1 合同")
    report = data.get("report")
    if not isinstance(report, dict) or not isinstance(report.get("date"), str):
        raise PipelineError("AI HOT 官方日报缺少 report/date")
    if not isinstance(report.get("sections"), list) or not isinstance(report.get("flashes"), list):
        raise PipelineError("AI HOT 官方日报缺少 sections/flashes")
    return report


def validate_selected_response(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or data.get("schemaVersion") != 1 or not isinstance(data.get("items"), list):
        raise PipelineError("AI HOT 24 小时精选响应结构不符合 v1 合同")
    return data["items"]


def fetch_official_daily() -> tuple[dict[str, Any], dict[str, Any]]:
    data, meta = fetch_json(AIHOT_DAILY_URL, allow_404=True)
    if data is not None:
        validate_daily_response(data)
        meta["route"] = "latest"
        return data, meta

    index, index_meta = fetch_json(AIHOT_DAILY_INDEX_URL)
    if not isinstance(index, dict) or index.get("schemaVersion") != 1 or not isinstance(index.get("items"), list):
        raise PipelineError("AI HOT 日报索引响应结构不符合 v1 合同")
    if not index["items"]:
        raise PipelineError("AI HOT 日报索引为空")
    date = index["items"][0].get("date")
    if not isinstance(date, str):
        raise PipelineError("AI HOT 日报索引缺少日期")
    fallback_url = f"https://aihot.virxact.com/api/v1/dailies/{date}"
    fallback, fallback_meta = fetch_json(fallback_url)
    validate_daily_response(fallback)
    return fallback, {
        "route": "index-fallback",
        "latest": meta,
        "index": index_meta,
        "report": fallback_meta,
    }


def clean_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    return text or None


def normalize_topic(value: Any, field: str) -> str:
    topic = ensure_text(value, field, 2, 40)
    normalized = TOPIC_ALIASES.get(topic, topic)
    if normalized not in TOPICS:
        raise PipelineError(f"无效 topic：{topic}")
    return normalized


def normalize_links(value: Any) -> tuple[dict[str, str | None], str]:
    links = value if isinstance(value, dict) else {}
    aihot = clean_optional_text(links.get("aihot"))
    original = clean_optional_text(links.get("original"))
    if aihot and not any(aihot.startswith(origin + "/") for origin in (
        "https://aihot.virxact.com", "https://aihot.news",
    )):
        raise PipelineError("AI HOT 条目含无效站内链接")
    if original and not original.startswith(("https://", "http://")):
        raise PipelineError("AI HOT 条目含无效原文链接")
    canonical = aihot or original
    if not canonical:
        raise PipelineError("AI HOT 条目缺少可展示链接")
    return {"aihot": aihot, "original": original}, canonical


def normalize_candidate(item: Any, *, origin: str, daily_label: str | None = None) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise PipelineError("AI HOT 候选含无效条目")
    title = ensure_text(item.get("title"), "candidate.title", 1, 180)
    source = item.get("source")
    if not isinstance(source, dict):
        raise PipelineError(f"候选缺少来源：{title}")
    source_name = ensure_text(source.get("name"), "candidate.source.name", 1, 120)
    links, canonical = normalize_links(item.get("links"))
    key = f"candidate-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}"
    return {
        "key": key,
        "id": clean_optional_text(item.get("id")),
        "title": title,
        "summary": clean_optional_text(item.get("summary")),
        "source": {"name": source_name},
        "links": links,
        "displayLink": canonical,
        "publishedAt": clean_optional_text(item.get("publishedAt")),
        "discoveredAt": clean_optional_text(item.get("discoveredAt")),
        "category": clean_optional_text(item.get("category")),
        "score": item.get("score") if isinstance(item.get("score"), (int, float)) else None,
        "origins": [origin],
        "dailyLabels": [daily_label] if daily_label else [],
    }


def merge_candidate(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for field in ("id", "summary", "publishedAt", "discoveredAt", "category", "score"):
        if incoming.get(field) is not None:
            merged[field] = incoming[field]
    merged["links"] = {
        "aihot": incoming["links"].get("aihot") or existing["links"].get("aihot"),
        "original": incoming["links"].get("original") or existing["links"].get("original"),
    }
    merged["displayLink"] = merged["links"]["aihot"] or merged["links"]["original"]
    merged["origins"] = list(dict.fromkeys(existing.get("origins", []) + incoming.get("origins", [])))
    merged["dailyLabels"] = list(
        dict.fromkeys(existing.get("dailyLabels", []) + incoming.get("dailyLabels", []))
    )
    return merged


def build_candidates(daily: dict[str, Any] | None, selected: dict[str, Any] | None) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    if selected is not None:
        for item in validate_selected_response(selected):
            normalized.append(normalize_candidate(item, origin="selected_24h"))
    if daily is not None:
        report = validate_daily_response(daily)
        for section in report["sections"]:
            if not isinstance(section, dict) or not isinstance(section.get("items"), list):
                raise PipelineError("AI HOT 官方日报 section 无效")
            label = clean_optional_text(section.get("label"))
            for item in section["items"]:
                normalized.append(normalize_candidate(item, origin="official_daily", daily_label=label))
        for item in report["flashes"]:
            normalized.append(normalize_candidate(item, origin="official_daily", daily_label="快讯"))

    by_key: dict[str, dict[str, Any]] = {}
    link_to_key: dict[str, str] = {}
    order: list[str] = []
    for candidate in normalized:
        links = [link for link in candidate["links"].values() if link]
        existing_key = next((link_to_key[link] for link in links if link in link_to_key), None)
        if existing_key is None:
            key = candidate["key"]
            by_key[key] = candidate
            order.append(key)
        else:
            key = existing_key
            by_key[key] = merge_candidate(by_key[key], candidate)
        for link in links:
            link_to_key[link] = key
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "items": [by_key[key] for key in order],
    }


def command_fetch_daily(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir).expanduser().resolve()
    daily: dict[str, Any] | None = None
    selected: dict[str, Any] | None = None
    sources: dict[str, Any] = {}

    try:
        daily, sources["officialDaily"] = fetch_official_daily()
        report = validate_daily_response(daily)
        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        if report["date"] != today:
            sources["officialDaily"]["stale"] = True
            sources["officialDaily"]["reportDate"] = report["date"]
            daily_for_candidates = None
        else:
            sources["officialDaily"]["stale"] = False
            daily_for_candidates = daily
    except PipelineError as exc:
        sources["officialDaily"] = {"ok": False, "error": str(exc)}
        daily_for_candidates = None

    try:
        selected_payload, selected_meta = fetch_json(AIHOT_SELECTED_URL)
        validate_selected_response(selected_payload)
        selected = selected_payload
        sources["selected24h"] = {"ok": True, **selected_meta}
    except PipelineError as exc:
        sources["selected24h"] = {"ok": False, "error": str(exc)}

    if daily_for_candidates is None and selected is None:
        raise PipelineError("AI HOT 官方日报与 24 小时精选均不可用")

    candidates = build_candidates(daily_for_candidates, selected)
    if not candidates["items"]:
        raise PipelineError("AI HOT 双源没有可用候选")
    atomic_write_json(out_dir / "daily.json", daily or {"schemaVersion": 1, "report": None})
    atomic_write_json(out_dir / "selected.json", selected or {"schemaVersion": 1, "items": []})
    atomic_write_json(out_dir / "candidates.json", candidates)
    atomic_write_json(out_dir / "fetch-meta.json", {
        "fetchedAt": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "sources": sources,
        "candidateCount": len(candidates["items"]),
    })
    print(json.dumps({"ok": True, "count": len(candidates["items"]), "outDir": str(out_dir)}, ensure_ascii=False))


def ensure_text(value: Any, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise PipelineError(f"{field} 必须是字符串")
    text = " ".join(value.strip().split())
    if not minimum <= len(text) <= maximum:
        raise PipelineError(f"{field} 长度必须在 {minimum}–{maximum} 字符之间")
    return text


def aihot_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise PipelineError("来源 JSON 缺少 items 数组")
    items = []
    for item in payload["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise PipelineError("来源 items 含无效条目")
        link = item.get("links", {}).get("aihot") if isinstance(item.get("links"), dict) else None
        if not isinstance(link, str) or not link.startswith("https://aihot.virxact.com/"):
            raise PipelineError(f"条目 {item.get('id')} 缺少有效 AI HOT 链接")
        items.append(item)
    return items


def daily_candidates(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise PipelineError("候选 JSON 不符合 v1 合同")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise PipelineError("候选 JSON 缺少 items 数组")
    items = []
    for item in raw_items:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise PipelineError("候选 items 含无效条目")
        ensure_text(item.get("title"), f"{item.get('key')}.title", 1, 180)
        ensure_text(item.get("source", {}).get("name"), f"{item.get('key')}.source.name", 1, 120)
        link = item.get("displayLink")
        if not isinstance(link, str) or not link.startswith(("https://", "http://")):
            raise PipelineError(f"候选 {item.get('key')} 缺少有效展示链接")
        items.append(item)
    return items


def parse_alert_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PipelineError(f"热点缺少 {field}")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise PipelineError(f"{field} 不是有效 ISO 时间") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(ZoneInfo("UTC"))


def validate_alert_timeline(item: dict[str, Any], why_now: str) -> tuple[str, datetime]:
    alert_reason = item.get("alertReason")
    if alert_reason not in {"new_event", "material_update"}:
        raise PipelineError("热点缺少有效 alertReason；无法区分新事件与既有事件更新")
    first_report = parse_alert_timestamp(item.get("firstReportAt"), "firstReportAt")
    if alert_reason == "material_update":
        if any(phrase in why_now for phrase in ALERT_MATERIAL_UPDATE_PROHIBITED_PHRASES):
            raise PipelineError("既有事件的新进展不得写成刚发布")
        return alert_reason, first_report

    age_hours = (datetime.now(ZoneInfo("UTC")) - first_report).total_seconds() / 3600
    if age_hours < -ALERT_MAX_FUTURE_SKEW_HOURS or age_hours > ALERT_MAX_NEW_EVENT_AGE_HOURS:
        raise PipelineError("新事件已超过即时热点时效门槛")
    return alert_reason, first_report


def note(source_name: str, link: str) -> dict[str, Any]:
    return {
        "tag": "note",
        "elements": [{"tag": "lark_md", "content": f"来源：[{source_name}]({link})"}],
    }


def render_daily_card(source: Any, decision: Any) -> dict[str, Any]:
    items = {item["key"]: item for item in daily_candidates(source)}
    if not isinstance(decision, dict):
        raise PipelineError("decision 必须是对象")
    date = ensure_text(decision.get("date"), "date", 10, 10)
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise PipelineError("date 必须是 YYYY-MM-DD") from exc
    selected = decision.get("items")
    if not isinstance(selected, list) or not 1 <= len(selected) <= 7:
        raise PipelineError("日报 items 必须为 1–7 条")
    lead_value = decision.get("lead")
    lead = None if lead_value in (None, "") else ensure_text(lead_value, "lead", 12, 100)
    seen: set[str] = set()
    grouped: dict[str, list[tuple[dict[str, Any], str, str]]] = {key: [] for key in SECTIONS}
    for index, choice in enumerate(selected):
        if not isinstance(choice, dict):
            raise PipelineError(f"items[{index}] 必须是对象")
        item_key = ensure_text(choice.get("key"), f"items[{index}].key", 1, 200)
        if item_key in seen:
            raise PipelineError(f"重复条目：{item_key}")
        if item_key not in items:
            raise PipelineError(f"条目不在本次 AI HOT 候选中：{item_key}")
        section = choice.get("section")
        if section not in SECTIONS:
            raise PipelineError(f"无效 section：{section}")
        topic = normalize_topic(choice.get("topic"), f"items[{index}].topic")
        insight = ensure_text(choice.get("insight"), f"items[{index}].insight", 12, 100)
        grouped[section].append((items[item_key], topic, insight))
        seen.add(item_key)

    elements: list[dict[str, Any]] = [{
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"今天筛出 {len(selected)} 条值得看的 AI 信息，按信息增量筛选。",
        },
    }]
    if lead:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**今日主线**\n{lead}"},
        })
    number = 1
    emitted_section = False
    for section_key, section_title in SECTIONS.items():
        entries = grouped[section_key]
        if not entries:
            continue
        if emitted_section:
            elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**{section_title}**"}})
        for item, topic, insight in entries:
            title = ensure_text(item.get("title"), f"{item['key']}.title", 1, 180)
            source_name = ensure_text(item.get("source", {}).get("name"), f"{item['key']}.source.name", 1, 120)
            link = item["displayLink"]
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{number:02d}｜[{topic}] {title}**\n值得看：{insight}",
                },
            })
            elements.append(note(source_name, link))
            number += 1
        emitted_section = True

    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "turquoise", "title": {"tag": "plain_text", "content": f"AIGC 日报｜{date}"}},
        "elements": elements,
    }


def render_alert_card(source: Any, decision: Any) -> dict[str, Any]:
    items = {item["id"]: item for item in aihot_items(source)}
    if not isinstance(decision, dict):
        raise PipelineError("decision 必须是对象")
    item_id = ensure_text(decision.get("id"), "id", 1, 200)
    if item_id not in items:
        raise PipelineError(f"热点不在本次候选中：{item_id}")
    item = items[item_id]
    source_count = item.get("sourceCount")
    signal_count = item.get("signalCount")
    if not isinstance(source_count, int) or not isinstance(signal_count, int):
        raise PipelineError("热点缺少 sourceCount/signalCount")
    if not (source_count >= 5 or (source_count >= 3 and signal_count >= 8)):
        raise PipelineError("热点证据未达到即时推送门槛")
    why_now = ensure_text(decision.get("why_now"), "why_now", 12, 120)
    impact = ensure_text(decision.get("impact"), "impact", 12, 120)
    alert_reason, first_report = validate_alert_timeline(item, why_now)
    title = ensure_text(item.get("title"), "title", 1, 180)
    source_name = ensure_text(item.get("source", {}).get("name"), "source.name", 1, 120)
    link = item["links"]["aihot"]
    latest_at = ensure_text(item.get("latestAt"), "latestAt", 10, 80)
    try:
        latest = datetime.fromisoformat(latest_at.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Shanghai"))
        latest_display = latest.strftime("%m-%d %H:%M")
        first_report_display = first_report.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M")
    except ValueError as exc:
        raise PipelineError("latestAt 不是有效 ISO 时间") from exc

    intro = f"**{title}**\n"
    if alert_reason == "material_update":
        intro += "这是既有事件的新进展。\n"
    intro += why_now

    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "orange", "title": {"tag": "plain_text", "content": "AIGC 重大热点快报"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": intro}},
            {"tag": "div", "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**独立来源**\n{source_count}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**热度信号**\n{signal_count}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**事件首报**\n{first_report_display}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**最新信号**\n{latest_display}"}},
            ]},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**对内容生产的影响**\n{impact}"}},
            note(source_name, link),
        ],
    }


def validate_card(card: Any) -> None:
    if not isinstance(card, dict) or set(card) != {"config", "header", "elements"}:
        raise PipelineError("卡片根节点必须严格为 config/header/elements")
    if "schema" in card or "body" in card:
        raise PipelineError("Card 1.0 禁止 schema/body")
    if not isinstance(card.get("elements"), list) or not card["elements"]:
        raise PipelineError("卡片 elements 不能为空")
    header = card.get("header")
    if not isinstance(header, dict) or header.get("template") not in {"turquoise", "orange"}:
        raise PipelineError("卡片 header.template 无效")
    serialized = json.dumps(card, ensure_ascii=False)
    if "api/public" in serialized:
        raise PipelineError("卡片包含废弃 AI HOT API")
    for token in ("系统日志",):
        if token in serialized:
            raise PipelineError(f"卡片包含禁止文案：{token}")
    if header.get("template") == "turquoise":
        for token in DAILY_PROHIBITED_PHRASES:
            if token in serialized:
                raise PipelineError(f"日报卡片包含压力型文案：{token}")


def command_render(args: argparse.Namespace, mode: str) -> None:
    source = read_json(Path(args.source).expanduser().resolve())
    decision = read_json(Path(args.decision).expanduser().resolve())
    card = render_daily_card(source, decision) if mode == "daily" else render_alert_card(source, decision)
    validate_card(card)
    output = Path(args.output).expanduser().resolve()
    atomic_write_json(output, card)
    print(json.dumps({"ok": True, "output": str(output)}, ensure_ascii=False))


def lark_env() -> dict[str, str]:
    env = os.environ.copy()
    env["OPENCLAW_HOME"] = OPENCLAW_HOME
    return env


def run_json_command(argv: list[str], *, env: dict[str, str]) -> dict[str, Any]:
    try:
        result = subprocess.run(argv, env=env, text=True, capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as exc:
        raise PipelineError("lark-cli 调用超过 60 秒，未取得成功回执") from exc
    if result.returncode != 0:
        useful = (result.stderr or result.stdout).strip()[-1200:]
        raise PipelineError(f"命令失败 ({result.returncode})：{useful}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"命令未返回 JSON：{result.stdout[-500:]}") from exc


def verify_identity(env: dict[str, str]) -> dict[str, Any]:
    status = run_json_command(["lark-cli", "auth", "status", "--json", "--verify"], env=env)
    bot = status.get("identities", {}).get("bot", {})
    if status.get("appId") != EXPECTED_APP_ID or bot.get("appName") != EXPECTED_APP_NAME:
        raise PipelineError("lark-cli 当前不是章北海 bot 工作区")
    if status.get("identity") != "bot" or bot.get("verified") is not True:
        raise PipelineError("章北海 bot 身份未通过验证")
    return status


def command_send(args: argparse.Namespace) -> None:
    if not args.send:
        return _command_send(args)
    if not args.log:
        raise PipelineError("真实发送必须指定 --log")
    lock_path = Path(args.log).expanduser().resolve().with_suffix('.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _command_send(args)


def _command_send(args: argparse.Namespace) -> None:
    card_path = Path(args.card).expanduser().resolve()
    card = read_json(card_path)
    validate_card(card)
    is_daily = card["header"]["template"] == "turquoise"
    expected_chat_id = DAILY_CHAT_ID if is_daily else EXPECTED_CHAT_ID
    if args.chat_id != expected_chat_id:
        raise PipelineError("目标群不符合当前日报或热点的授权目标")
    compact = json.dumps(card, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(compact.encode("utf-8")).hexdigest()[:12]
    if is_daily:
        title = card["header"].get("title", {}).get("content", "")
        day = title.removeprefix("AIGC 日报｜") if title.startswith("AIGC 日报｜") else ""
        try:
            if datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d") != day:
                raise ValueError("noncanonical date")
        except ValueError as exc:
            raise PipelineError("日报标题必须包含有效的 YYYY-MM-DD 日期") from exc
        idempotency_key = f"aigc-daily-{day}-team"
    else:
        idempotency_key = f"aigc-alert-{digest}"
    event_id = getattr(args, "event_id", None)
    event_log = None
    if event_id:
        if is_daily or not re.fullmatch(r"[A-Za-z0-9-]{1,100}", event_id):
            raise PipelineError("热点 event-id 必须为有效的 canonical story ID")
        idempotency_key = f"aigc-alert-{event_id}-team"
        event_log = Path(OPENCLAW_HOME) / "workspace/artifacts/aigc-alert/delivered-events" / f"{event_id}.json"
        if event_log.exists():
            previous_event = read_json(event_log)
            if previous_event.get("message_id"):
                print(json.dumps({"skipped": True, "reason": "event_already_delivered", "message_id": previous_event["message_id"]}))
                return
    if args.send and not args.log:
        raise PipelineError("真实发送必须指定 --log 保存回执")
    log_path = Path(args.log).expanduser().resolve() if args.log else None
    if log_path and log_path.exists():
        previous = read_json(log_path)
        if (
            previous.get("sent") is True
            and previous.get("chatId") == expected_chat_id
            and previous.get("idempotencyKey") == idempotency_key
            and previous.get("result", {}).get("ok") is True
            and previous.get("result", {}).get("data", {}).get("message_id")
        ):
            print(json.dumps({**previous, "skipped": True}, ensure_ascii=False))
            return
    if log_path and log_path.exists():
        previous = read_json(log_path)
        if previous.get("sendAttempted") and not previous.get("sent"):
            raise PipelineError("上次发送结果未知，保留原记录；核对群消息后才能恢复，禁止自动重发")
    base = [
        "lark-cli", "im", "+messages-send", "--as", "bot", "--chat-id", args.chat_id,
        "--msg-type", "interactive", "--content", compact, "--idempotency-key", idempotency_key,
    ]
    log_data: dict[str, Any] = {
        "chatId": args.chat_id,
        "idempotencyKey": idempotency_key,
        "sent": False,
    }
    try:
        env = lark_env()
        identity = verify_identity(env)
        log_data["identity"] = {"appId": identity.get("appId"), "appName": EXPECTED_APP_NAME, "as": "bot"}
        dry_run = run_json_command(base + ["--dry-run"], env=env)
        log_data["dryRun"] = dry_run
        if dry_run.get("ok") is not True or dry_run.get("dry_run") is not True:
            raise PipelineError("飞书 dry-run 没有返回成功凭据")
        if args.send:
            log_data["sendAttempted"] = True
            atomic_write_json(log_path, log_data)
            sent = run_json_command(base, env=env)
            log_data["result"] = sent
            if sent.get("ok") is not True or not sent.get("data", {}).get("message_id"):
                raise PipelineError("飞书发送结果缺少成功标志或 message_id")
            log_data["sent"] = True
            if event_log:
                atomic_write_json(event_log, {
                    "canonicalEventId": event_id, "message_id": sent["data"]["message_id"],
                    "chatId": args.chat_id, "idempotencyKey": idempotency_key,
                    "sentAt": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                })
    except PipelineError as exc:
        log_data["error"] = str(exc)
        if log_path and args.send:
            atomic_write_json(log_path, log_data)
        raise
    if log_path and args.send:
        atomic_write_json(log_path, log_data)
    print(json.dumps(log_data, ensure_ascii=False))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch-daily")
    fetch.add_argument("--out-dir", required=True)

    for name in ("render-daily", "render-alert"):
        render = sub.add_parser(name)
        render.add_argument("--source", required=True)
        render.add_argument("--decision", required=True)
        render.add_argument("--output", required=True)

    send = sub.add_parser("send")
    send.add_argument("--card", required=True)
    send.add_argument("--chat-id", required=True)
    send.add_argument("--log")
    send.add_argument("--event-id", help="canonical story ID for scheduled alert deduplication")
    send.add_argument("--send", action="store_true", help="perform one real send after dry-run")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "fetch-daily":
            command_fetch_daily(args)
        elif args.command == "render-daily":
            command_render(args, "daily")
        elif args.command == "render-alert":
            command_render(args, "alert")
        elif args.command == "send":
            command_send(args)
        return 0
    except PipelineError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
