"""
主图：DeerFlow 白盒规划模式

流程：
  clarify → planner → [human_approval] → researcher×N（并发）→ gap_eval
                             ↑                                      │
                        interrupt() 确认/修改计划                    ↓
                                              [supplement_approval] ─通过→ reporter
                                                     │  ↑
                                                     └──┘ 补充收集（Send→researcher，带最大轮数上限）

  gap_eval            ：模型判断已收集资料是否充分、是否需要补充
  supplement_approval ：把模型判断展示给用户，interrupt() 等用户审批，
                        通过则进 reporter，否则按补充计划再收集一轮
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

import config.settings as settings
from core.source_merge import has_source_section, merge_findings_with_sources
from graph.researcher_graph import researcher_node
from graph.state import AgentState, ResearchTask, ResearcherState

logger = logging.getLogger(__name__)

# ── LLM 工厂 ──────────────────────────────────────────────────────────────────

def _llm(temperature: float = 0, max_tokens: int = 512) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.AGENT_MODEL_URL,
        api_key=settings.VLLM_API_KEY,
        model=settings.AGENT_MODEL_NAME,
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
_REJECT_WORDS = {"不对", "取消", "算了", "不要", "退出", "重", "错", "改", "重新"}

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
      - "不对" / "取消" / "算了" → 重新展示计划，要求明确指示
    """
    plan = state["plan"]
    show_reprompt = False
    
    while True:
        plan_text = "\n".join(
            f"  Task {t['id'].split('_')[1]}：{t['topic']} — {t['description']}"
            for t in plan
        )
        
        if show_reprompt:
            prompt = (
                f"收到您的反馈。请明确告诉我：\n"
                f"1. 输入'确认'执行当前计划\n"
                f"2. 描述需要修改的内容（如'把第一个任务改成...'）\n"
                f"3. 输入 JSON 数组替换整个计划\n"
                f"4. 输入'取消'退出\n\n"
                f"当前计划：\n{plan_text}"
            )
        else:
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
        
        if normalized in {"取消", "退出"}:
            return {"plan": []}
        
        if any(word in normalized for word in _REJECT_WORDS):
            show_reprompt = True
            continue

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
        
        show_reprompt = True


# ── Node 4：dispatch_researchers（路由函数，返回 Send 列表）────────────────────

def _send_tasks(state: AgentState, tasks: list[ResearchTask]) -> list[Send]:
    """将一批 ResearchTask 用 Send() 并发派发给 researcher 子图。"""
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
        for task in tasks
    ]


def dispatch_researchers(state: AgentState) -> list[Send]:
    """首轮：派发 planner/human_approval 确定的完整计划。"""
    return _send_tasks(state, state["plan"])


# ── Node 5：gap_eval（资料充分性判断）─────────────────────────────────────────

MAX_RESEARCH_ROUNDS = 3  # 含首轮在内的最大研究轮数，防止补充收集无限循环

_GAP_EVAL_SYSTEM = """\
你是城市研究资料审核员。给定原始研究计划和已收集的研究笔记，判断证据是否足以支撑撰写最终报告。

判断标准：
- 每个研究维度是否都有实质性证据（而非"证据不足""暂无来源"之类的占位）
- 是否存在关键缺口、未解决的证据冲突，或明显遗漏的重要方面
- 已被研究员标注"冲突未解决/证据不足"且补充也难有来源的方面，不必反复补充

输出严格为 JSON：
{
  "sufficient": true,
  "gap": "一句话说明为何已足够，或还缺什么（≤50字）",
  "supplement": []
}
当 sufficient=false 时，supplement 给出 1-3 个针对性的补充研究任务：
  [{"topic": "简短标题(≤10字)", "description": "具体补充内容(≤30字)"}]
sufficient=true 时 supplement 必须为空数组。
只输出 JSON，不含其他文字。
"""


