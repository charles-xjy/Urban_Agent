import asyncio
import logging

from langchain_core.tools import tool

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)


def _sync_search(query: str, max_results: int) -> list[dict]:
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


@tool
async def web_search(query: str, max_results: int = 5) -> str:
    """搜索网页文字证据。

    适用场景：建立背景知识、获取政策/新闻/统计报道、验证事件是否真实发生。
    建议先调用本工具获取文字背景，再决定是否需要卫星图或 POI 数据。

    Args:
        query: 搜索关键词，控制在 4-6 个词以内，不要写完整句子。
               正确示例："雄安新区住宅建设 2024"
               错误示例："雄安新区容东片区从2018年到2024年住宅建设情况如何"
        max_results: 最多返回结果数，默认 5
    """
    try:
        results = await asyncio.to_thread(_sync_search, query, max_results)
    except Exception as e:
        logger.warning("DDGS 搜索失败: %s", e)
        return f"搜索失败：{e}"

    if not results:
        return "未找到相关结果。"

    lines = [f"【{r['title']}】\n{r['body']}" for r in results]
    return "\n\n---\n\n".join(lines)
