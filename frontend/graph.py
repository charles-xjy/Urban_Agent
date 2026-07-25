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
import operator
import os
import re
import sys
from datetime import datetime
from typing import Annotated

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.constants import Send
from langgraph.graph import END, START, StateGraph
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
    plan: list[dict]
    findings: Annotated[list[str], operator.add]
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
你是城市变化研究智能体的路由助手。当前年份：{datetime.now().year}。

判断用户最新消息的意图：
1. 如果包含城市/地区名称 + 分析/变化/发展等意图 → type: "analysis"
2. 如果是问候、闲聊、询问功能 → type: "chat"，给出友好回复
3. 如果意图不明或信息不足 → type: "clarify"，追问具体需求

输出严格 JSON：
- {{"type": "analysis"}}
- {{"type": "chat", "reply": "你的回复内容"}}
- {{"type": "clarify", "question": "你的追问"}}

注意：
- "你好"、"在吗"、"你能做什么" → chat
- "asdf"、"1"、无意义输入 → clarify，引导用户输入有效的城市分析请求
- "分析雄安新区变化"、"北京最近5年发展" → analysis
"""


async def router_node(state: WebState) -> dict:
    messages = state["messages"]
    last_human = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            last_human = msg.content if isinstance(msg.content, str) else str(msg.content)
            break

    resp = await _llm(max_tokens=256).ainvoke([
        SystemMessage(content=_ROUTER_SYSTEM),
        HumanMessage(content=last_human),
    ])
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", resp.content).strip()
    try:
        result = json.loads(raw)
    except Exception:
        result = {"type": "clarify", "question": "请输入您想分析的城市和时间范围，例如：雄安新区 2018 到 2024 年的城市变化"}

    route_type = result.get("type", "clarify")

    if route_type == "chat":
        reply = result.get("reply", "你好！我是城市变化研究智能体，可以帮你分析任意城市在指定时间段内的变化。请输入分析请求，例如：雄安新区 2018 到 2024 年的城市变化。")
        return {
            "messages": [AIMessage(content=reply)],
            "route": "chat",
        }
    elif route_type == "analysis":
        return {"route": "analysis", "user_input": last_human}
    else:
        question = result.get("question", "请输入您想分析的城市和时间范围。")
        return {
            "messages": [AIMessage(content=question)],
            "route": "clarify",
        }


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
    resp = await _llm(max_tokens=128).ainvoke([
        SystemMessage(content=_PARSE_SYSTEM),
        HumanMessage(content=user_input),
    ])
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
    resp = await _llm(max_tokens=512).ainvoke([
        SystemMessage(content=_PLANNER_SYSTEM),
        HumanMessage(content=context),
    ])
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
        "messages": [AIMessage(content=msg)],
    }


# ── 7. Human Approval 节点 ────────────────────────────────────────────────────

_CONFIRM_WORDS = {"确认", "可以", "ok", "okay", "yes", "y", "行", "好", "没问题", "同意", "开始", "执行"}

_PLAN_EDIT_SYSTEM = """\
你是研究计划编辑助手。根据当前研究计划和用户的修改要求，输出修改后的计划。
规则：保留未要求修改的任务，总数 2-4 个，输出 JSON 数组。
"""


async def web_human_approval_node(state: WebState) -> dict:
    plan = state["plan"]

    while True:
        plan_text = "\n".join(
            f"  Task {t['id'].split('_')[1]}：{t['topic']} — {t['description']}"
            for t in plan
        )
        prompt = (
            f"我计划从以下维度研究 {state['location']} {state['start_year']}-{state['end_year']} 年的变化：\n\n"
            f"{plan_text}\n\n"
            "请确认，或告诉我需要调整哪些维度。"
        )

        user_response: str = interrupt(prompt)
        stripped = user_response.strip()
        normalized = stripped.lower()

        if not stripped or normalized in _CONFIRM_WORDS:
            return {"plan": plan}

        if normalized in {"取消", "退出", "算了"}:
            return {
                "plan": [],
                "messages": [AIMessage(content="好的，已取消本次分析。")],
            }

        # JSON 替换
        if stripped.startswith("["):
            try:
                items = json.loads(stripped)
                new_plan = [
                    {"id": f"task_{i+1}", "topic": it.get("topic", ""), "description": it.get("description", "")}
                    for i, it in enumerate(items[:4])
                    if it.get("topic") and it.get("description")
                ]
                if new_plan:
                    plan = new_plan
                    return {"plan": plan}
            except Exception:
                pass

        # LLM 编辑
        edit_context = json.dumps({
            "current_plan": [{"topic": t["topic"], "description": t["description"]} for t in plan],
            "user_request": stripped,
        }, ensure_ascii=False)
        resp = await _llm(max_tokens=512).ainvoke([
            SystemMessage(content=_PLAN_EDIT_SYSTEM),
            HumanMessage(content=edit_context),
        ])
        raw = re.sub(r"```(?:json)?\s*|\s*```", "", resp.content).strip()
        try:
            items = json.loads(raw)
            if isinstance(items, dict):
                items = [items]
            new_plan = [
                {"id": f"task_{i+1}", "topic": it.get("topic", ""), "description": it.get("description", "")}
                for i, it in enumerate(items[:4])
                if it.get("topic") and it.get("description")
            ]
            if new_plan:
                plan = new_plan
                return {"plan": plan}
        except Exception:
            pass

        # 解析失败，重新提示
        continue


def dispatch_researchers(state: WebState) -> list[Send] | str:
    if not state["plan"]:
        return "__end__"
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

class ResearcherInput(TypedDict):
    messages: list[BaseMessage]
    task: dict
    location: str
    start_year: int
    end_year: int


_RESEARCHER_SYSTEM = """\
你是城市变化研究员。针对一个具体研究子任务，收集多源证据后给出研究结论。

