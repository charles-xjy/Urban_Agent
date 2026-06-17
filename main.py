import asyncio
import json
import logging
import re
import sys

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("primp").setLevel(logging.WARNING)
logging.getLogger("ddgs").setLevel(logging.WARNING)
logging.getLogger("ddgs.ddgs").setLevel(logging.WARNING)
logging.getLogger("langchain_openai").setLevel(logging.WARNING)

from datetime import datetime
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.types import Command

from config.settings import AGENT_MODEL_NAME, AGENT_MODEL_URL, VLLM_API_KEY
from graph.main_graph import main_graph


# ── 自然语言 → {location, start_year, end_year} ───────────────────────────────

_PARSE_SYSTEM = f"""\
将用户的城市分析请求解析为结构化参数。当前年份：{datetime.now().year}。

时间解析规则：
- "最近N年" / "近N年" → start_year = 当前年 - N
- "N年前和现在"       → start_year = 当前年 - N
- "X年到Y年"          → start_year=X, end_year=Y
- 未提及时间          → start_year = 当前年 - 5

输出严格为 JSON（不含其他文字）：
{{"location": "地点名称", "start_year": 2019, "end_year": 2024}}
"""


async def _parse_query(text: str) -> dict | None:
    model = ChatOpenAI(
        base_url=AGENT_MODEL_URL, api_key=VLLM_API_KEY,
        model=AGENT_MODEL_NAME, temperature=0, max_tokens=128,
    )
    resp = await model.ainvoke([
        SystemMessage(content=_PARSE_SYSTEM),
        HumanMessage(content=text),
    ])
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", resp.content).strip()
    try:
        return json.loads(raw)
    except Exception:
        return None


# ── 流式运行 + 进度打印 ───────────────────────────────────────────────────────

_NODE_LABEL = {
    "clarify":             "澄清问题",
    "planner":             "制定计划",
    "human_approval":      "等待确认",
    "researcher":          "研究员",
    "gap_eval":            "资料审核",
    "supplement_approval": "补充审批",
    "reporter":            "生成报告",
}


async def _stream_run(initial_state: dict, config: dict) -> str:
    """
    流式运行主图，逐节点打印进度。
    遇到 interrupt() 暂停点时提示用户输入，然后 resume 继续。
    返回最终报告字符串。
    """
    report = ""
    input_or_command = initial_state

    while True:
        interrupted = False
        interrupt_prompt = ""

        async for chunk in main_graph.astream(
            input_or_command, config, stream_mode="updates"
        ):
            # interrupt 事件
            if "__interrupt__" in chunk:
                interrupted = True
                interrupt_prompt = chunk["__interrupt__"][0].value
                break

            for node_name, updates in chunk.items():
                label = _NODE_LABEL.get(node_name, node_name)

                if node_name == "planner":
                    plan = updates.get("plan", [])
                    print(f"\n[{label}] 已生成 {len(plan)} 个研究方向")

                elif node_name == "researcher":
                    findings_list = updates.get("findings", [])
                    if findings_list:
                        finding = findings_list[-1]
                        # 取第一行作为标题
                        title = finding.splitlines()[0] if finding else ""
                        print(f"\n[{label}完成] {title}")
                        print("-" * 40)
                        print(finding.strip())
                        print("-" * 40)

                elif node_name == "gap_eval":
                    gap = updates.get("gap_analysis", "")
                    supp = updates.get("supplement_plan", [])
                    print(f"\n[{label}] 第 {updates.get('research_round', '?')} 轮 — {gap}")
                    if supp:
                        print(f"[{label}] 建议补充 {len(supp)} 个维度")

                elif node_name == "reporter":
                    report = updates.get("report", "")
                    print(f"\n[{label}] 完成")

                elif node_name == "clarify":
                    needed = updates.get("clarify_needed", False)
                    if not needed:
                        print(f"[{label}] 问题清晰，无需追问")

        if not interrupted:
            break

        # 用户交互（clarify 或 human_approval）
        print(f"\n[系统] {interrupt_prompt}")
        user_reply = input("你的回复：").strip()
        input_or_command = Command(resume=user_reply)

    return report


# ── 主循环 ────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("  城市变化研究智能体")
    print("=" * 60)
    print("输入分析请求，例如：雄安新区 2018 到 2024 年的城市变化")
    print("输入 q 退出")
    print("=" * 60)

    while True:
        try:
            query = input("\n请输入分析请求：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出")
            break

        if not query or query.lower() in ("q", "quit", "exit", "退出"):
            break

        parsed = await _parse_query(query)
        if not parsed:
            print("[错误] 无法解析地点或时间范围，请重新输入")
            continue

        location   = parsed["location"]
        start_year = int(parsed["start_year"])
        end_year   = int(parsed["end_year"])
        print(f"\n解析结果：{location}  {start_year} → {end_year}")

        initial_state = {
            "user_input":     query,
            "location":       location,
            "start_year":     start_year,
            "end_year":       end_year,
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

        config = {"configurable": {"thread_id": f"run_{location}_{start_year}"}}
        report = await _stream_run(initial_state, config)
        if report:
            print("\n" + "=" * 60)
            print("【最终报告】")
            print("=" * 60)
            print(report)
        else:
            print("[错误] 未生成报告")


if __name__ == "__main__":
    asyncio.run(main())
