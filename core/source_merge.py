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

import difflib
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

# 来源区段起始行：兼容 Markdown 标题、旧版方头括号标题和纯文本标题，
# 但不匹配正文中的“来源分析”等普通短语。
_SOURCE_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s*来源\s*[:：]?\s*|【\s*来源\s*】\s*|来源\s*[:：]?\s*)$",
    re.MULTILINE,
)
# 来源区段结束：下一个 ## 标题
_NEXT_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
# [n] 编号标记
_NUM_TAG_RE = re.compile(r"\[(\d+)\]")
# URL
_URL_RE = re.compile(r"https?://\S+")
_BARE_DOMAIN_RE = re.compile(
    r"(?<![@\w])((?:[\w-]+\.)+[A-Za-z]{2,}(?:/[^\s，。；、)]*)?)",
    re.IGNORECASE,
)

# 研究员有时会写出正确的来源机构和证据摘要，却漏掉 URL。这里仅把明确的
# 机构名用于候选链接加权；最终仍会结合页面标题、摘要和搜索词判断。
_PROVIDER_DOMAINS: dict[str, tuple[str, ...]] = {
    "教育部": ("moe.gov.cn",),
    "人民网": ("people.com.cn",),
    "人民日报": ("people.com.cn", "paper.people.com.cn"),
    "新华网": ("xinhuanet.com", "news.cn"),
    "央广网": ("cnr.cn",),
    # bjnews.com.cn 是《新京报》，不能作为《北京日报》的域名命中。
    "北京日报": ("beijingdaily.com.cn", "bjd.com.cn"),
    "北邮官网": ("bupt.edu.cn",),
    "北京邮电大学官网": ("bupt.edu.cn",),
    "信息化技术中心": ("nic.bupt.edu.cn",),
    "政府采购网": ("ccgp.gov.cn",),
    "北京市科委": ("kw.beijing.gov.cn",),
    "科委官网": ("kw.beijing.gov.cn",),
    "知乎": ("zhihu.com",),
    "泰伯网": ("taibo.cn",),
    "新浪科技": ("sina.com.cn",),
    "高考直通车": ("gaokaozhitongche.com",),
    "CERNET": ("cernet.edu.cn", "edu.cn"),
}


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
        if not m_num:
            continue
        num = int(m_num.group(1))
        after_num = chunk[m_num.end():]
        # 只看编号所在行：坏格式笔记会在 [n] 后拖出大段正文，
        # 若全量解析会把正文吞进 title/url，污染最终来源列表。
        first_line = after_num.split("\n", 1)[0]
        url_m = _URL_RE.search(first_line)
        url = url_m.group(0).rstrip(".,;，。；)]") if url_m else ""
        url_pos = first_line.find(url) if url else -1
        title = first_line[:url_pos] if url_pos != -1 else first_line
        title = title.strip(" \t\r-–—•·*、:：。，,；;.")
        if not url and not re.search(r"[\w\u4e00-\u9fff]", title):
            # 「- [1] 。」这类编号后只剩标点的行不是真实来源
            continue
        if not url and re.match(r"^置信度\s*[:：（(]", title):
            # 「- [n] 置信度：高」是结论条目残片，不是来源
            continue
        if not title and not url:
            continue
        sources.append(Source(num=num, title=title, url=url))
    return sources


def _format_source(num: int, title: str, url: str) -> str:
    if title and url:
        return f"- [{num}] {title} - {url}"
    if title:
        return f"- [{num}] {title}"
    return f"- [{num}] {url}"


def _compact_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalized_match_text(value: object) -> str:
    return "".join(
        re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", str(value or ""))
    ).casefold()


def _match_grams(value: object) -> set[str]:
    text = _normalized_match_text(value)
    grams = {text[i:i + 2] for i in range(max(0, len(text) - 1))}
    grams.update(re.findall(r"[a-z]+|\d{4}|\d+(?:\.\d+)?g", text))
    return grams


