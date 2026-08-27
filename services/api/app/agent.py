"""Irrigation advisory agent: RAG over a cloud knowledge base + live telemetry synthesis.

Retrieval is keyword-based (Chinese-friendly via substring + 2-gram tokenization) so it
runs without an embedding model or external service. The answer is composed from the
top-ranked knowledge base docs combined with the current device's live state.

Optional LLM upgrade: if LLM_API_KEY, LLM_BASE_URL and LLM_MODEL environment variables
are set, retrieved context + live state are sent to an OpenAI-compatible chat
completions endpoint for a richer natural-language answer. Without those variables the
local synthesizer is used (always works, no external dependency).
"""
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

LOGGER = logging.getLogger("smart-agriculture-api")

DEFAULT_KB_PATH = Path(__file__).resolve().parent / "knowledge_base.json"
KB_PATH = os.getenv("KNOWLEDGE_BASE_PATH", str(DEFAULT_KB_PATH))

# Optional LLM upgrade: "luna" mode uses the user's Luna model (OpenAI-compatible).
# Thinking effort is FIXED at medium and not exposed to end users.
LUNA_API_KEY = os.getenv("LUNA_API_KEY")
LUNA_BASE_URL = os.getenv("LUNA_BASE_URL", "https://wolfai.top/v1")
LUNA_MODEL = os.getenv("LUNA_MODEL", "gpt-5.6-luna")
LUNA_REASONING_EFFORT = os.getenv("LUNA_REASONING_EFFORT", "medium")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))  # medium reasoning can take 20-45s

# CJK Unified Ideographs basic block
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_PUNCT_RE = re.compile(r"[\s,，。.!?;；:：、/\\\\()()\\[\\]【】\"'\"\"'']+")
_TOKEN_SPLIT_RE = re.compile(r"[\\s,，。.!?;；:：、/\\\\()()\\[\\]【】]+")

_KB_CACHE = {"loaded_at": 0.0, "docs": []}


