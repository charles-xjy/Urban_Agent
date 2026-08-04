"""
Urban_Agent LangGraph 服务端入口

供 `langgraph dev` 使用，不依赖交互式 CLI。
通过环境变量配置模型，跳过交互式扫描/确认。

环境变量（在 ../.env 中配置）：
  VLLM_MAIN_PORT   主模型端口（留空则取第一个可用端口）
  VLLM_CLAIM_PORT  视觉模型端口（留空则与主模型相同）

启动：
  cd frontend
  langgraph dev
"""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Annotated
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.constants import Send, TAG_NOSTREAM
from langgraph.graph import END, START, StateGraph
from langgraph.config import get_stream_writer
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from typing_extensions import TypedDict

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ── sys.path 设置 ─────────────────────────────────────────────────────────────
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import config.settings as settings  # noqa: E402
from core.intent import (  # noqa: E402
    invalid_input_clarification,
    is_explicit_analysis_request,
    is_obviously_invalid_input,
    latest_human_text,
    normalize_router_type,
)
from core.source_merge import (  # noqa: E402
    enrich_report_sources,
    has_source_section,
    merge_findings_with_sources,
    replace_report_sources,
)
from core.state_reducers import FINDINGS_RESET, findings_reducer  # noqa: E402

logger = logging.getLogger(__name__)


# ── 1. 非交互式模型配置 ───────────────────────────────────────────────────────

def _auto_configure_models() -> None:
    """扫描端口，根据环境变量自动分配模型，无需用户交互。"""
    available = settings.scan_models()
    if not available:
        raise RuntimeError(
            "[frontend] 端口 8001-8003 均无可用模型，请确认 vLLM 服务已启动"
        )

    preferred_main = os.environ.get("VLLM_MAIN_PORT", "").strip()
    preferred_claim = os.environ.get("VLLM_CLAIM_PORT", "").strip()

    # 分配主模型
    if preferred_main and int(preferred_main) in available:
        agent_info = available[int(preferred_main)]
    else:
        agent_info = next(iter(available.values()))

    # 分配视觉模型
    if preferred_claim and int(preferred_claim) in available:
        claim_info = available[int(preferred_claim)]
    else:
        claim_info = agent_info

    settings.apply_model_config({
        "agent": agent_info,
        "claim": claim_info,
    })
    print(f"[frontend] 主模型: {agent_info['url']} → {agent_info['model']}")
    print(f"[frontend] 视觉模型: {claim_info['url']} → {claim_info['model']}")


_auto_configure_models()


# ── 2. WebState 定义 ──────────────────────────────────────────────────────────

class WebState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    # 城市分析字段
    user_input: str
    location: str
    start_year: int
    end_year: int
    route: str  # "analysis" | "chat" | "clarify"
    intent_route: str  # 对齐参考：action | chat | clarify，便于路由与调试
    plan: list[dict]
    plan_approved: bool
    findings: Annotated[list[str], findings_reducer]  # 支持跨轮 reset，见 core/state_reducers.py
    report: str


# ── 3. LLM 工厂 ──────────────────────────────────────────────────────────────

def _llm(temperature: float = 0, max_tokens: int = 512) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.AGENT_MODEL_URL,
        api_key=settings.VLLM_API_KEY,
        model=settings.AGENT_MODEL_NAME,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ── 4. Router 节点 ────────────────────────────────────────────────────────────

_ROUTER_SYSTEM = f"""\
你是城市治理智能体的意图路由器。当前年份：{datetime.now().year}。

判断用户最新一条消息属于哪一类：
- action：明确的城市/地区分析任务（含地点 + 分析/变化/发展等意图）
- chat：问候、闲聊、询问你是谁/能做什么，或无需调用工具的普通问题
- clarify：输入无意义，或看起来想执行任务但关键信息（如地点、时间）不足

只输出严格 JSON：
{{"type":"action"}}
{{"type":"chat","reply":"自然、友好的中文回复"}}
{{"type":"clarify","question":"结合用户原话提出简短确认问题"}}

注意：
- 不要复读用户原文
- 1、纯数字、乱码等输入必须输出 clarify
- 分析雄安新区变化、北京最近5年发展 -> action
- 你好、在吗、你能做什么 -> chat
"""


