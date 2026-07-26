"""
core/source_merge.py 的测试。

可两种方式运行：
  python tests/test_source_merge.py        # 直跑
  pytest tests/test_source_merge.py        # pytest
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.source_merge import (  # noqa: E402
    _renumber_search_report,
    has_source_section,
    merge_findings_with_sources,
    replace_report_sources,
)


def _sources_as_dict(text):
    """从 ## 来源 区段提取 {num: (title, url)}。"""
    from core.source_merge import _parse_sources, _split_source_section

    _, sec = _split_source_section(text)
    if not sec:
        return {}
    return {s.num: (s.title, s.url) for s in _parse_sources(sec)}


# ── 1. 两份报告都从 [1] 开始 -> 合并后连续递增 ────────────────────────────────

def test_two_reports_both_start_from_one():
    f1 = (
        "容东片区住宅建设持续推进[1]，公共服务逐步完善[2]。\n\n"
        "## 来源\n\n"
        "- [1] 官方建设进展 - https://example.com/a\n"
        "- [2] 容东建设报道 - https://example.com/b"
    )
    f2 = (
        "白洋淀水质提升至Ⅲ类[1]。\n\n"
        "## 来源\n\n"
        "- [1] 水质公报 - https://example.com/c"
    )
    merged = merge_findings_with_sources([f1, f2])

    # 正文引用应已映射到全局编号：f2 的 [1] -> [3]
    assert "[1]" in merged.body and "[2]" in merged.body
    assert "[3]" in merged.body, "f2 的 [1] 应被映射为 [3]"

    src = _sources_as_dict(merged.sources_md)
    assert sorted(src.keys()) == [1, 2, 3], src
    assert src[3][1] == "https://example.com/c"


# ── 2. 同一报告中重复 URL -> 只留一条 ───────────────────────────────────────

def test_duplicate_url_within_report():
    f = (
        "结论A[1]，结论B[2]。\n\n"
        "## 来源\n\n"
        "- [1] 来源A - https://example.com/same\n"
        "- [2] 来源B - https://example.com/same"
    )
    renumbered, n = _renumber_search_report(f, offset=0)
    assert n == 1, f"唯一来源应为 1，实际 {n}"
    # 两个原始编号都映射到 [1]
    src = _sources_as_dict(renumbered)
    assert sorted(src.keys()) == [1], src
    # 正文 [1] 和 [2] 都应指向 [1]
    assert "[1]" in renumbered
    # [2] 在正文中应被替换为 [1]
    assert "结论B[1]" in renumbered, renumbered


# ── 3. 多轮搜索产生重复原始编号 -> 偏移按唯一来源数推进 ────────────────────────

def test_offset_advances_by_unique_count_not_max():
    # 报告1：来源编号 [1]..[3]，但正文引用了不存在的 [10]（max=10，唯一=3）
    f1 = (
        "建设持续[1]，人口增长[3]，另见[10]。\n\n"
        "## 来源\n\n"
        "- [1] 建设进展 - https://example.com/a\n"
        "- [2] 中间来源 - https://example.com/b\n"
        "- [3] 人口来源 - https://example.com/c"
    )
    f2 = (
        "生态修复[1]。\n\n"
        "## 来源\n\n"
        "- [1] 生态来源 - https://example.com/d"
    )
    merged = merge_findings_with_sources([f1, f2])

    # 报告1 唯一来源 3 条 -> 报告2 的 [1] 应映射为 [4]（而非 max+1=11）
    src = _sources_as_dict(merged.sources_md)
    assert sorted(src.keys()) == [1, 2, 3, 4], src
    assert src[4][1] == "https://example.com/d"
    # 报告1 正文里的 [10] 未在映射中 -> 原样保留
    assert "[10]" in merged.body, "未知编号应原样保留"


# ── 4. 正文引用与来源列表同步重编号且一致 ────────────────────────────────────

