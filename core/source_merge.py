"""
搜索报告来源编号与正文引用合并工具。

解决的问题（详见 skill_aicoding/REPORT_SOURCE_NUMBERING.md）：
- 多个搜索报告各自从 [1] 开始编号，合并后编号重复；
- 正文 [n] 引用与最终来源列表对不上。

做法：在“最终报告合并节点”按 URL 去重 + 连续编号 + 同步替换正文 [n]，
前端只做展示兜底，不重新编号。

本模块为纯函数，无外部依赖，可独立测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Source:
    """单条来源。num 为当前报告内的编号。"""
    num: int
    title: str
    url: str


@dataclass
class MergedReport:
    """合并结果。"""
    body: str                                  # 拼接后的正文（已剥离各 finding 的 ## 来源 区段，[n] 已重编号）
    sources_md: str                            # "## 来源\n\n- [1] ..." 统一来源列表
    sources: list[tuple[int, str, str]]        # (num, title, url)


# ── 正则 ────────────────────────────────────────────────────────────────────

# ## 来源 区段起始行：兼容 "## 来源" / "##来源" / "## 来源：" / 尾随空白，但不匹配 "## 来源分析"
_SOURCE_HEADING_RE = re.compile(r"^#{1,6}\s*来源\s*:?\s*$", re.MULTILINE)
# 来源区段结束：下一个 ## 标题
_NEXT_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
# [n] 编号标记
_NUM_TAG_RE = re.compile(r"\[(\d+)\]")
# URL
_URL_RE = re.compile(r"https?://\S+")


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _normalize_url(url: str) -> str:
    """URL 归一化用于去重：去空白、去尾斜杠、scheme 与 host 小写。"""
    url = url.strip().rstrip("/")
    m = re.match(r"^(https?://)([^/]+)(.*)$", url, re.IGNORECASE)
    if m:
        url = m.group(1).lower() + m.group(2).lower() + m.group(3)
    return url


def _split_source_section(text: str) -> tuple[str, Optional[str]]:
    """
    把报告拆为 (正文, 来源区段原文)。
    来源区段从 "## 来源" 行开始，到下一个同级或更高级标题或 EOF。
    无来源区段时返回 (text, None)。
    """
    m = _SOURCE_HEADING_RE.search(text)
    if not m:
        return text, None
    body = text[:m.start()].rstrip()
    rest = text[m.end():]
    nxt = _NEXT_HEADING_RE.search(rest)
    if nxt:
        sources_text = rest[: nxt.start()]
        tail = rest[nxt.start():]
        # tail 是来源之后的章节，拼回正文
        return (body + "\n\n" + tail.strip()).strip() if tail else body, sources_text
    return body, rest


def _parse_sources(sources_text: str) -> list[Source]:
    """
    从来源区段文本解析 Source 列表。
    在每个 [n] 边界切分，因此同时支持「每条独占一行」和「多条挤在一行」两种写法。
    """
    sources: list[Source] = []
    for chunk in re.split(r"(?=\[\d+\])", sources_text):
        chunk = chunk.strip()
        if not chunk:
            continue
        m_num = _NUM_TAG_RE.search(chunk)
        url_m = _URL_RE.search(chunk)
        if not m_num or not url_m:
            continue
        num = int(m_num.group(1))
        url = url_m.group(0).rstrip(".,;，。；)]")
        after_num = chunk[m_num.end():]
        url_pos = after_num.find(url)
        title = after_num[:url_pos] if url_pos != -1 else after_num
        title = title.strip(" \t\n\r-–—•·*、:")
        sources.append(Source(num=num, title=title, url=url))
    return sources


def _format_source(num: int, title: str, url: str) -> str:
    return f"- [{num}] {title} - {url}" if title else f"- [{num}] {url}"


# ── 公开接口 ──────────────────────────────────────────────────────────────────

def _renumber_search_report(text: str, offset: int = 0) -> tuple[str, int]:
    """
    重编号单个搜索报告：按 URL 去重，分配 offset+1.. 连续新编号，
    同步替换正文与来源行里的 [n]。

    返回 (重编号后的报告, 本报告唯一来源数量)。
    无 ## 来源 区段时原样返回 (text, 0)（向后兼容旧 findings）。
    """
    body, sources_text = _split_source_section(text)
    if sources_text is None:
        return text, 0

    sources = _parse_sources(sources_text)

    # URL 去重 -> 新编号；保留首条标题；建立 orig_num -> new_num 映射
    url_to_new: dict[str, int] = {}
    ordered: list[tuple[int, str, str]] = []   # (new_num, title, url)
    orig_to_new: dict[int, int] = {}
    for s in sources:
        key = _normalize_url(s.url)
        if key not in url_to_new:
            new_num = offset + len(ordered) + 1
            url_to_new[key] = new_num
            ordered.append((new_num, s.title, s.url))
        orig_to_new[s.num] = url_to_new[key]

    # 替换正文 [n]（仅已知 orig num；未知的 [n] 原样保留，避免误伤代码块等）
    def _repl(m: re.Match) -> str:
        n = int(m.group(1))
        return f"[{orig_to_new[n]}]" if n in orig_to_new else m.group(0)

    new_body = _NUM_TAG_RE.sub(_repl, body)

    # 重建来源区段
    lines = ["## 来源", ""]
    for new_num, title, url in ordered:
        lines.append(_format_source(new_num, title, url))
    new_sources = "\n".join(lines)

    sep = "\n\n" if new_body else ""
    return f"{new_body}{sep}{new_sources}", len(ordered)


def merge_findings_with_sources(findings: list[str]) -> MergedReport:
    """
    合并多个 finding：
    1. 顺序对每个 finding 调用 _renumber_search_report，offset 按「唯一来源数」累计；
    2. 剥离各 finding 的 ## 来源 区段，正文用 \\n\\n---\\n\\n 拼接；
    3. 收集统一来源列表（编号跨 finding 连续）。

    返回 MergedReport(body, sources_md, sources)。
    """
    offset = 0
    bodies: list[str] = []
    unified: list[tuple[int, str, str]] = []
    for f in findings or []:
        if not f or not f.strip():
            continue
        renumbered, n = _renumber_search_report(f, offset)
        body, sources_text = _split_source_section(renumbered)
        body = body.strip()
        if body:
            bodies.append(body)
        if sources_text:
            for s in _parse_sources(sources_text):
                unified.append((s.num, s.title, s.url))
        offset += n

    lines = ["## 来源", ""]
    for num, title, url in unified:
        lines.append(_format_source(num, title, url))
    sources_md = "\n".join(lines)

    return MergedReport(
        body="\n\n---\n\n".join(bodies),
        sources_md=sources_md,
        sources=unified,
    )


def has_source_section(text: str) -> bool:
    """报告是否已包含 ## 来源 区段。"""
    return bool(_SOURCE_HEADING_RE.search(text or ""))
