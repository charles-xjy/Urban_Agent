"""
主图：DeerFlow 白盒规划模式

流程：
  clarify → planner → [human_approval] → researcher×N（并发）→ compress → reporter
                             ↑
                        interrupt() 暂停，等用户确认/修改计划
"""

import json
import logging
import operator
import re
import uuid
from typing import Annotated

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.constants import Send
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from config.settings import AGENT_MODEL_NAME, AGENT_MODEL_URL, VLLM_API_KEY
from graph.researcher_graph import researcher_node
from graph.state import AgentState, ResearchTask, ResearcherState

logger = logging.getLogger(__name__)

# ── LLM 工厂 ──────────────────────────────────────────────────────────────────

def _llm(temperature: float = 0, max_tokens: int = 512) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=AGENT_MODEL_URL,
        api_key=VLLM_API_KEY,
        model=AGENT_MODEL_NAME,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ── Node 1：clarify_with_user ─────────────────────────────────────────────────

_CLARIFY_SYSTEM = """\
判断用户的城市分析请求是否包含足够信息（至少需要：地点 + 时间范围）。

如果信息完整，输出：
{"needed": false, "question": ""}

如果缺少关键信息，输出一个追问问题：
{"needed": true, "question": "请问您希望分析哪个时间段？"}

只输出 JSON，不含其他文字。
"""


async def clarify_node(state: AgentState) -> AgentState:
    if state.get("batch_mode"):
        return {"clarify_needed": False, "clarify_answer": ""}

    resp = await _llm().ainvoke([
        SystemMessage(content=_CLARIFY_SYSTEM),
        HumanMessage(content=state["user_input"]),
    ])
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", resp.content).strip()
    try:
        result = json.loads(raw)
    except Exception:
        result = {"needed": False, "question": ""}

    needed: bool = result.get("needed", False)
    question: str = result.get("question", "")

    if needed:
        answer: str = interrupt(question)
        return {
            "clarify_needed": True,
            "clarify_answer": answer,
            "user_input": f"{state['user_input']}\n补充：{answer}",
        }

    return {"clarify_needed": False, "clarify_answer": ""}


# ── Node 2：planner ───────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """\
你是城市研究规划师。根据用户的分析需求，制定 2-4 个研究子任务。

每个子任务需要：
- topic：简短标题（≤10字），如"生态环境变化"
- description：具体研究内容（≤30字），如"植被覆盖、水体面积、绿地密度变化"

输出严格为 JSON 数组：
[
  {"topic": "生态环境变化", "description": "植被覆盖、水体面积、绿地密度变化"},
  {"topic": "建筑建设变化", "description": "住宅、商业楼宇密度与规模变化"}
]

只输出 JSON，不含其他文字。
"""


async def planner_node(state: AgentState) -> AgentState:
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
        logger.warning("Planner 输出解析失败，原文：%s", raw)
        items = [{"topic": "综合变化分析", "description": "城市整体变化"}]

    plan: list[ResearchTask] = [
        {"id": f"task_{i+1}", "topic": item["topic"], "description": item["description"]}
        for i, item in enumerate(items)
    ]
    return {"plan": plan}


# ── Node 3：human_approval ────────────────────────────────────────────────────

_CONFIRM_WORDS = {"确认", "可以", "ok", "okay", "yes", "y", "行", "好", "没问题", "同意", "开始", "执行"}

_PLAN_EDIT_SYSTEM = """\
你是研究计划编辑助手。根据当前研究计划和用户的自然语言修改要求，输出修改后的计划。

规则：
- 保留用户没有要求删除或修改的任务
- 按用户要求替换、删除、合并或新增任务
- 总任务数保持在 2-4 个；除非用户明确要求更少，否则至少保留 2 个
- topic：简短标题（≤10字）
- description：具体研究内容（≤30字）
- 输出严格为 JSON 数组，不含其他文字