async def router_node(state: WebState) -> dict:
    """
    入口意图路由（规则 + 模型两层）：
    1. 确定性规则拦截空 / 纯数字 / 乱码 -> 直接 clarify，不调模型
    2. 明确时间范围 + 分析意图的请求直接进入 action，避免模型误判
    3. 其余输入取最近 8 条上下文交模型分类 action/chat/clarify
    4. 复读、空回复、JSON 解析失败 -> 兜底
    5. action 时重置上一轮任务状态（findings/plan/report），避免同线程跨轮继承
    """
    messages = state.get("messages", [])
    last_human = latest_human_text(messages)

    # 1. 规则前置：明显无效输入不交给模型
    if is_obviously_invalid_input(last_human):
        return {
            "messages": [AIMessage(content=invalid_input_clarification(last_human))],
            "route": "clarify",
            "intent_route": "clarify",
        }

    # 2. 明确分析请求走确定性快路径，不让 LLM 把清晰输入误判成 clarify。
    if is_explicit_analysis_request(last_human):
        return {
            "route": "analysis",
            "intent_route": "action",
            "user_input": last_human,
            "plan": [],
            "findings": [FINDINGS_RESET],
            "report": "",
        }

    # 3. 模型分类：传最近 8 条，使连续确认能结合上一轮追问理解
    recent = messages[-8:]
    resp = await _llm(max_tokens=256).ainvoke(
        [
            SystemMessage(content=_ROUTER_SYSTEM),
            HumanMessage(content=_format_recent(recent)),
        ],
        config={"tags": [TAG_NOSTREAM]},
    )
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", resp.content).strip()
    try:
        result = json.loads(raw)
    except Exception:
        result = {}

    route_type = normalize_router_type(result.get("type"))

    if route_type == "chat":
        reply = str(result.get("reply", "")).strip()
        if not reply or reply == last_human:
            reply = "你好！我是城市变化研究智能体，可以帮你分析任意城市在指定时间段内的变化，也可以普通聊天。请输入分析请求，例如：雄安新区 2018 到 2024 年的城市变化。"
        return {
            "messages": [AIMessage(content=reply)],
            "route": "chat",
            "intent_route": "chat",
        }

    if route_type == "action":
        # 重置上一轮任务状态，避免同线程连续分析时 findings 跨轮继承
        return {
            "route": "analysis",
            "intent_route": "action",
            "user_input": last_human,
            "plan": [],
            "findings": [FINDINGS_RESET],
            "report": "",
        }

    # clarify
    question = str(result.get("question", "")).strip()
    if not question or question == last_human:
        question = "我还不太确定你的具体需求。你是想普通聊天，还是需要分析某个地区在一段时间内的变化？"
    return {
        "messages": [AIMessage(content=question)],
        "route": "clarify",
        "intent_route": "clarify",
    }


def _format_recent(messages: list) -> str:
    """把最近几条消息压成纯文本给路由模型，避免协议对象差异。"""
    parts = []
    for m in messages:
        if isinstance(m, HumanMessage):
            parts.append(f"用户：{m.content if isinstance(m.content, str) else str(m.content)}")
        elif isinstance(m, AIMessage):
            parts.append(f"助手：{m.content if isinstance(m.content, str) else str(m.content)}")
        else:
            text = latest_human_text([m]) or ""
            if text:
                parts.append(text)
    return "\n".join(parts) or "（无上下文）"


def route_after_router(state: WebState) -> str:
    route = state.get("route", "clarify")
    if route == "analysis":
        return "parse_input"
    return "__end__"


# ── 5. Parse Input 节点 ───────────────────────────────────────────────────────