async def gap_eval_node(state: AgentState) -> AgentState:
    """每批 researcher 完成后运行一次：判断资料是否充分。"""
    round_no = state.get("research_round", 0) + 1
    findings_text = "\n\n---\n\n".join(state.get("findings") or []) or "（暂无任何研究笔记）"

    # 达到轮数上限：强制收尾，不再建议补充
    if round_no >= MAX_RESEARCH_ROUNDS:
        return {
            "research_round": round_no,
            "gap_analysis": f"已达到最大研究轮数（{MAX_RESEARCH_ROUNDS} 轮），不再继续补充，将基于现有资料撰写报告。",
            "supplement_plan": [],
        }

    plan_text = "\n".join(f"- {t['topic']}：{t['description']}" for t in state.get("plan", []))
    resp = await _llm(max_tokens=1024).ainvoke([
        SystemMessage(content=_GAP_EVAL_SYSTEM),
        HumanMessage(content=(
            f"分析对象：{state['location']} {state['start_year']}-{state['end_year']}\n"
            f"原始研究计划：\n{plan_text}\n\n"
            f"已收集的研究笔记：\n{findings_text}"
        )),
    ])
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", resp.content).strip()
    try:
        result = json.loads(raw)
    except Exception:
        logger.warning("gap_eval 输出解析失败，原文：%s", raw)
        result = {"sufficient": True, "gap": "资料审核解析失败，按已充分处理。", "supplement": []}

    sufficient: bool = result.get("sufficient", True)
    gap: str = result.get("gap", "")
    raw_supp = result.get("supplement", []) if not sufficient else []
    supplement_plan: list[ResearchTask] = [
        {
            "id": f"supp{round_no}_{i+1}",
            "topic": item.get("topic", item.get("t", "")),
            "description": item.get("description", item.get("d", "")),
        }
        for i, item in enumerate(raw_supp[:3])
        if item.get("topic", item.get("t", "")) and item.get("description", item.get("d", ""))
    ]
    return {
        "research_round": round_no,
        "gap_analysis": gap,
        "supplement_plan": supplement_plan,
    }


def _after_gap_eval(state: AgentState):
    # 批量模式跳过用户审批，直接收尾
    if state.get("batch_mode"):
        return "reporter"
    return "supplement_approval"


# ── Node 6：supplement_approval（补充收集审批）────────────────────────────────

_REPORT_WORDS = _CONFIRM_WORDS | {"生成报告", "够了", "可以了", "结束", "report", "done", "总结", "直接总结", "不用补充"}
_COLLECT_WORDS = {"补充", "继续", "再查", "再收集", "collect", "more", "需要补充"}