def _citation_context(text: str, num: int) -> str:
    """提取 [num] 所在的短句，避免同一段多个引用互相干扰。"""
    contexts: list[str] = []
    for match in re.finditer(rf"\[{num}\]", text):
        before = text[max(0, match.start() - 180):match.start()]
        after = text[match.end():min(len(text), match.end() + 80)]
        before = re.split(r"[。！？\n]", before)[-1]
        after = re.split(r"[。！？\n]", after)[0]
        context = _compact_text(f"{before} [{num}] {after}")
        if context and context not in contexts:
            contexts.append(context)
    return " ".join(contexts)


def _evidence_source_hints(text: str) -> dict[int, str]:
    """
    兼容研究员偶尔漏写 ``## 来源``、只在 ``【证据摘要】`` 中列出处的情况。
    支持 ``[7][8] 同一组摘要``，两个编号都会得到该提示。
    """
    evidence_start = re.search(
        r"(?:【\s*证据摘要\s*】|^#{1,6}\s*证据摘要\s*$)",
        text,
        re.MULTILINE,
    )
    if not evidence_start:
        return {}

    remainder = text[evidence_start.end():]
    evidence_end = re.search(
        r"(?:【\s*(?:不确定|证据不足)[^】]*】|^#{1,6}\s+\S)",
        remainder,
        re.MULTILINE,
    )
    section = remainder[:evidence_end.start()] if evidence_end else remainder

    hints: dict[int, str] = {}
    for line in section.splitlines():
        match = re.match(
            r"^\s*[-*]\s*((?:\[\d+\]\s*)+)(.+?)\s*$",
            line,
        )
        if not match:
            continue
        description = _compact_text(match.group(2))
        for raw_num in _NUM_TAG_RE.findall(match.group(1)):
            hints[int(raw_num)] = description
    return hints


def _search_candidates(search_groups: list[dict]) -> list[dict[str, str]]:
    """把多轮 web_search 结果按 URL 去重，并保留该 URL 出现过的搜索词。"""
    by_url: dict[str, dict[str, str]] = {}
    for group in search_groups or []:
        if not isinstance(group, dict):
            continue
        query = _compact_text(group.get("query"))
        for item in group.get("results") or []:
            if not isinstance(item, dict):
                continue
            url = _compact_text(item.get("url"))
            if not re.match(r"^https?://", url, re.IGNORECASE):
                continue
            key = _normalize_url(url)
            candidate = by_url.get(key)
            if candidate is None:
                candidate = {
                    "title": _compact_text(item.get("title")),
                    "url": url,
                    "snippet": _compact_text(item.get("snippet")),
                    "queries": query,
                }
                by_url[key] = candidate
            elif query and query not in candidate["queries"]:
                candidate["queries"] = _compact_text(
                    f"{candidate['queries']} {query}"
                )
    return list(by_url.values())


def _candidate_score(
    hint: str,
    focus: str,
    candidate: dict[str, str],
) -> tuple[float, bool, float]:
    """
    返回 (排序分数, 是否命中明确的来源机构域名, 页面内容相关性)。

    ``hint`` 包含来源机构描述，``focus`` 只取当前编号所在短句。这样像
    ``[13][14] 新华网/央广网`` 这种合并摘要不会把两个不同事实误配到同一页。
    """
    focus_grams = _match_grams(focus or hint)
    title_grams = _match_grams(candidate["title"])
    snippet_grams = _match_grams(candidate["snippet"])
    query_grams = _match_grams(candidate["queries"])

    title_dice = (
        2 * len(focus_grams & title_grams) / (len(focus_grams) + len(title_grams))
        if focus_grams and title_grams
        else 0
    )
    snippet_cover = (
        len(focus_grams & snippet_grams) / len(focus_grams)
        if focus_grams
        else 0
    )
    query_cover = (
        len(focus_grams & query_grams) / len(focus_grams)
        if focus_grams
        else 0
    )

    normalized_focus = _normalized_match_text(focus or hint)
    normalized_title = _normalized_match_text(candidate["title"])
    longest = difflib.SequenceMatcher(
        None,
        normalized_focus,
        normalized_title,
    ).find_longest_match().size
    longest_ratio = longest / max(1, min(len(normalized_title), 45))

    content_score = (
        title_dice * 70
        + snippet_cover * 25
        + longest_ratio * 35
    )
    score = content_score + query_cover * 18

    hint_years = set(re.findall(r"20\d{2}", focus or hint))
    candidate_years = set(
        re.findall(
            r"20\d{2}",
            f"{candidate['title']} {candidate['snippet']}",
        )
    )
    score += 6 * len(hint_years & candidate_years)
    score -= 4 * len(hint_years - candidate_years)

    provider_match = False
    candidate_url = candidate["url"].casefold()
    for provider, domains in _PROVIDER_DOMAINS.items():
        if provider.casefold() not in hint.casefold():
            continue
        if any(domain in candidate_url for domain in domains):
            score += 35
            provider_match = True
        else:
            score -= 5

    return score, provider_match, content_score


