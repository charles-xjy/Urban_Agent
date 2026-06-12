import operator
from typing import Annotated
from typing_extensions import TypedDict


class ResearchTask(TypedDict):
    id: str        # "task_1"
    topic: str     # "生态环境变化"
    description: str  # "植被、水体、绿地的面积与质量变化"


class AgentState(TypedDict):
    # ── 输入 ──────────────────────────────────────────────────────────────────
    user_input: str
    location: str
    start_year: int
    end_year: int
    batch_mode: bool              # True 时跳过 clarify/human_approval，用于批量实验
    # ── clarify ───────────────────────────────────────────────────────────────
    clarify_needed: bool         # 是否需要追问
    clarify_answer: str          # 用户回答（空串表示未追问）
    # ── planner ───────────────────────────────────────────────────────────────
    plan: list[ResearchTask]     # Planner 初稿，用户可修改
    # ── researcher（并发写入，用 operator.add 追加）──────────────────────────
    findings: Annotated[list[str], operator.add]
    # ── 最终 ──────────────────────────────────────────────────────────────────
    report: str


class ResearcherState(TypedDict):
    """单个 Researcher 子图的独立状态。"""
    task: ResearchTask
    location: str
    start_year: int
    end_year: int
    findings: str
    runtime: Annotated[int, operator.add]  # 累计工具调用次数，每次 on_tool_end +1