_PARSE_SYSTEM = f"""\
将用户的城市分析请求解析为结构化参数。当前年份：{datetime.now().year}。

时间解析规则：
- "最近N年" / "近N年" → start_year = 当前年 - N
- "X年到Y年"          → start_year=X, end_year=Y
- 未提及时间          → start_year = 当前年 - 5

输出严格为 JSON：
{{"location": "地点名称", "start_year": 2019, "end_year": 2024}}
"""


async def parse_input_node(state: WebState) -> dict:
    user_input = state["user_input"]
    resp = await _llm(max_tokens=128).ainvoke(
        [
            SystemMessage(content=_PARSE_SYSTEM),
            HumanMessage(content=user_input),
        ],
        config={"tags": [TAG_NOSTREAM]},
    )
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", resp.content).strip()
    try:
        parsed = json.loads(raw)
        location = parsed["location"]
        start_year = int(parsed["start_year"])
        end_year = int(parsed["end_year"])
    except Exception:
        return {
            "messages": [AIMessage(content="无法解析地点或时间范围，请重新输入。例如：雄安新区 2018 到 2024 年的城市变化")],
            "route": "chat",
        }

    if len(location) < 2 or location.isdigit():
        return {
            "messages": [AIMessage(content=f"地点名称无效：'{location}'，请输入具体的城市或地区名称。")],
            "route": "chat",
        }

    return {
        "location": location,
        "start_year": start_year,
        "end_year": end_year,
    }


def route_after_parse(state: WebState) -> str:
    if state.get("route") == "chat":
        return "__end__"
    return "web_planner"


# ── 6. Planner 节点 ───────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """\
你是城市研究规划师。根据用户的分析需求，制定 2-4 个研究子任务。

每个子任务需要：
- topic：简短标题（≤10字）
- description：具体研究内容（≤30字）

输出严格为 JSON 数组：
[
  {"topic": "生态环境变化", "description": "植被覆盖、水体面积、绿地密度变化"},
  {"topic": "建筑建设变化", "description": "住宅、商业楼宇密度与规模变化"}
]