def load_knowledge_base(force: bool = False):
    """Load and cache the knowledge base from JSON. Cached for 5 minutes."""
    if not force and _KB_CACHE["docs"] and (time.time() - _KB_CACHE["loaded_at"]) < 300:
        return _KB_CACHE["docs"]
    try:
        with open(KB_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        docs = data.get("docs", [])
        _KB_CACHE["docs"] = docs
        _KB_CACHE["loaded_at"] = time.time()
        LOGGER.info("loaded knowledge base: %d docs from %s", len(docs), KB_PATH)
        return docs
    except Exception as exc:
        LOGGER.warning("failed to load knowledge base %s: %s", KB_PATH, exc)
        return _KB_CACHE["docs"] or []


def tokenize(text: str):
    """Tokenize text into a set of terms, with Chinese 2-gram expansion for retrieval.

    Splits on whitespace and punctuation, lowercases, then for CJK adds all 2-character
    sliding windows so partial matches still hit curated keywords.
    """
    if not text:
        return set()
    cleaned = _PUNCT_RE.sub(" ", text.lower())
    parts = [p for p in _TOKEN_SPLIT_RE.split(cleaned) if p]
    terms = set()
    for part in parts:
        terms.add(part)
        if _CJK_RE.search(part):
            for i in range(len(part) - 1):
                bigram = part[i:i + 2]
                if _CJK_RE.search(bigram):
                    terms.add(bigram)
    return terms


def retrieve(query: str, docs, top_k: int = 3):
    """Score each doc by keyword + tag overlap with the query; return top-k."""
    if not docs:
        return []
    terms = tokenize(query)
    if not terms:
        return []
    query_lower = query.lower()
    scored = []
    for doc in docs:
        score = 0
        for keyword in doc.get("keywords", []):
            kw = keyword.lower()
            if kw in terms:
                score += 3
            elif kw and kw in query_lower:
                score += 2
        for tag in doc.get("tags", []):
            if tag.lower() in terms or tag.lower() in query_lower:
                score += 1
        title_tokens = tokenize(doc.get("title", ""))
        if title_tokens & terms:
            score += 1
        if score > 0:
            scored.append((doc, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]


def _latest_snapshot(registry, device_id):
    if not device_id or not registry:
        return None
    return registry.get(device_id)


def _trend_hint(samples):
    """Return a short trend label from a chronological list of moisture values."""
    if not samples or len(samples) < 2:
        return "样本不足"
    last = samples[-1]
    prev = samples[0]
    if last is None or prev is None or prev == 0:
        return "样本不足"
    delta = (last - prev) / max(abs(prev), 1) * 100
    if delta > 3:
        return f"近段时间上升约 {delta:.1f}%"
    if delta < -3:
        return f"近段时间下降约 {abs(delta):.1f}%"
    return f"近段时间基本平稳（变化 {delta:+.1f}%）"


def collect_live_context(device_id=None, history=None, irrigation_rules=None, registry=None):
    """Build a small live-state context dict used in the synthesized answer.

    `history` and `irrigation_rules` and `registry` are injected by the caller to keep
    this module decoupled from main.py globals (and trivially testable).
    """
    registry = registry or {}
    history = history or {}
    irrigation_rules = irrigation_rules or {}
    device = _latest_snapshot(registry, device_id) if device_id else None
    soil = (device.get("telemetry", {}).get("soil", {}).get("payload", {}) if device else {}) or {}
    climate = (device.get("telemetry", {}).get("climate", {}).get("payload", {}) if device else {}) or {}
    moisture = soil.get("moisture_pct")
    air_temp = climate.get("air_temperature_c")
    light = climate.get("light_lux")
    pump = (device.get("pump") or {}) if device else {}
    rule = None
    if device_id and irrigation_rules:
        rule_data = irrigation_rules.get(device_id, {})
        rule = {
            "auto_enabled": bool(rule_data.get("auto_enabled", False)),
            "start_threshold_pct": rule_data.get("start_threshold_pct"),
            "stop_threshold_pct": rule_data.get("stop_threshold_pct"),
        }
    moisture_series = []
    for item in (history.get(device_id, []) if history else []):
        if item.get("kind") == "soil":
            payload = item.get("payload", {}) or {}
            moisture_series.append(payload.get("moisture_pct"))
    moisture_series = [v for v in moisture_series if isinstance(v, (int, float))]
    return {
        "device_id": device_id,
        "moisture_pct": moisture,
        "air_temperature_c": air_temp,
        "light_lux": light,
        "pump_running": bool(pump.get("running")),
        "pump_status": pump.get("status"),
        "rule": rule,
        "moisture_trend": _trend_hint(moisture_series[-10:]),
    }


def _live_recommendation(ctx):
    """Produce a concrete recommendation string grounded in current live numbers."""
    moisture = ctx.get("moisture_pct")
    air_temp = ctx.get("air_temperature_c")
    pump_running = ctx.get("pump_running")
    rule = ctx.get("rule") or {}
    start = rule.get("start_threshold_pct")
    stop = rule.get("stop_threshold_pct")
    auto = rule.get("auto_enabled")

    tips = []
    if moisture is not None:
        if moisture < 40:
            tips.append(f"当前土壤湿度 {moisture:.1f}%，低于 40% 警戒线，建议立即灌溉")
        elif moisture < 55:
            tips.append(f"当前土壤湿度 {moisture:.1f}%，处于略偏低区间，可考虑补水")
        elif moisture > 70:
            tips.append(f"当前土壤湿度 {moisture:.1f}%，偏高，注意排水通风，避免烂根")
        else:
            tips.append(f"当前土壤湿度 {moisture:.1f}%，处于健康区间")
    if air_temp is not None and air_temp > 30:
        tips.append(f"空气温度 {air_temp:.1f}°C 偏高，建议加强通风并考虑遮阳")
    if pump_running:
        tips.append("水泵当前运行中")
    if auto and start is not None and stop is not None:
        tips.append(f"自动规则已启用：低于 {start}% 启动、≥ {stop}% 停止")
    elif auto is False:
        tips.append("自动灌溉未启用，当前为手动模式")
    trend = ctx.get("moisture_trend")
    if trend and "样本不足" not in trend:
        tips.append(f"湿度趋势：{trend}")
    return tips


# --- synthesis -------------------------------------------------------------
_TOPIC_OPENERS = {
    "低湿度": "您关心的是低湿度情况下的灌溉处理。",
    "高湿度": "您提到了湿度偏高的问题。",
    "灌溉时间": "您询问的是灌溉时间的选择。",
    "高温": "您提到了高温下的灌溉注意事项。",
    "自动规则": "您关心的是自动灌溉规则的配置。",
    "pH EC": "您询问的是灌溉水 pH 与电导率管理。",
    "作物": "您想了解的是不同作物的灌溉阈值。",
    "养分": "您关心的是施肥与灌溉的协同管理。",
    "节水": "您希望了解节水灌溉的最佳实践。",
    "设备": "您关心的是灌溉设备的维护与故障处理。",
    "育苗": "您询问的是育苗期的水分管理。",
    "土壤": "您关心的是土壤类型与保水特性。",
    "地膜": "您想了解地膜覆盖的作用与用法。",
    "滴灌": "您询问的是滴灌系统的设计与使用。",
    "通风": "您关心的是温室通风管理。",
    "遮阳": "您想了解遮阳网的使用技巧。",
    "病虫害": "您关心的是病虫害与灌溉的关系。",
    "根系": "您询问的是根系健康管理。",
    "盐碱": "您提到了土壤盐碱化问题。",
    "水质": "您关心的是灌溉水的过滤与水质管理。",
    "水肥": "您想了解水肥一体化（灌溉施肥）的做法。",
    "果实膨大期": "您询问的是果实膨大期的管理要点。",
    "开花期": "您关心的是开花期的水分管理。",
    "苗期": "您想了解苗期水分管理原则。",
    "采收前": "您询问的是采收前的灌溉管理。",
    "冬季": "您关心的是冬季灌溉注意事项。",
    "排水": "您想了解雨季排水防涝措施。",
    "干旱": "您提到了干旱缺水的应对策略。",
    "传感器": "您询问的是土壤湿度传感器的校准。",
    "水泵": "您关心的是水泵选型与节能。",
    "管道": "您想了解管道维护与冬季防冻。",
    "滴头堵塞": "您询问的是滴头堵塞的防治。",
    "板结": "您关心的是土壤板结的改良。",
    "有机肥": "您想了解有机肥的施用要点。",
    "堆肥": "您询问的是堆肥的制作方法。",
    "叶面肥": "您关心的是叶面肥的使用技巧。",
    "土壤检测": "您想了解土壤检测与配方施肥。",
    "轮作": "您询问的是轮作与茬口安排。",
    "间作": "您想了解间作套种的做法。",
    "温室温控": "您关心的是温室温度调控。",
    "二氧化碳": "您询问的是二氧化碳施肥（气肥）。",
    "光照": "您想了解光照管理。",
    "昼夜温差": "您关心的是昼夜温差管理。",
    "空气湿度": "您询问的是空气湿度管理。",
    "防冻": "您想了解防冻保温措施。",
    "防涝": "您关心的是防涝排水措施。",
    "生物防治": "您询问的是生物防治方法。",
    "灌溉量": "您想了解灌溉量的计算方法。",
    "蒸散": "您关心的是蒸散量与补水时机。",
    "智能灌溉": "您询问的是智能灌溉系统的使用。",
    "数据异常": "您关心的是遥测数据异常的排查。",
    "设备离线": "您想了解设备离线如何排查。",
    "告警": "您询问的是告警处理流程。",
}


def _summarize_content(content):
    """Extract the first two non-empty lines of a doc's content for a compact quote."""
    lines = [line.strip() for line in (content or "").splitlines() if line.strip()]
    return " ".join(lines[:2])


def synthesize_answer(query, retrieved, ctx, history=None):
    """Compose a natural-language advisory answer from retrieved docs + live state."""
    history = history or []
    tips = _live_recommendation(ctx)
    topic_open = ""
    primary = retrieved[0][0] if retrieved else None
    if primary:
        topic_open = _TOPIC_OPENERS.get(primary.get("topic", ""), "我已根据您的问题检索了温室灌溉知识库。")
    else:
        topic_open = "我已在知识库中检索了相关信息。"

    parts = [topic_open]
    if primary:
        quote = _summarize_content(primary.get("content", ""))
        if quote:
            parts.append(f"参考建议：{quote}")
    if len(retrieved) > 1:
        extras = []
        for doc, score in retrieved[1:]:
            summary = _summarize_content(doc.get("content", ""))
            if summary:
                extras.append(f"· {doc.get('title', '')}：{summary}")
        if extras:
            parts.append("其他可参考要点：\n" + "\n".join(extras[:2]))
    if tips:
        parts.append("结合您当前的温室数据：\n· " + "\n· ".join(tips))
    if history:
        last_q = history[-1].get("question", "")
        if last_q and last_q != query:
            parts.append(f"（您上一轮问的是「{last_q}」，本轮答案已结合最新遥测。）")
    closing = ""
    if primary and primary.get("topic") in {"低湿度", "高温"}:
        closing = "建议优先处理上述建议，再观察 1-2 小时确认效果。"
    elif primary and primary.get("topic") == "自动规则":
        closing = "修改规则后请观察 1 个灌溉周期确认行为符合预期。"
    elif primary and primary.get("topic") == "设备":
        closing = "如按上述排查仍未恢复，请联系维护人员进一步检查。"
    elif tips and any("立即灌溉" in t or "加强通风" in t for t in tips):
        closing = "建议尽快执行上述操作，并通过看板的灌溉控制面板下达指令。"
    else:
        closing = "如需更具体的操作（如调整阈值、切换自动/手动模式），请告诉我。"
    if closing:
        parts.append(closing)

    sources = [
        {"id": doc.get("id"), "title": doc.get("title"), "topic": doc.get("topic"), "score": score}
        for doc, score in retrieved
    ]
    return {
        "answer": "\n\n".join(parts),
        "sources": sources,
        "context": {
            "moisture_pct": ctx.get("moisture_pct"),
            "air_temperature_c": ctx.get("air_temperature_c"),
            "rule": ctx.get("rule"),
            "pump_running": ctx.get("pump_running"),
        },
    }


# --- optional LLM upgrade (OpenAI-compatible) ------------------------------
def _call_llm(prompt_messages, base_url=None, api_key=None, model=None, reasoning_effort=None):
    """Call an OpenAI-compatible chat completions endpoint.

    Returns {"content": str, "reasoning": str|None} or None on failure.
    reasoning_effort=None -> thinking off (param omitted); the model's
    chain-of-thought (reasoning_content) is captured when present.
    """
    api_key = api_key or LUNA_API_KEY
    base_url = base_url or LUNA_BASE_URL
    model = model or LUNA_MODEL
    if not api_key:
        return None
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": prompt_messages,
        "temperature": 0.3,
        "max_tokens": 900,
    }
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
        message = data.get("choices", [{}])[0].get("message", {})
        content = (message.get("content") or "").strip() or None
        reasoning = (message.get("reasoning_content") or "").strip() or None
        if not content:
            return None
        return {"content": content, "reasoning": reasoning}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        LOGGER.warning("LLM call failed: %s", exc)
        return None


def _build_llm_messages(question, retrieved, ctx, history=None):
    history = history or []
    # Luna is the owner's catgirl maid: warm, with verbal tics, but the advice
    # must stay professional and grounded in the KB + live telemetry.
    persona = os.getenv(
        "LUNA_PERSONA",
        "你是温室灌溉顾问 Luna，是主人的猫儿女仆。语气亲切温柔，像照顾主人的贴心女仆，"
        "句尾常带口癖（如\"喵~\"\"呐\"\"主人\"），但给出的农事建议必须专业准确。",
    )
    system = (
        f"{persona} 基于【知识库片段】和【实时遥测】用中文回答农户问题，"
        "给出可执行建议并引用知识来源。回答 2-4 段，先自然回应主人的问题再给建议，避免堆砌术语。"
    )
    retrieved_block = "\n\n".join(
        f"《{doc.get('title')}》\n{doc.get('content', '')}" for doc, _ in retrieved
    )
    live_block = json.dumps(ctx, ensure_ascii=False)
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"问题：{question}\n\n实时遥测：{live_block}\n\n知识库片段：\n{retrieved_block}"
            ),
        },
    ]
    for turn in history[-3:]:
        messages.append({"role": "user", "content": turn.get("question", "")})
    return messages


