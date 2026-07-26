"""
Researcher 子图

每个实例聚焦一个子任务，通过 ReAct 循环自主调用工具，
证据足够后返回 findings 字符串写回主图。
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

import config.settings as settings
from graph.state import ResearcherState
from tools.poi import query_poi_history
from tools.satellite import analyze_satellite_image
from tools.web_fetch import web_fetch
from tools.web_search import web_search

logger = logging.getLogger(__name__)

# ── 工具集 ────────────────────────────────────────────────────────────────────

RESEARCHER_TOOLS = [web_search, web_fetch, analyze_satellite_image, query_poi_history]
MAX_TOOL_CALLS = 30

# ── System Prompt ─────────────────────────────────────────────────────────────

_RESEARCHER_SYSTEM = """\
你是城市变化研究员。你负责针对一个具体的研究子任务，收集多源证据后给出研究结论。

## 工具使用策略

**第一步：web_search** — 宽泛探索，建立背景
  - 关键词 4-6 个词，不写完整句子
  - 加机构名可显著提升结果相关性，优先尝试以下来源：
      政策法规 → 加"住建部" / "自然资源部" / "发改委"
      统计数据 → 加"统计年鉴" / "统计公报" / "普查"
      规划文件 → 加"国土空间规划" / "控制性详规"
  - 示例好查询："雄安新区住宅 住建部 2024"  "白洋淀水质 统计公报"
  - 示例差查询："雄安新区容东片区2018年到2024年住宅建设情况"（太长）
  - 每次只搜一个聚焦方向，每个方向最多 3 次，无论换什么变体
  - 3 次仍找不到 → 立即放弃，结论中注明"该数据暂无公开来源"

**第二步：web_fetch** — 精读搜索结果中的重要链接
  - 何时调用：web_search 返回结果后，看到来源域名权威（gov.cn、stats.gov.cn、规划局官网）
    或摘要提到了具体数字/政策名称，立即用 web_fetch 抓取该 URL 的完整内容
  - 作用：摘要只有 200 字，全文可能有详细数据表格、政策原文、具体年份数字
  - 不要对无关链接或社交媒体链接调用

**analyze_satellite_image** — 文字证据提到空间/物理变化时调用
  - 用于：视觉确认建筑密度、植被消失、水体变化、道路扩张
  - 返回像素级观察，需结合其他证据才能得出结论
  - 文字证据已充分时可跳过

**query_poi_history** — 有定性结论需要量化支撑时调用
  - 用于：建了多少建筑、道路增加了多少、医院/学校有没有新增
  - 可用类别：building / road_primary / road_secondary / hospital /
              school / residential / commercial / park / water / industrial
  - OSM 中国覆盖率有限，返回 0 时改用 web_search

## 搜索失败换词策略

同一方向搜索失败后按顺序尝试：
1. 换机构词：gov.cn → 住建部 → 地方政府官网
2. 换粒度：整体地区 → 具体片区
3. 换时间表达：精确年份 → 时间段（"十三五期间" / "2015-2020"）
4. 换同义词：学术词 → 政策词（"生态修复" → "生态文明建设"）

## 证据置信度原则

三路证据（文字 + 视觉 + 量化）互相印证 → 置信度高，明确陈述
两路证据支撑                           → 置信度中，正常陈述
单路证据                               → 置信度低，需标注"仅有文字/视觉/量化证据支持"
无证据支撑                             → 不写入结论，说明"该方向证据不足"

## 证据冲突处理

当两路证据得出相反结论时（如文字报道绿化增加、卫星图显示植被减少）：
- 不得直接采信任意一路，不得对矛盾结论取平均
- 按以下顺序尝试消解冲突：
    1. 再次 web_search，关键词加"官方数据"或具体统计机构名，寻找第三方裁定来源
    2. 调用 query_poi_history 获取量化数据，作为独立仲裁证据
    3. 再次调用 analyze_satellite_image，换时间节点或更小区域重新观测
- 若经过上述三步仍无法消解冲突，**立即停止收集，不再继续循环**
  在结论中写明：
    【冲突未解决】证据存在冲突，暂无法定论
    - 支持"[结论A]"的证据：[来源及内容]
    - 支持"[结论B]"的证据：[来源及内容]
