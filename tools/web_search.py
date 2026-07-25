import asyncio
import json
import logging
from urllib.parse import urlparse

from langchain_core.tools import tool

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

# 域名权威性评分规则：(score, label)
_DOMAIN_RULES: list[tuple[list[str], float, str]] = [
    (["stats.gov.cn", "data.stats.gov.cn"],                          1.0, "国家统计局"),
    (["gov.cn"],                                                      0.95, "政府官网"),
    (["xinhuanet.com", "people.com.cn", "chinadaily.com.cn",
      "cctv.com", "gmw.cn"],                                         0.85, "官方媒体"),
    ([".edu.cn", ".ac.cn", "cnki.net", "wanfangdata.com.cn"],        0.80, "学术机构"),
    (["sina.com.cn", "sohu.com", "163.com", "qq.com",
      "toutiao.com", "baidu.com"],                                   0.45, "门户/聚合"),
]
_DEFAULT_SCORE = (0.40, "普通网站")


def _score_domain(url: str) -> tuple[float, str]:
    if not url:
        return 0.30, "无链接"
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return 0.30, "无链接"
    for patterns, score, label in _DOMAIN_RULES:
        if any(p in host for p in patterns):
            return score, label
    return _DEFAULT_SCORE


def _sync_search(query: str, max_results: int) -> list[dict]:
    with DDGS(timeout=30) as ddgs:
        return list(ddgs.text(
            query,
            region="cn-zh",
            safesearch="moderate",
            max_results=max_results,
        ))


@tool
async def web_search(query: str, max_results: int = 5) -> str:
    """搜索网页文字证据。

    适用场景：建立背景知识、获取政策/新闻/统计报道、验证事件是否真实发生。
    建议先调用本工具获取文字背景，再决定是否需要卫星图或 POI 数据。
    搜到相关结果后，用 web_fetch 抓取完整页面内容以获取更多细节。

    Args:
        query: 搜索关键词，控制在 4-6 个词以内，不要写完整句子。
               可加机构名提升相关性："雄安新区住宅建设 住建部 2024"
               正确示例："雄安新区住宅建设 2024"  "白洋淀水质 统计公报"
               错误示例："雄安新区容东片区从2018年到2024年住宅建设情况如何"
        max_results: 最多返回结果数，默认 5
    """
    try:
        results = await asyncio.to_thread(_sync_search, query, max_results)
    except Exception as e:
        logger.warning("DDGS 搜索失败: %s", e)
        return json.dumps({"error": str(e), "query": query}, ensure_ascii=False)

    if not results:
        return json.dumps({"error": "未找到相关结果", "query": query}, ensure_ascii=False)

    normalized = []
    for r in results:
        url = r.get("href", r.get("link", ""))
        score, label = _score_domain(url)
        normalized.append({
            "title":           r.get("title", ""),
            "url":             url,
            "snippet":         r.get("body", r.get("snippet", "")),
            "source_score":    score,
            "source_label":    label,
        })

    normalized.sort(key=lambda x: x["source_score"], reverse=True)

    return json.dumps(
        {"query": query, "total": len(normalized), "results": normalized},
        ensure_ascii=False,
        indent=2,
    )