def test_body_and_sources_renumbered_consistently():
    f = (
        "雄安新区建设投资持续增长[1]，重点片区公共服务逐步完善[3]。\n\n"
        "## 来源\n\n"
        "- [1] 来源A - https://example.com/a\n"
        "- [2] 来源B - https://example.com/b\n"
        "- [3] 来源C - https://example.com/c"
    )
    # offset=10：正文 [1]->[11], [3]->[13]
    renumbered, n = _renumber_search_report(f, offset=10)
    assert n == 3
    assert "[11]" in renumbered and "[13]" in renumbered
    # [2] 在正文中未出现，但来源里应映射为 [12]
    src = _sources_as_dict(renumbered)
    assert src[11][1] == "https://example.com/a"
    assert src[12][1] == "https://example.com/b"
    assert src[13][1] == "https://example.com/c"
    # 旧编号 [1] 不应残留在来源列表
    assert 1 not in src


# ── 5. Markdown 列表来源与普通文本来源混用 ───────────────────────────────────

def test_mixed_list_and_plain_text_sources():
    f = (
        "结论[1]，结论[2]。\n\n"
        "## 来源\n\n"
        "- [1] 列表来源 - https://example.com/a\n"
        "[2] 纯文本来源 https://example.com/b"
    )
    renumbered, n = _renumber_search_report(f, offset=0)
    assert n == 2
    src = _sources_as_dict(renumbered)
    assert src[1][0] == "列表来源"
    assert src[2][0] == "纯文本来源"
    assert src[1][1] == "https://example.com/a"


# ── 6. 多条来源挤在一行 -> 仍能正确切分 ─────────────────────────────────────

def test_packed_sources_on_one_line():
    f = (
        "结论[1][2]。\n\n"
        "## 来源\n\n"
        "- [1] 来源A - https://example.com/a [2] 来源B - https://example.com/b"
    )
    renumbered, n = _renumber_search_report(f, offset=0)
    assert n == 2, f"应解析出 2 条，实际 {n}"
    src = _sources_as_dict(renumbered)
    assert sorted(src.keys()) == [1, 2], src
    assert src[1][1] == "https://example.com/a"
    assert src[2][1] == "https://example.com/b"


# ── 7. 无 ## 来源 区段 -> 原样返回，不报错 ────────────────────────────────────

def test_no_source_section_passes_through():
    f = "只是普通研究笔记，没有来源区段，正文有个 [1] 也不应被改动。"
    renumbered, n = _renumber_search_report(f, offset=5)
    assert n == 0
    assert renumbered == f, "无来源区段应原样返回"

    merged = merge_findings_with_sources([f])
    assert merged.body == f
    assert merged.sources == []
    assert not has_source_section(merged.sources_md) or merged.sources_md == "## 来源\n"


# ── 8. URL 归一化去重（尾斜杠/大小写）────────────────────────────────────────

def test_url_normalization_dedup():
    # 仅 host 大小写 / 尾斜杠不同，视为同一来源（路径大小写保持敏感）
    f = (
        "结论[1][2]。\n\n"
        "## 来源\n\n"
        "- [1] 来源A - https://Example.com/a/\n"
        "- [2] 来源B - https://example.com/a"
    )
    renumbered, n = _renumber_search_report(f, offset=0)
    assert n == 1, f"归一化后应为同一来源，实际 {n}"
    assert "结论[1][1]" in renumbered, renumbered


# ── 9. 最终报告来源强制使用统一列表，且只保留正文引用项 ─────────────────────

def test_replace_report_sources_uses_only_cited_canonical_sources():
    report = (
        "建设变化结论[1]，人口变化结论[3]。\n\n"
        "## 来源\n\n"
        "- [1] 模型自行生成的错误来源 - https://wrong.example.com"
    )
    canonical = [
        (1, "建设官方来源", "https://example.com/a"),
        (2, "未引用来源", "https://example.com/b"),
        (3, "人口官方来源", "https://example.com/c"),
    ]

    finalized = replace_report_sources(report, canonical)
    sources = _sources_as_dict(finalized)

    assert sorted(sources) == [1, 3]
    assert sources[1][1] == "https://example.com/a"
    assert sources[3][1] == "https://example.com/c"
    assert "wrong.example.com" not in finalized
    assert "未引用来源" not in finalized


def _run_all():
    import inspect

    fns = [
        (name, obj) for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    passed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ✓ {name}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {name}: {type(e).__name__}: {e}")
            raise
    print(f"\n{passed}/{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