只输出 JSON，不含其他文字。
"""


async def web_planner_node(state: WebState) -> dict:
    context = (
        f"分析地点：{state['location']}\n"
        f"时间范围：{state['start_year']} → {state['end_year']}\n"
        f"用户需求：{state['user_input']}"
    )
    resp = await _llm(max_tokens=512).ainvoke(
        [
            SystemMessage(content=_PLANNER_SYSTEM),
            HumanMessage(content=context),
        ],
        config={"tags": [TAG_NOSTREAM]},
    )
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", resp.content).strip()
    try:
        items = json.loads(raw)
        if isinstance(items, dict):
            items = [items]
    except Exception:
        items = [{"topic": "综合变化分析", "description": "城市整体变化"}]

    plan = [
        {"id": f"task_{i+1}", "topic": item["topic"], "description": item["description"]}
        for i, item in enumerate(items[:4])
    ]

    plan_text = "\n".join(
        f"  Task {t['id'].split('_')[1]}：{t['topic']} — {t['description']}"
        for t in plan
    )
    msg = f"已生成 {len(plan)} 个研究方向：\n{plan_text}"

    return {
        "plan": plan,
        "plan_approved": False,
        "messages": [AIMessage(content=msg)],
    }


# ── 7. Human Approval 节点 ────────────────────────────────────────────────────

_CONFIRM_WORDS = {"确认", "可以", "ok", "okay", "yes", "y", "行", "好", "没问题", "同意", "开始", "执行"}
_MAX_PLAN_TASKS = 8

_PLAN_EDIT_SYSTEM = """\
你是研究计划编辑助手。根据当前研究计划和用户的修改要求，输出修改后的计划。
规则：
- 这是对当前计划的修订，不是新的研究请求。研究地点、时间范围和原始主题不可改变。
- “增加/再加/补充一个维度”表示逐字保留全部现有任务，并在末尾新增任务；不得重写或删除原任务。
- 只有用户明确要求删除、替换或修改的任务才可以变更。
- 例如研究主题是“雄安城市变化”，用户说“再加一个学校层面的维度”，应新增“教育资源与学校建设”等城市教育维度，不能把整个主题改成“校园变化”。
- 修改后总数为 2-8 个。
- 只输出 JSON 数组，每项包含 topic 和 description，不要输出其他文字。
"""


def _normalize_plan_items(items: object) -> list[dict]:
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []

    normalized = []
    for item in items[:_MAX_PLAN_TASKS]:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic", "")).strip()
        description = str(item.get("description", "")).strip()
        if topic and description:
            normalized.append(
                {
                    "id": f"task_{len(normalized) + 1}",
                    "topic": topic,
                    "description": description,
                }
            )
    return normalized


def _plan_summary(plan: list[dict], prefix: str) -> str:
    plan_text = "\n".join(
        f"  Task {i}：{task['topic']} — {task['description']}"
        for i, task in enumerate(plan, start=1)
    )
    return f"{prefix} {len(plan)} 个研究方向：\n{plan_text}"


async def web_human_approval_node(state: WebState) -> dict:
    plan = state["plan"]
    plan_text = "\n".join(
        f"  Task {i}：{task['topic']} — {task['description']}"
        for i, task in enumerate(plan, start=1)
    )
    prompt = (
        f"我计划从以下维度研究 {state['location']} {state['start_year']}-{state['end_year']} 年的变化：\n\n"
        f"{plan_text}\n\n"
        "请确认，或告诉我需要调整哪些维度。"
    )

    user_response: str = interrupt(prompt)
    stripped = user_response.strip()
    normalized = stripped.lower()
    human_message = [HumanMessage(content=stripped)] if stripped else []

    if not stripped or normalized in _CONFIRM_WORDS:
        return {
            "plan": plan,
            "plan_approved": True,
            "messages": human_message,
        }

    if normalized in {"取消", "退出", "算了"}:
        return {
            "plan": [],
            "plan_approved": False,
            "messages": human_message + [AIMessage(content="好的，已取消本次分析。")],
        }

    # JSON 整体替换
    if stripped.startswith("["):
        try:
            new_plan = _normalize_plan_items(json.loads(stripped))
            if new_plan:
                return {
                    "plan": new_plan,
                    "plan_approved": False,
                    "messages": human_message + [
                        AIMessage(content=_plan_summary(new_plan, "已按你的要求更新为"))
                    ],
                }
        except Exception:
            pass

    # 自然语言增量编辑；显式携带原始研究上下文，避免把编辑要求误当成新主题。
    edit_context = json.dumps(
        {
            "research_context": {
                "location": state["location"],
                "start_year": state["start_year"],
                "end_year": state["end_year"],
                "original_request": state["user_input"],
            },
            "current_plan": [{"topic": t["topic"], "description": t["description"]} for t in plan],
            "edit_request": stripped,
        },
        ensure_ascii=False,
    )
    resp = await _llm(max_tokens=768).ainvoke(
        [
            SystemMessage(content=_PLAN_EDIT_SYSTEM),
            HumanMessage(content=edit_context),
        ],
        config={"tags": [TAG_NOSTREAM]},
    )
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", resp.content).strip()
    try:
        new_plan = _normalize_plan_items(json.loads(raw))
    except Exception:
        new_plan = []

    if new_plan:
        return {
            "plan": new_plan,
            "plan_approved": False,
            "messages": human_message + [
                AIMessage(content=_plan_summary(new_plan, "已按你的要求更新为"))
            ],
        }

    return {
        "plan": plan,
        "plan_approved": False,
        "messages": human_message + [
            AIMessage(content="我没能解析这次修改，原计划已保留。请换一种说法说明要增加、删除或修改的维度。")
        ],
    }


def dispatch_researchers(state: WebState) -> list[Send] | str:
    if not state["plan"]:
        return "__end__"
    if not state.get("plan_approved", False):
        return "web_human_approval"
    return [
        Send("web_researcher", {
            "messages": state["messages"],
            "task": task,
            "location": state["location"],
            "start_year": state["start_year"],
            "end_year": state["end_year"],
        })
        for task in state["plan"]
    ]


# ── 8. Researcher 节点 ────────────────────────────────────────────────────────


def _compact(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _src_line(num: int, title: str, url: str) -> str:
    if title and url:
        return f"- [{num}] {title} - {url}"
    return f"- [{num}] {title or url}"


_SOURCE_HEADING_LINE_RE = re.compile(
    r"^\s*(?:#{1,6}\s*来源\s*[:：]?\s*|【\s*来源\s*】\s*|来源\s*[:：]?\s*)$",
    re.MULTILINE,
)


def _findings_degenerate(findings: str) -> bool:
    """结论正文缺失：整体为空，或来源区段前只有短前言（模型漏写结论）。"""
    text = (findings or "").strip()
    if not text:
        return True
    body = _SOURCE_HEADING_LINE_RE.split(text, 1)[0]
    return len(body.strip()) < 200


def _salvage_findings(
    topic: str,
    search_groups: list[dict],
    tool_results: list[dict],
) -> str:
    """研究员最终输出退化时，用已收集的原始材料确定性重建 findings。"""
    evidence: list[str] = []
    sources: list[str] = []
    seen: set[str] = set()
    for sg in search_groups:
        for item in sg.get("results", []):
            if len(sources) >= 20:
                break
            url = _compact(item.get("url"))
            title = _compact(item.get("title"))
            snippet = _compact(item.get("snippet"))
            if not url.startswith(("http://", "https://")):
                continue
            key = url.rstrip("/").casefold()
            if key in seen:
                continue
            seen.add(key)
            n = len(sources) + 1
            label = title or url
            evidence.append(f"- {label}：{snippet}[{n}]" if snippet else f"- {label}[{n}]")
            sources.append(_src_line(n, title, url))
        if len(sources) >= 20:
            break

    tool_labels = {
        "analyze_satellite_image": "卫星图像分析",
        "query_poi_history": "POI 历史数据",
    }
    tool_notes = []
    for tr in tool_results:
        label = tool_labels.get(tr.get("tool"), str(tr.get("tool") or ""))
        summary = _compact(tr.get("summary"))[:400]
        if label and summary:
            tool_notes.append(f"- {label}：{summary}")

    if not evidence and not tool_notes:
        return ""

    parts = [
        f"【研究材料汇总：{topic}】",
        "（研究过程未正常输出结论，以下为已收集的证据材料）",
        "",
    ]
    parts.extend(evidence)
    if tool_notes:
        parts.extend(["", "**工具分析结果**"])
        parts.extend(tool_notes)
    body = "\n".join(parts)
    if sources:
        body += "\n\n## 来源\n\n" + "\n".join(sources)
    return body


class ResearcherInput(TypedDict):
    messages: list[BaseMessage]
    task: dict
    location: str
    start_year: int
    end_year: int


async def web_researcher_node(state: ResearcherInput) -> dict:
    from graph.researcher_graph import _make_researcher
    from langchain_core.messages import HumanMessage as HM

    task = state["task"]
    location = state["location"]
    start_year = state["start_year"]
    end_year = state["end_year"]
    topic = task["topic"]
    execution_id = f"{task['id']}:{uuid4().hex}"

    query = (
        f"研究任务：{topic}\n"
        f"具体内容：{task['description']}\n"
        f"分析地点：{location}\n"
        f"时间范围：{start_year} 年 → {end_year} 年\n\n"
        "请开始收集证据，完成后给出研究结论。"
    )

    agent = _make_researcher()
    findings = ""
    progress_msgs = []
    progress_events = []
    search_groups = []
    tool_results = []
    terminal_status = "completed"
    # 实时进度写回流（前端 onCustomEvent 接住）。非流式环境下 writer 是 no-op。
    try:
        writer = get_stream_writer()
    except Exception:
        writer = None

    def _emit(
        stage: str,
        detail: str,
        *,
        content: str = "",
        status: str = "running",
    ) -> None:
        event_payload = {
            "sequence": len(progress_events) + 1,
            "stage": stage,
            "detail": detail,
            "content": content,
            "status": status,
        }
        progress_events.append(event_payload)
        if writer is None:
            return
        try:
            writer({
                "type": "research_progress",
                "execution_id": execution_id,
                "task_id": task["id"],
                "topic": topic,
                "stage": stage,
                "detail": detail,
                "content": content,
                "status": status,
                "sequence": event_payload["sequence"],
                # 兼容旧前端；新逻辑使用 sequence。
                "round": event_payload["sequence"],
            })
        except Exception:
            pass  # 进度事件失败不影响研究流程

    def _tool_output_preview(value: object, limit: int = 1200) -> str:
        text = str(value).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) > limit:
            return f"{text[:limit].rstrip()}…"
        return text

    def _parse_search_group(value: object) -> dict | None:
        try:
            data = json.loads(str(value))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None

        raw_results = data.get("results")
        results = []
        if isinstance(raw_results, list):
            for item in raw_results[:5]:
                if not isinstance(item, dict):
                    continue
                results.append({
                    "title": str(item.get("title", "")).strip()[:180],
                    "url": str(item.get("url", "")).strip(),
                    "snippet": str(item.get("snippet", "")).strip()[:260],
                    "source_label": str(item.get("source_label", "")).strip(),
                })

        return {
            "query": str(data.get("query", "")).strip(),
            "total": int(data.get("total", len(results)) or 0),
            "error": str(data.get("error", "")).strip(),
            "results": results,
        }

    try:
        async for event in agent.astream_events(
            {"messages": [HM(content=query)]},
            config={
                "recursion_limit": 40,
                "tags": [TAG_NOSTREAM],
            },
            version="v2",
        ):
            kind = event["event"]
            name = event.get("name", "")

            if kind == "on_tool_start":
                tool_input = event.get("data", {}).get("input", {})
                if name == "web_search":
                    hint = tool_input.get("query", "")[:50]
                    progress_msgs.append(f"正在搜索：{hint}")
                    _emit("searching", f"正在搜索：{hint}")
                elif name == "analyze_satellite_image":
                    progress_msgs.append(f"正在分析卫星影像...")
                    _emit("satellite", "正在分析卫星影像...")
                elif name == "query_poi_history":
                    cat = tool_input.get("category", "")
                    progress_msgs.append(f"正在查询 POI 数据（{cat}）...")
                    _emit("poi", f"正在查询 POI 数据（{cat}）...")

            elif kind == "on_tool_end":
                output = event.get("data", {}).get("output", "")
                if hasattr(output, "content"):
                    output_str = output.content
                else:
                    output_str = str(output)
                if name == "web_search":
                    search_group = _parse_search_group(output_str)
                    if search_group:
                        search_groups.append(search_group)
                        query = search_group["query"] or "当前关键词"
                        count = len(search_group["results"])
                        detail = f"搜索完成：{query}（{count} 条有效结果）"
                        progress_msgs.append(detail)
                        _emit("search_done", detail)
                        if search_group["results"]:
                            titles = "\n".join(
                                f"{i}. {item['title']}"
                                for i, item in enumerate(
                                    search_group["results"][:3],
                                    start=1,
                                )
                            )
                            _emit(
                                "search_result",
                                "已生成搜索结果概览",
                                content=titles,
                            )
                    else:
                        progress_msgs.append("搜索完成")
                        _emit("search_done", "搜索完成")
                elif name in ("analyze_satellite_image", "query_poi_history"):
                    progress_msgs.append(f"{name} 完成")
                    _emit("tool_done", f"{name} 完成")
                    result_preview = _tool_output_preview(output_str, limit=600)
                    if result_preview:
                        tool_results.append({
                            "tool": name,
                            "summary": result_preview,
                        })
                        _emit(
                            "tool_result",
                            f"{name} 已生成结果摘要",
                            content=result_preview,
                        )

            elif kind == "on_chat_model_end":
                msg = event.get("data", {}).get("output")
                if msg and hasattr(msg, "content") and msg.content:
                    tool_calls = getattr(msg, "tool_calls", [])
                    if not tool_calls:
                        findings = msg.content

    except Exception as e:
        logger.error("Researcher 执行失败 | %s: %s", task.get("id"), e)
        terminal_status = "failed"
        # 兜底：用已收集的搜索/工具结果拼出摘要，避免 findings 为空
        fallback_parts = []
        for sg in search_groups:
            for item in sg.get("results", [])[:5]:
                title = item.get("title", "")
                snippet = item.get("snippet", "")
                url = item.get("url", "")
                if title:
                    fallback_parts.append(f"- {title}: {snippet} ({url})")
        for tr in tool_results:
            fallback_parts.append(f"- [{tr['tool']}] {tr['summary']}")
        if fallback_parts:
            findings = (
                f"[{topic}] 研究未正常收敛（{e}），以下为已收集的原始材料：\n"
                + "\n".join(fallback_parts)
            )
        else:
            findings = f"[{topic}] 研究过程出错：{e}"

    if terminal_status == "completed":
        if findings:
            _emit(
                "finding",
                "研究结论已生成",
                status="finalizing",
            )
        # 先进入收尾态；前端收到同 execution_id 的持久化消息后再移除临时卡。
        _emit(
            "complete",
            f"研究完成（共 {len(progress_msgs)} 步）",
            status="finalizing",
        )
    else:
        _emit(
            "failed",
            "研究过程执行失败",
            content=str(findings),
            status="failed",
        )

    # 研究员偶尔输出退化：最终消息为空，或只剩短前言+来源、结论正文丢失。
    # 用已收集的原始检索/工具材料确定性重建，保证子 agent 报告不空、来源标题正确。
    if terminal_status == "completed" and _findings_degenerate(findings):
        salvaged = _salvage_findings(topic, search_groups, tool_results)
        if salvaged:
            findings = salvaged

    # 模型偶尔会给出完整的证据编号与来源描述，却漏掉最后的 URL。
    # 使用本轮已保存的原始搜索结果做确定性回填，避免最终报告出现不可点击来源。
    if findings and search_groups:
        findings = enrich_report_sources(findings, search_groups)

    # 研究员偶尔整段漏写 ## 来源（比如只做了卫星图分析）。
    # 用已保存的原始检索结果确定性地补一个来源区段，保证每个 agent 都有来源。
    if findings and terminal_status == "completed" and not has_source_section(findings):
        lines: list[str] = []
        seen: set[str] = set()
        for sg in search_groups:
            for item in sg.get("results", []):
                url = _compact(item.get("url"))
                title = _compact(item.get("title"))
                if not url.startswith(("http://", "https://")):
                    continue
                key = url.rstrip("/").casefold()
                if key in seen:
                    continue
                seen.add(key)
                lines.append(_src_line(len(lines) + 1, title, url))
        if not lines:
            tool_labels = {
                "analyze_satellite_image": (
                    f"卫星图像分析（{location} {start_year}→{end_year}）"
                ),
                "query_poi_history": "POI 历史数据（OpenStreetMap/ohsome API）",
            }
            used = {tr.get("tool") for tr in tool_results}
            for tool, label in tool_labels.items():
                if tool in used:
                    lines.append(_src_line(len(lines) + 1, label, ""))
        if lines:
            findings = (
                f"{findings.rstrip()}\n\n## 来源\n\n" + "\n".join(lines)
            )

    # 使用结构化 payload，避免把长 JSON 直接混排进 Markdown。
    task_number = task["id"].removeprefix("task_")
    card_payload = json.dumps(
        {
            "version": 3,
            "execution_id": execution_id,
            "task_id": task["id"],
            "status": terminal_status,
            "events": progress_events,
            "process": progress_msgs,
            "searches": search_groups,
            "tools": tool_results,
            "findings": findings,
        },
        ensure_ascii=False,
    )
    result_label = "执行结果" if terminal_status == "completed" else "执行失败"
    card_content = (
        f"【Agent {task_number} · {topic} {result_label}】\n"
        f"{card_payload}"
    )

    return {
        "messages": [AIMessage(content=card_content, name="internal")],
        "findings": [f"=== {topic} ===\n{findings}"],
    }


# ── 9. Reporter 节点 ──────────────────────────────────────────────────────────

_REPORTER_SYSTEM = """\
你是城市治理分析报告撰写助手。
只能基于提供的研究笔记撰写报告，禁止添加未经证实的内容。
对于笔记中未涉及的方面，明确说明"现有证据不足，本报告不作评价"。
报告结构清晰，语言专业简洁。

