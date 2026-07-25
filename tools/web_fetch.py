import io
import logging
import os
import urllib.request

import httpx
from langchain_core.tools import tool
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

_JINA_BASE = "https://r.jina.ai/"
_MAX_CHARS = 4096
_api_key_warned = False


def _get_proxy() -> str | None:
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    if proxy:
        return proxy
    system_proxies = urllib.request.getproxies()
    return system_proxies.get("https") or system_proxies.get("http")


async def _fetch_jina(url: str) -> str:
    global _api_key_warned

    api_key = os.getenv("JINA_API_KEY", "")
    headers = {
        "Content-Type": "application/json",
        "X-Return-Format": "markdown",
        "X-Timeout": "20",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif not _api_key_warned:
        _api_key_warned = True
        logger.warning("JINA_API_KEY 未设置，使用匿名限速模式（20 RPM）。设置后可提升至 500 RPM。")

    proxy = _get_proxy()
    client_kwargs: dict = {}
    if proxy:
        client_kwargs["proxy"] = proxy

    async with httpx.AsyncClient(**client_kwargs) as client:
        resp = await client.post(
            _JINA_BASE,
            headers=headers,
            json={"url": url},
            timeout=20,
        )

    if resp.status_code != 200:
        return f"Error: Jina 返回 {resp.status_code}: {resp.text[:200]}"
    if not resp.text or not resp.text.strip():
        return "Error: Jina 返回空内容"

    return resp.text


async def _fetch_mcp(url: str) -> str:
    """通过本机 mcp-server-fetch 抓取（走本地代理，能访问国内网站）。"""
    server_params = StdioServerParameters(
        command="uvx",
        args=["mcp-server-fetch"],
    )
    async with stdio_client(server_params, errlog=io.StringIO()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("fetch", {"url": url, "max_length": _MAX_CHARS})

    texts = [c.text for c in result.content if hasattr(c, "text")]
    content = "\n".join(texts)
    if not content.strip():
        return "Error: MCP fetch 返回空内容"
    return content


@tool
async def web_fetch(url: str) -> str:
    """抓取指定 URL 的完整网页内容（Markdown 格式）。

    适用场景：web_search 找到相关链接后，用本工具精读全文，获取摘要之外的详细数据、
    政策原文、统计数字等。只抓取 web_search 或用户提供的真实 URL，不要猜测 URL。

    Args:
        url: 需要抓取的网页完整地址，必须包含 https:// 前缀。
    """
    try:
        logger.debug("web_fetch [jina] %s", url)
        content = await _fetch_jina(url)
    except Exception as e:
        logger.warning("web_fetch jina 失败 [%s]: %s: %s，尝试 mcp-fetch", url, type(e).__name__, e)
        content = f"Error: {e}"

    if content.startswith("Error:"):
        logger.info("web_fetch fallback → mcp-fetch [%s]", url)
        try:
            content = await _fetch_mcp(url)
        except Exception as e:
            logger.warning("web_fetch mcp-fetch 也失败 [%s]: %s: %s", url, type(e).__name__, e)
            return f"Error: 抓取失败 — {type(e).__name__}: {e}"

    if content.startswith("Error:"):
        return content

    if len(content) > _MAX_CHARS:
        content = content[:_MAX_CHARS] + f"\n\n…（内容已截断，共 {len(content)} 字符）"

    return content
