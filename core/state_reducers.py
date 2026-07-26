"""自定义 reducer：支持跨轮重置的 findings 列表。

findings 用 operator.add 时，同一线程连续分析第二个地区会继承上一轮内容。
本 reducer 额外支持 reset 标记：router 判定 action 时返回 [FINDINGS_RESET]
即可清空旧值，同时不破坏并发 researcher 的 append 语义。
"""

from __future__ import annotations

FINDINGS_RESET = "__findings_reset__"


def findings_reducer(left: list[str] | None, right: list[str] | None) -> list[str]:
    """
    - right 为 None -> 返回 left（不改动）
    - right 含 FINDINGS_RESET -> 清空旧值，返回 reset 标记之后的追加项
      （允许 [FINDINGS_RESET, "新内容"] 这种"清空并立即追加"的用法）
    - 否则 -> left + right（同一轮内并发 researcher 结果合并）
    """
    if right is None:
        return list(left or [])
    right_list = list(right)
    if FINDINGS_RESET in right_list:
        return [x for x in right_list if x != FINDINGS_RESET]
    return list(left or []) + right_list