def _direct_url_from_hint(hint: str) -> str:
    """像 ``ucloud.bupt.edu.cn`` 这类明确域名无需搜索即可转成链接。"""
    match = _BARE_DOMAIN_RE.search(hint)
    if not match:
        return ""
    value = match.group(1).rstrip(".,;，。；")
    return f"https://{value}"


def enrich_report_sources(
    text: str,
    search_groups: list[dict],
) -> str:
    """
    用本次研究的原始搜索结果补齐来源 URL。

    - 已有完整 URL 的来源保持不变；
    - 若研究员漏写 ``## 来源``，从 ``【证据摘要】`` 恢复编号和标题；
    - 仅在标题/摘要/搜索词相关性足够高，或明确命中来源机构域名时回填直链；
    - 无法可靠匹配时保留标题，不猜测 URL，由前端提供可点击的搜索兜底。
    """
    text = (text or "").strip()
    if not text:
        return text

    body, source_section = _split_source_section(text)
    parsed_sources = _parse_sources(source_section or "")
    source_by_num = {source.num: source for source in parsed_sources}
    evidence_hints = _evidence_source_hints(body)

    evidence_match = re.search(
        r"(?:【\s*证据摘要\s*】|^#{1,6}\s*证据摘要\s*$)",
        body,
        re.MULTILINE,
    )
    conclusion = body[:evidence_match.start()] if evidence_match else body
    cited_nums = {int(raw) for raw in _NUM_TAG_RE.findall(conclusion)}
    nums = sorted(cited_nums | set(source_by_num) | set(evidence_hints))
    if not nums:
        return text

    candidates = _search_candidates(search_groups)
    enriched: list[tuple[int, str, str]] = []
    for num in nums:
        existing = source_by_num.get(num)
        title = existing.title if existing else evidence_hints.get(num, "")
        context = _citation_context(conclusion, num)
        hint = _compact_text(
            " ".join(
                part
                for part in (title, evidence_hints.get(num, ""), context)
                if part
            )
        )
        if not title:
            title = context or f"来源 {num}"

        url = existing.url if existing else ""
        if not url:
            url = _direct_url_from_hint(hint)
        if not url and candidates and hint:
            matching_focus = _compact_text(
                " ".join(
                    part
                    for part in (
                        existing.title if existing else "",
                        context,
                    )
                    if part
                )
            )
            ranked = sorted(
                (
                    (
                        *_candidate_score(hint, matching_focus, candidate),
                        candidate,
                    )
                    for candidate in candidates
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            best_score, provider_match, content_score, best = ranked[0]
            second_score = ranked[1][0] if len(ranked) > 1 else float("-inf")
            if (
                provider_match
                and best_score >= 42
                and content_score >= 13
                and (content_score >= 16 or best_score - second_score >= 3)
            ):
                url = best["url"]
            elif best_score >= 52 and content_score >= 34:
                url = best["url"]

        enriched.append((num, title, url))

    source_lines = ["## 来源", ""]
    source_lines.extend(_format_source(*source) for source in enriched)
    return f"{body.rstrip()}\n\n" + "\n".join(source_lines)


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
        key = (
            f"url:{_normalize_url(s.url)}"
            if s.url
            else f"title:{s.title.strip().casefold()}"
        )
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

    # 跨 finding 去重：同一 URL（或无 URL 时的同名占位条目）只保留一次，
    # 重排为从 1 的连续编号，并把合并正文里的 [n] 同步改号。
    deduped: list[tuple[int, str, str]] = []
    remap: dict[int, int] = {}
    seen_keys: dict[str, int] = {}
    for num, title, url in unified:
        key = (
            f"url:{_normalize_url(url)}"
            if url
            else f"title:{title.strip().casefold()}"
        )
        if key in seen_keys:
            remap[num] = seen_keys[key]
        else:
            new_num = len(deduped) + 1
            seen_keys[key] = new_num
            remap[num] = new_num
            deduped.append((new_num, title, url))
    unified = deduped

    def _remap_citation(m: re.Match) -> str:
        n = int(m.group(1))
        return f"[{remap[n]}]" if n in remap else m.group(0)

    merged_body = "\n\n---\n\n".join(bodies)
    merged_body = _NUM_TAG_RE.sub(_remap_citation, merged_body)

    lines = ["## 来源", ""]
    for num, title, url in unified:
        lines.append(_format_source(num, title, url))
    sources_md = "\n".join(lines)

    return MergedReport(
        body=merged_body,
        sources_md=sources_md,
        sources=unified,
    )


def has_source_section(text: str) -> bool:
    """报告是否已包含 ## 来源 区段。"""
    return bool(_SOURCE_HEADING_RE.search(text or ""))


def replace_report_sources(
    report: str,
    sources: list[tuple[int, str, str]],
) -> str:
    """
    用统一来源替换模型自行生成的来源区段，只保留正文实际引用的来源，
    并把保留下来的编号压缩为从 1 开始的连续序列。

    这能避免模型遗漏、重排或混用不同 researcher 的来源，同时减少最终列表
    中正文从未引用的条目。正文里的 [n] 会与来源列表同步改号。
    """
    if not sources:
        # 若 researcher 没提供可用的统一来源，尝试规范化 reporter 自带的
        # 来源区段；仍然只保留正文实际引用项，并保证最终编号连续。
        normalized, source_count = _renumber_search_report(report or "", offset=0)
        if source_count:
            _, sources_text = _split_source_section(normalized)
            fallback_sources = [
                (source.num, source.title, source.url)
                for source in _parse_sources(sources_text or "")
            ]
            return replace_report_sources(normalized, fallback_sources)
        return (report or "").strip()

    body, _ = _split_source_section(report or "")
    cited = {int(num) for num in _NUM_TAG_RE.findall(body)}
    selected = [
        (num, title, url)
        for num, title, url in sources
        if num in cited
    ]

    # reporter 自创编号时（正文引用的 [n] 大多不在统一来源里），
    # 偶然的数字重合不可信：整体视为幻觉编号，输出完整统一来源列表。
    if not selected or (cited and len(selected) * 2 < len(cited)):
        selected = list(sources)

    old_to_new = {
        old_num: new_num
        for new_num, (old_num, _, _) in enumerate(selected, start=1)
    }

    def _repl(m: re.Match) -> str:
        old_num = int(m.group(1))
        new_num = old_to_new.get(old_num)
        return f"[{new_num}]" if new_num is not None else ""

    body = _NUM_TAG_RE.sub(_repl, body)
    # 编号被清除后可能残留「文字 ，」这类悬空空格
    body = re.sub(r"[ \t]+(?=[，。；：、）)\n])", "", body)

    lines = ["## 来源", ""]
    lines.extend(
        _format_source(new_num, title, url)
        for new_num, (_, title, url) in enumerate(selected, start=1)
    )
    sources_md = "\n".join(lines)
    return f"{body.rstrip()}\n\n{sources_md}"