引用规则（务必遵守）：
- 正文引用证据时使用方括号编号 [n]，编号必须对应随附的统一来源列表，不得自创新编号；
- 笔记中没有 [n] 编号的内容（如卫星图像分析、POI 数据）直接陈述即可，严禁为其编造或续写任何编号；
- 来源编号在整份报告中全局唯一，不得在新章节重新从 [1] 开始，也不得让同一编号指向不同来源；
- 只引用最终报告实际采用的证据，不要为了展示来源而引用无关材料；
- 报告结尾输出一个 `## 来源` 区段，按编号列出所有引用过的来源；
- 若某条来源未在正文中引用，可省略；不要重新编号已存在的来源。
"""


async def web_reporter_node(state: WebState) -> dict:
    findings = state.get("findings") or []
    if not findings:
        report = f"对于{state['location']} {state['start_year']}-{state['end_year']}年的变化，现有证据不足以支持任何结论。"
        return {
            "report": report,
            "messages": [AIMessage(content=report)],
        }

    # 合并多份研究笔记：按 URL 去重 + 跨 finding 连续编号 + 同步替换正文 [n]
    merged = merge_findings_with_sources(findings)

    try:
        writer = get_stream_writer()
    except Exception:
        writer = None

    report = ""
    # TAG_NOSTREAM：报告进度已由 report_chunk custom event 推送，
    # 若不抑制，token 会同时经 messages-tuple 流涌向前端造成渲染风暴。
    async for chunk in _llm(max_tokens=8192).astream([
        SystemMessage(content=_REPORTER_SYSTEM),
        HumanMessage(content=(
            f"分析对象：{state['location']}\n"
            f"时间范围：{state['start_year']} 至 {state['end_year']}\n\n"
            f"研究笔记（正文 [n] 已统一编号）：\n{merged.body}\n\n"
            f"统一来源列表（正文 [n] 必须对应这里的编号）：\n{merged.sources_md}\n\n"
            "请撰写城市变化分析报告，正文用 [n] 引用来源，报告结尾输出 ## 来源 区段。"
        )),
    ], config={"tags": [TAG_NOSTREAM]}):
        delta = chunk.content or ""
        if not delta:
            continue
        report += delta
        if writer is not None:
            try:
                writer({"type": "report_chunk", "delta": delta})
            except Exception:
                pass

    # 始终使用统一来源，并仅保留正文实际引用的编号。
    report = replace_report_sources(report, merged.sources)

    if writer is not None:
        try:
            writer({"type": "report_done"})
        except Exception:
            pass

    return {
        "report": report,
        "messages": [AIMessage(content=report)],
    }


# ── 10. 构建图 ────────────────────────────────────────────────────────────────

def build_web_graph():
    g = StateGraph(WebState)

    g.add_node("router", router_node)
    g.add_node("parse_input", parse_input_node)
    g.add_node("web_planner", web_planner_node)
    g.add_node("web_human_approval", web_human_approval_node)
    g.add_node("web_researcher", web_researcher_node)
    g.add_node("web_reporter", web_reporter_node)

    g.add_edge(START, "router")
    g.add_conditional_edges("router", route_after_router, ["parse_input", "__end__"])
    g.add_conditional_edges("parse_input", route_after_parse, ["web_planner", "__end__"])
    g.add_edge("web_planner", "web_human_approval")
    g.add_conditional_edges(
        "web_human_approval",
        dispatch_researchers,
        ["web_human_approval", "web_researcher", "__end__"],
    )
    g.add_edge("web_researcher", "web_reporter")
    g.add_edge("web_reporter", END)

    return g.compile()


graph = build_web_graph()