- 累计工具调用次数超过 30 次仍无法消解冲突，视同冲突无法消解，
  直接输出上述冲突声明，禁止继续调用工具（初始 query 中会告知当前已用轮数）
- 禁止在证据冲突未解决时给出确定性结论

## 输出格式

完成研究后，以以下格式输出你的研究结论（这将作为最终报告的原材料）：

【研究结论：{子任务名称}】
[按条列出每个有证据支撑的发现，每条结论末尾用 [n] 注明对应来源编号，并标注置信度]
示例：
- 容东片区住宅建设持续推进[1]，置信度：中
- 白洋淀水质提升至Ⅲ类[2]，置信度：高

【证据摘要】
- 文字证据：[关键来源和内容]
- 视觉证据：[卫星图观察，若调用了的话]
- 量化数据：[POI 数量变化，若调用了的话]

【不确定或证据不足的方面】
[列出你希望研究但证据不足的内容]

## 来源

- [1] 官方公告标题 - https://example.com/a
- [2] 政策解读标题 - https://example.com/b

来源编号规则（务必遵守，否则最终报告的引用会对不上）：
- 编号仅在当前研究结论内部使用，从 [1] 开始；
- 上面【研究结论】正文里的 [n] 必须对应「## 来源」列表里的第 n 条；
- 同一 URL 只列一次，重复引用时复用同一编号；
- URL 必须完整保留（含 https://），不要省略或猜测；
- 每条来源独占一行，使用 Markdown 列表（`- [n] 标题 - URL`）。
"""

# ── Researcher Agent ──────────────────────────────────────────────────────────

def _make_researcher() -> object:
    model = ChatOpenAI(
        base_url=settings.AGENT_MODEL_URL,
        api_key=settings.VLLM_API_KEY,
        model=settings.AGENT_MODEL_NAME,
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

    runtime_offset: int = state.get("runtime", 0)
    if runtime_offset >= MAX_TOOL_CALLS:
        findings = (
            f"【冲突未解决】工具调用次数已达到 {MAX_TOOL_CALLS} 次，"
            "为避免无限搜索循环，停止继续收集证据，暂无法定论。"
        )
        return {"findings": [f"=== {task['topic']} ===\n{findings}"], "runtime": 0}

    query = (
        f"研究任务：{task['topic']}\n"
        f"具体内容：{task['description']}\n"
        f"分析地点：{location}\n"
        f"时间范围：{start_year} 年 → {end_year} 年\n"
        f"当前已累计工具调用次数：{runtime_offset}（达到 {MAX_TOOL_CALLS} 次未定论须输出冲突声明）\n\n"
        "请开始收集证据，完成后给出研究结论。"
    )

    agent = _make_researcher()
    findings = ""
    tool_call_count = 0
    try:
        async for event in agent.astream_events(
            {"messages": [HumanMessage(content=query)]},
            config={"recursion_limit": 150},
            version="v2",
        ):
            kind = event["event"]
            name = event.get("name", "")

            if kind == "on_tool_start":
                if runtime_offset + tool_call_count >= MAX_TOOL_CALLS:
                    findings = (
                        f"【冲突未解决】工具调用次数已达到 {MAX_TOOL_CALLS} 次，"
                        "为避免无限搜索循环，停止继续收集证据，暂无法定论。"
                    )
                    logger.warning(
                        "Researcher 工具调用达到上限 | %s | limit=%d",
                        task["id"],
                        MAX_TOOL_CALLS,
                    )
                    break

                tool_call_count += 1
                tool_input = event.get("data", {}).get("input", {})
                if name == "web_search":
                    query_hint = tool_input.get("query", "")[:50]
                    print(f"  [{task['topic']}] 正在搜索（第{runtime_offset + tool_call_count}轮）：{query_hint}")
                elif name in ("analyze_satellite_image", "query_poi_history"):
                    hint = next(iter(tool_input.values()), "")
                    print(f"  [{task['topic']}] → {name}({hint})（第{runtime_offset + tool_call_count}轮）")

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

    logger.info("Researcher 完成 | %s | findings 长度=%d | 本次工具调用=%d", task["id"], len(findings), tool_call_count)

    return {
        "findings": [f"=== {task['topic']} ===\n{findings}"],
        "runtime": tool_call_count,
    }