工具使用策略：
- web_search：先用，4-6 个关键词，同方向最多 3 次
- analyze_satellite_image：文字证据提到空间/物理变化时调用
- query_poi_history：需要量化支撑时调用

输出格式：
【研究结论：{子任务名称}】
[按条列出发现，注明证据来源和置信度]
"""


async def web_researcher_node(state: ResearcherInput) -> dict:
    from graph.researcher_graph import _make_researcher
    from langchain_core.messages import HumanMessage as HM

    task = state["task"]
    location = state["location"]
    start_year = state["start_year"]
    end_year = state["end_year"]
    topic = task["topic"]

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

    try:
        async for event in agent.astream_events(
            {"messages": [HM(content=query)]},
            config={"recursion_limit": 80},
            version="v2",
        ):
            kind = event["event"]
            name = event.get("name", "")

            if kind == "on_tool_start":
                tool_input = event.get("data", {}).get("input", {})
                if name == "web_search":
                    hint = tool_input.get("query", "")[:50]
                    progress_msgs.append(f"正在搜索：{hint}")
                elif name == "analyze_satellite_image":
                    progress_msgs.append(f"正在分析卫星影像...")
                elif name == "query_poi_history":
                    cat = tool_input.get("category", "")
                    progress_msgs.append(f"正在查询 POI 数据（{cat}）...")

            elif kind == "on_tool_end":
                output = event.get("data", {}).get("output", "")
                if hasattr(output, "content"):
                    output_str = output.content
                else:
                    output_str = str(output)
                if name == "web_search":
                    progress_msgs.append(f"搜索完成")
                elif name in ("analyze_satellite_image", "query_poi_history"):
                    progress_msgs.append(f"{name} 完成")

            elif kind == "on_chat_model_end":
                msg = event.get("data", {}).get("output")
                if msg and hasattr(msg, "content") and msg.content:
                    tool_calls = getattr(msg, "tool_calls", [])
                    if not tool_calls:
                        findings = msg.content

    except Exception as e:
        logger.error("Researcher 执行失败 | %s: %s", task.get("id"), e)
        findings = f"[{topic}] 研究过程出错：{e}"

    # 输出折叠卡片内容
    progress_text = "\n".join(f"  • {p}" for p in progress_msgs)
    card_content = f"【{topic} 执行结果】\n研究过程：\n{progress_text}\n\n{findings}"

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
"""


async def web_reporter_node(state: WebState) -> dict:
    findings_text = "\n\n---\n\n".join(state.get("findings") or [])
    if not findings_text:
        report = f"对于{state['location']} {state['start_year']}-{state['end_year']}年的变化，现有证据不足以支持任何结论。"
    else:
        resp = await _llm(max_tokens=8192).ainvoke([
            SystemMessage(content=_REPORTER_SYSTEM),
            HumanMessage(content=(
                f"分析对象：{state['location']}\n"
                f"时间范围：{state['start_year']} 至 {state['end_year']}\n\n"
                f"研究笔记：\n{findings_text}\n\n"
                "请撰写城市变化分析报告。"
            )),
        ])
        report = resp.content

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
    g.add_conditional_edges("web_human_approval", dispatch_researchers, ["web_researcher", "__end__"])
    g.add_edge("web_researcher", "web_reporter")
    g.add_edge("web_reporter", END)

    return g.compile()


graph = build_web_graph()