输出示例：
[
  {"topic": "空间扩张", "description": "建成区面积、建设用地扩张趋势"},
  {"topic": "经济发展", "description": "产业布局、投资强度和经济活动变化"}
]
"""


async def human_approval_node(state: AgentState) -> AgentState:
    """
    展示计划给用户，等待用户确认或修改。

    用户可以回复：
      - "确认" / "可以" / "ok" → 直接执行
      - JSON 数组 → 替换整个计划
      - 自然语言修改描述 → 交给 LLM 重新解析计划
    """
    plan = state["plan"]
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

    # 尝试解析用户修改（JSON 格式）
    if stripped.startswith("["):
        try:
            items = json.loads(stripped)
            new_plan: list[ResearchTask] = [
                {
                    "id": f"task_{i+1}",
                    "topic": item.get("topic", item.get("t", "")),
                    "description": item.get("description", item.get("d", "")),
                }
                for i, item in enumerate(items)
                if item.get("topic", item.get("t", "")) and item.get("description", item.get("d", ""))
            ]
            if new_plan:
                return {"plan": new_plan}
        except Exception:
            pass

    edit_context = {
        "current_plan": [
            {"topic": t["topic"], "description": t["description"]}
            for t in plan
        ],
        "user_request": stripped,
    }
    resp = await _llm(max_tokens=512).ainvoke([
        SystemMessage(content=_PLAN_EDIT_SYSTEM),
        HumanMessage(content=json.dumps(edit_context, ensure_ascii=False)),
    ])
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", resp.content).strip()
    try:
        items = json.loads(raw)
        if isinstance(items, dict):
            items = [items]
        new_plan = [
            {
                "id": f"task_{i+1}",
                "topic": item.get("topic", item.get("t", "")),
                "description": item.get("description", item.get("d", "")),
            }
            for i, item in enumerate(items[:4])
            if item.get("topic", item.get("t", "")) and item.get("description", item.get("d", ""))
        ]
        if new_plan:
            return {"plan": new_plan}
    except Exception:
        logger.warning("计划修改解析失败，用户输入：%s，模型输出：%s", stripped, raw)

    return {"plan": plan}


# ── Node 4：dispatch_researchers（路由函数，返回 Send 列表）────────────────────

def dispatch_researchers(state: AgentState) -> list[Send]:
    """将每个 ResearchTask 用 Send() 并发派发给 researcher 子图。"""
    return [
        Send(
            "researcher",
            {
                "task": task,
                "location": state["location"],
                "start_year": state["start_year"],
                "end_year": state["end_year"],
                "findings": "",
                "runtime": 0,
            },
        )
        for task in state["plan"]
    ]


# ── Node 6：reporter ──────────────────────────────────────────────────────────

_REPORTER_SYSTEM = """\
你是城市治理分析报告撰写助手。
只能基于提供的研究笔记撰写报告，禁止添加未经证实的内容。
对于笔记中未涉及的方面，明确说明"现有证据不足，本报告不作评价"。
报告结构清晰，语言专业简洁，不使用"可能""推测"等模糊表达。
"""


async def reporter_node(state: AgentState) -> AgentState:
    findings_text = "\n\n---\n\n".join(state.get("findings") or [])
    if not findings_text:
        return {
            "report": (
                f"对于{state['location']} {state['start_year']}-{state['end_year']}年的变化，"
                "现有证据不足以支持任何结论，本报告不作评价。"
            )
        }

    resp = await _llm(max_tokens=8192).ainvoke([
        SystemMessage(content=_REPORTER_SYSTEM),
        HumanMessage(content=(
            f"分析对象：{state['location']}\n"
            f"时间范围：{state['start_year']} 至 {state['end_year']}\n\n"
            f"研究笔记：\n{findings_text}\n\n"
            "请撰写城市变化分析报告。"
        )),
    ])
    return {"report": resp.content}


# ── 构建图 ────────────────────────────────────────────────────────────────────

def build_main_graph():
    g = StateGraph(AgentState)

    g.add_node("clarify",        clarify_node)
    g.add_node("planner",        planner_node)
    g.add_node("human_approval", human_approval_node)
    g.add_node("researcher",     researcher_node)
    g.add_node("reporter",       reporter_node)

    g.add_edge(START,            "clarify")
    g.add_edge("clarify",        "planner")
    def _after_planner(state: AgentState):
        if state.get("batch_mode"):
            return dispatch_researchers(state)
        return "human_approval"

    g.add_conditional_edges("planner", _after_planner, ["human_approval", "researcher"])
    # human_approval → researcher×N 并发派发
    g.add_conditional_edges("human_approval", dispatch_researchers, ["researcher"])
    g.add_edge("researcher",     "reporter")
    g.add_edge("reporter",       END)

    return g.compile(checkpointer=MemorySaver())


main_graph = build_main_graph()


# ── 入口 ──────────────────────────────────────────────────────────────────────

async def run(
    user_input: str,
    location: str,
    start_year: int,
    end_year: int,
) -> str:
    """交互模式：带 clarify / human_approval 的完整流程。"""
    initial: AgentState = {
        "user_input":     user_input,
        "location":       location,
        "start_year":     start_year,
        "end_year":       end_year,
        "batch_mode":     False,
        "clarify_needed": False,
        "clarify_answer": "",
        "plan":           [],
        "findings":       [],
        "report":         "",
    }
    final = await main_graph.ainvoke(initial)
    return final["report"]


async def batch_run(location: str, start_year: int, end_year: int) -> str:
    """批量模式：跳过 clarify / human_approval，直接执行。用于对照实验。"""
    initial: AgentState = {
        "user_input":     f"分析{location} {start_year}-{end_year} 年的城市变化",
        "location":       location,
        "start_year":     start_year,
        "end_year":       end_year,
        "batch_mode":     True,
        "clarify_needed": False,
        "clarify_answer": "",
        "plan":           [],
        "findings":       [],
        "report":         "",
    }
    final = await main_graph.ainvoke(
        initial,
        config={"configurable": {"thread_id": f"{location}_{start_year}_{end_year}_{id(initial)}"}},
    )
    return final["report"]
