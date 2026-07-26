"""
core/intent.py + core/state_reducers.py 的测试。

  python tests/test_intent.py
  pytest tests/test_intent.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.intent import (  # noqa: E402
    invalid_input_clarification,
    is_obviously_invalid_input,
    latest_human_text,
)
from core.state_reducers import FINDINGS_RESET, findings_reducer  # noqa: E402


# ── 1. 明显无效输入判定（参考"推荐测试用例"）──────────────────────────────────

def test_obviously_invalid_inputs():
    invalid = ["", "   ", "1", "12345", "!!!", "……", "???", "@#$%"]
    for s in invalid:
        assert is_obviously_invalid_input(s), f"应判定无效：{s!r}"


def test_valid_inputs_not_flagged():
    valid = ["你好", "你是谁", "雄安新区", "分析雄安新区变化", "asdf help", "帮我分析一下"]
    for s in valid:
        assert not is_obviously_invalid_input(s), f"不应判定无效：{s!r}"


def test_none_input_is_invalid():
    assert is_obviously_invalid_input(None)


# ── 2. latest_human_text 消息兼容读取 ─────────────────────────────────────────

def test_latest_human_text_human_message():
    from langchain_core.messages import AIMessage, HumanMessage

    msgs = [HumanMessage(content="分析雄安新区"), AIMessage(content="好的")]
    assert latest_human_text(msgs) == "分析雄安新区"


def test_latest_human_text_dict_form():
    msgs = [
        {"role": "user", "content": "你是谁"},
        {"role": "assistant", "content": "我是助手"},
        {"type": "human", "content": "分析北京"},
    ]
    assert latest_human_text(msgs) == "分析北京"


def test_latest_human_text_content_blocks():
    msgs = [
        {
            "type": "human",
            "content": [
                {"type": "text", "text": "分析"},
                {"type": "text", "text": "上海"},
            ],
        }
    ]
    assert latest_human_text(msgs) == "分析上海"


def test_latest_human_text_empty():
    assert latest_human_text([]) == ""
    assert latest_human_text(None) == ""


# ── 3. invalid_input_clarification 针对性话术 ─────────────────────────────────

def test_clarification_quotes_original():
    out = invalid_input_clarification("1")
    assert "1" in out, "应引用用户原话"
    assert "输错" in out


def test_clarification_for_blank():
    out = invalid_input_clarification("")
    assert "没有输入" in out


# ── 4. findings_reducer：append 与 reset ───────────────────────────────────────

def test_findings_reducer_append():
    assert findings_reducer(["a"], ["b"]) == ["a", "b"]
    assert findings_reducer(None, ["x"]) == ["x"]
    assert findings_reducer(["a"], None) == ["a"]


def test_findings_reducer_reset_clears_old():
    # 同线程第二轮：旧 findings 已累积，router 返回 [FINDINGS_RESET] 清空
    assert findings_reducer(["old1", "old2"], [FINDINGS_RESET]) == []


def test_findings_reducer_reset_then_append_same_call():
    # 清空并立即追加新内容
    out = findings_reducer(["old"], [FINDINGS_RESET, "new1"])
    assert out == ["new1"]


def test_findings_reducer_reset_idempotent_after_clear():
    # reset 之后正常 append 不再清空
    cleared = findings_reducer(["old"], [FINDINGS_RESET])
    assert findings_reducer(cleared, ["new1", "new2"]) == ["new1", "new2"]


def _run_all():
    fns = [(n, o) for n, o in sorted(globals().items()) if n.startswith("test_") and callable(o)]
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