def answer_question(question, history=None, device_id=None, *, registry=None, history_rows=None, irrigation_rules=None, mode="kb", reasoning=False, reasoning_effort="medium"):
    """Top-level entry:
    - mode="kb":   knowledge-base synthesizer (always available)
    - mode="luna": Luna model via OpenAI-compatible API (requires LUNA_API_KEY;
                   falls back to the synthesizer when the call fails).
    reasoning (bool) toggles thinking; reasoning_effort in {"low","medium"} picks
    the chain-of-thought depth. The model's reasoning_content is returned as
    "reasoning" so the UI can display it."""
    docs = load_knowledge_base()
    retrieved = retrieve(question, docs, top_k=3)
    ctx = collect_live_context(
        device_id=device_id,
        history=history_rows,
        irrigation_rules=irrigation_rules,
        registry=registry,
    )
    base = synthesize_answer(question, retrieved, ctx, history=history)
    base["answer_via"] = "synthesizer"
    base["reasoning"] = None
    base["reasoning_effort"] = None

    if mode == "luna" and retrieved and LUNA_API_KEY:
        messages = _build_llm_messages(question, retrieved, ctx, history=history)
        effort = reasoning_effort if reasoning else None
        luna_result = _call_llm(
            messages,
            base_url=LUNA_BASE_URL,
            api_key=LUNA_API_KEY,
            model=LUNA_MODEL,
            reasoning_effort=effort,
        )
        if luna_result:
            base["answer"] = luna_result["content"]
            base["answer_via"] = "luna"
            base["reasoning"] = luna_result.get("reasoning")
            base["reasoning_effort"] = effort

    base["retrieved_count"] = len(retrieved)
    return base