async def supplement_approval_node(state: AgentState) -> AgentState:
    """
    把模型的资料充分性判断展示给用户，等待审批。

    用户可以回复：
      - "生成报告" / "够了" / "确认" → 进入报告撰写
      - "补充" / "继续"               → 按模型建议的补充计划再收集一轮
      - JSON 数组                     → 用自定义补充计划再收集一轮
      - 自然语言（想补充哪些维度）     → 交给 LLM 解析成补充计划再收集
    """
    supplement_plan = state.get("supplement_plan") or []
    gap = state.get("gap_analysis", "")

    if supplement_plan:
        supp_text = "\n".join(
            f"  - {t['topic']}：{t['description']}" for t in supplement_plan
        )
        suggestion = f"建议补充以下维度：\n{supp_text}"
    else:
        suggestion = "我认为现有资料已基本充分。"

    prompt = (
        f"资料收集完成（第 {state.get('research_round', 1)} 轮）。我的判断：\n"
        f"  {gap}\n\n"
        f"{suggestion}\n\n"
        "请回复「生成报告」直接进入总结，或回复「补充」按上述维度再收集一轮，"
        "也可以直接告诉我还想补充哪些维度。"
    )

    user_response: str = interrupt(prompt)
    stripped = user_response.strip()
    normalized = stripped.lower()

    # 1) 明确要求生成报告，或空回复 → 收尾
    if not stripped or normalized in _REPORT_WORDS:
        return {"supplement_decision": "report"}

    # 2) 明确要求按模型建议补充
    if normalized in _COLLECT_WORDS:
        if supplement_plan:
            return {"supplement_decision": "collect", "supplement_plan": supplement_plan}
        return {"supplement_decision": "report"}  # 无可补充内容则收尾

    # 3) JSON 数组 → 自定义补充计划
    if stripped.startswith("["):
        try:
            items = json.loads(stripped)
            custom = [
                {
                    "id": f"supp{state.get('research_round', 1)}_{i+1}",
                    "topic": item.get("topic", item.get("t", "")),
                    "description": item.get("description", item.get("d", "")),
                }
                for i, item in enumerate(items[:3])
                if item.get("topic", item.get("t", "")) and item.get("description", item.get("d", ""))
            ]
            if custom:
                return {"supplement_decision": "collect", "supplement_plan": custom}
        except Exception:
            pass

    # 4) 自然语言 → 交给 LLM 解析成补充计划
    edit_context = {
        "current_supplement": [
            {"topic": t["topic"], "description": t["description"]} for t in supplement_plan
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
        custom = [
            {
                "id": f"supp{state.get('research_round', 1)}_{i+1}",
                "topic": item.get("topic", item.get("t", "")),
                "description": item.get("description", item.get("d", "")),
            }
            for i, item in enumerate(items[:3])
            if item.get("topic", item.get("t", "")) and item.get("description", item.get("d", ""))
        ]
        if custom:
            return {"supplement_decision": "collect", "supplement_plan": custom}
    except Exception:
        logger.warning("补充计划解析失败，用户输入：%s，模型输出：%s", stripped, raw)

    return {"supplement_decision": "report"}


def _route_after_supplement_approval(state: AgentState):
    # 达到轮数上限强制收尾
    if state.get("research_round", 0) >= MAX_RESEARCH_ROUNDS:
        return "reporter"
    if state.get("supplement_decision") == "collect" and state.get("supplement_plan"):
        return _send_tasks(state, state["supplement_plan"])
    return "reporter"


# ── Node 6：reporter ──────────────────────────────────────────────────────────

_REPORTER_SYSTEM = """\
你是城市治理分析报告撰写助手。
只能基于提供的研究笔记撰写报告，禁止添加未经证实的内容。
对于笔记中未涉及的方面，明确说明"现有证据不足，本报告不作评价"。
报告结构清晰，语言专业简洁，不使用"可能""推测"等模糊表达。

引用规则（务必遵守）：
- 正文引用证据时使用方括号编号 [n]，编号必须对应随附的统一来源列表，不得自创新编号；
- 报告结尾输出一个 `## 来源` 区段，按编号列出所有引用过的来源；
- 若某条来源未在正文中引用，可省略；不要重新编号已存在的来源。
"""


async def reporter_node(state: AgentState) -> AgentState:
    findings = state.get("findings") or []
    if not findings:
        return {
            "report": (
                f"对于{state['location']} {state['start_year']}-{state['end_year']}年的变化，"
                "现有证据不足以支持任何结论，本报告不作评价。"
            )
        }

    # 合并多份研究笔记：按 URL 去重 + 跨 finding 连续编号 + 同步替换正文 [n]
    merged = merge_findings_with_sources(findings)

    resp = await _llm(max_tokens=8192).ainvoke([
        SystemMessage(content=_REPORTER_SYSTEM),
        HumanMessage(content=(
            f"分析对象：{state['location']}\n"
            f"时间范围：{state['start_year']} 至 {state['end_year']}\n\n"
            f"研究笔记（正文 [n] 已统一编号）：\n{merged.body}\n\n"
            f"统一来源列表（正文 [n] 必须对应这里的编号）：\n{merged.sources_md}\n\n"
            "请撰写城市变化分析报告，正文用 [n] 引用来源，报告结尾输出 ## 来源 区段。"
        )),
    ])
    report = resp.content

    # LLM 漏掉来源列表 -> 追加统一列表（不重新编号）
    if merged.sources and not has_source_section(report):
        report = f"{report.rstrip()}\n\n{merged.sources_md}"

    return {"report": report}


# ── 构建图 ────────────────────────────────────────────────────────────────────

def build_main_graph():
    g = StateGraph(AgentState)

    g.add_node("clarify",              clarify_node)
    g.add_node("planner",              planner_node)
    g.add_node("human_approval",       human_approval_node)
    g.add_node("researcher",           researcher_node)
    g.add_node("gap_eval",             gap_eval_node)
    g.add_node("supplement_approval",  supplement_approval_node)
    g.add_node("reporter",             reporter_node)

    g.add_edge(START,            "clarify")
    g.add_edge("clarify",        "planner")
    def _after_planner(state: AgentState):
        if state.get("batch_mode"):
            return dispatch_researchers(state)
        return "human_approval"

    g.add_conditional_edges("planner", _after_planner, ["human_approval", "researcher"])
    # human_approval → researcher×N 并发派发
    g.add_conditional_edges("human_approval", dispatch_researchers, ["researcher"])
    # researcher×N 完成后 → gap_eval 判断资料是否充分
    g.add_edge("researcher",     "gap_eval")
    g.add_conditional_edges("gap_eval", _after_gap_eval, ["supplement_approval", "reporter"])
    # supplement_approval → 补充收集(Send→researcher) 或 进入 reporter
    g.add_conditional_edges(
        "supplement_approval",
        _route_after_supplement_approval,
        ["researcher", "reporter"],
    )
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
        "research_round":      0,
        "gap_analysis":        "",
        "supplement_plan":     [],
        "supplement_decision": "",
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
        "research_round":      0,
        "gap_analysis":        "",
        "supplement_plan":     [],
        "supplement_decision": "",
        "report":         "",
    }
    final = await main_graph.ainvoke(
        initial,
        config={"configurable": {"thread_id": f"{location}_{start_year}_{end_year}_{id(initial)}"}},
    )
    return final["report"]
