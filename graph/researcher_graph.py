"""
Researcher 子图

每个实例聚焦一个子任务，通过 ReAct 循环自主调用工具，
证据足够后返回 findings 字符串写回主图。
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from config.settings import AGENT_MODEL_NAME, AGENT_MODEL_URL, VLLM_API_KEY
from graph.state import ResearcherState
from tools.poi import query_poi_history
from tools.satellite import analyze_satellite_image
from tools.web_search import web_search

logger = logging.getLogger(__name__)

# ── 工具集 ────────────────────────────────────────────────────────────────────

RESEARCHER_TOOLS = [web_search, analyze_satellite_image, query_poi_history]

# ── System Prompt ─────────────────────────────────────────────────────────────

_RESEARCHER_SYSTEM = """\
你是城市变化研究员。你负责针对一个具体的研究子任务，收集多源证据后给出研究结论。

## 工具使用策略

**web_search** — 先用，建立背景
  - 用于：获取政策信息、新闻报道、政府数据、统计年鉴
  - 关键词严格控制在 4-6 个词，不写完整句子
  - 正确："雄安新区住宅 2024"  "白洋淀水质改善"
  - 错误："雄安新区容东片区2018年到2024年住宅建设情况"（太长，搜索效果差）
  - 每次调用只搜一个聚焦方向，需要多个方向时分多次调用
  - **严格限制**：同一个方向最多搜索 3 次，无论换什么关键词变体。超过 3 次仍找不到，立即放弃，在结论中注明"该数据暂无公开来源"

**analyze_satellite_image** — 文字证据提到空间/物理变化时调用
  - 用于：视觉确认建筑密度、植被消失、水体变化、道路扩张
  - 注意：返回的是像素级观察，需结合其他证据才能得出结论
  - 不要为了用而用，文字证据已充分时可跳过

**query_poi_history** — 有定性结论需要量化支撑时调用
  - 用于：建了多少建筑、道路增加了多少、医院/学校有没有新增
  - 可用类别：building / road_primary / road_secondary / hospital /
              school / residential / commercial / park / water / industrial
  - OSM 中国覆盖率有限，返回 0 时改用 web_search

## 证据置信度原则

三路证据（文字 + 视觉 + 量化）互相印证 → 置信度高，明确陈述
两路证据支撑                           → 置信度中，正常陈述
单路证据                               → 置信度低，需标注"仅有文字/视觉/量化证据支持"
无证据支撑                             → 不写入结论，说明"该方向证据不足"

## 输出格式

完成研究后，以以下格式输出你的研究结论（这将作为最终报告的原材料）：

【研究结论：{子任务名称}】
[按条列出每个有证据支撑的发现，注明证据来源和置信度]

【证据摘要】
- 文字证据：[关键来源和内容]
- 视觉证据：[卫星图观察，若调用了的话]
- 量化数据：[POI 数量变化，若调用了的话]

【不确定或证据不足的方面】
[列出你希望研究但证据不足的内容]
"""

# ── Researcher Agent ──────────────────────────────────────────────────────────

def _make_researcher() -> object:
    model = ChatOpenAI(
        base_url=AGENT_MODEL_URL,
        api_key=VLLM_API_KEY,
        model=AGENT_MODEL_NAME,
        temperature=0,
        max_tokens=8192,
    )
    return create_react_agent(
        model=model,
        tools=RESEARCHER_TOOLS,
        prompt=_RESEARCHER_SYSTEM,
    )


# ── 节点函数（接入 main_graph 的 researcher 节点）────────────────────────────

async def researcher_node(state: ResearcherState) -> dict:
    """
    单个 Researcher 实例的执行节点。
    接收 ResearcherState，运行 ReAct 循环，返回 findings 追加到主图。
    """
    task = state["task"]
    location = state["location"]
    start_year = state["start_year"]
    end_year = state["end_year"]

    logger.info("Researcher 启动 | %s — %s", task["id"], task["topic"])

    query = (
        f"研究任务：{task['topic']}\n"
        f"具体内容：{task['description']}\n"
        f"分析地点：{location}\n"
        f"时间范围：{start_year} 年 → {end_year} 年\n\n"
        "请开始收集证据，完成后给出研究结论。"
    )

    agent = _make_researcher()
    findings = ""
    try:
        async for event in agent.astream_events(
            {"messages": [HumanMessage(content=query)]},
            config={"recursion_limit": 80},
            version="v2",
        ):
            kind = event["event"]
            name = event.get("name", "")

            if kind == "on_tool_start":
                tool_input = event.get("data", {}).get("input", {})
                if name == "web_search":
                    query_hint = tool_input.get("query", "")[:50]
                    print(f"  [{task['topic']}] 正在搜索：{query_hint}")
                elif name in ("analyze_satellite_image", "query_poi_history"):
                    hint = next(iter(tool_input.values()), "")
                    print(f"  [{task['topic']}] → {name}({hint})")

            elif kind == "on_tool_end":
                output = event.get("data", {}).get("output", "")
                # output 可能是 ToolMessage 对象，取 .content；否则直接转字符串
                if hasattr(output, "content"):
                    output_str = output.content
                else:
                    output_str = str(output)
                if name == "web_search":
                    print(f"  [{task['topic']}] 搜索完成")
                elif name in ("analyze_satellite_image", "query_poi_history"):
                    print(f"  [{task['topic']}] ← {name} 结果：")
                    print(output_str)
                    print()

            elif kind == "on_chat_model_end":
                msg = event.get("data", {}).get("output")
                if msg and hasattr(msg, "content") and msg.content:
                    # 只保留没有 tool_calls 的纯文本回复作为 findings 候选
                    # 有 tool_calls 的是工具调用决策，不是最终总结
                    tool_calls = getattr(msg, "tool_calls", [])
                    if not tool_calls:
                        findings = msg.content

    except Exception as e:
        logger.error("Researcher 执行失败 | %s: %s", task["id"], e)
        findings = f"[{task['topic']}] 研究过程出错：{e}"

    logger.info("Researcher 完成 | %s | findings 长度=%d", task["id"], len(findings))

    # operator.add 会把列表追加，所以返回单元素列表
    return {"findings": [f"=== {task['topic']} ===\n{findings}"]}
