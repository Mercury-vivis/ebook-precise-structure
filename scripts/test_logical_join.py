"""回归测试：跨行连行纠错（硬换行切断的错形仍能命中）。

验证 apply_corrections_core 依赖的跨行匹配机制：
- `find_span` / `iter_spans` 的「level-2 换行容忍」会在 haystack 的字符间容忍任意 \\n/\\r；
- 命中返回的是【原始 body 坐标系】的 (start,end)，替换后不真正改写存储 body 的换行结构
  （即「仅匹配阶段视图，命中后按坐标回拆」），留痕插入坐标不会错位。

用例：基层 struct 真实形态「见剌\\n史」（刺史被 OCR 硬换行切断于行尾/行首）。
补丁 old 写作「剌史」（无换行），须仍能命中并替换为连续的「刺史」。

运行：python scripts/test_logical_join.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import iter_spans, find_span, apply_corrections_core


def _blocks_from(text):
    """单页测试块：[phys, book, title, body]"""
    return [[1, "", "", text]]


def test_cross_line_match():
    # 真实用例：刺史被硬换行切断为「见剌」(行尾) + 「史」(行首)
    body = '他到泗州时，即“见剌\n史”可以一站一站不断换官船前去。'
    needle = "剌史"  # 补丁 old（无换行）
    spans = iter_spans(body, needle)
    assert spans, "FAIL: 跨行连行未命中"
    (s, e) = spans[0]
    # 坐标落回原 body：替换后正文连续、03 不再有换行
    repl = body[:s] + "刺史" + body[e:]
    assert "刺史" in repl, "FAIL: 替换后无「刺史」"
    assert "见剌" not in repl, "FAIL: 残留「见剌」"
    assert repl.index("刺史") == s, "FAIL: 替换坐标错位"
    print("✅ test_cross_line_match: 跨行连行命中 + 按原坐标回拆，03 连续正确")


def test_apply_core_cross_line():
    # 端到端：apply_corrections_core 施加一个 per-phys 跨行补丁
    import json
    import tempfile
    body = '即“见剌\n史”可以换官船。'
    blocks = _blocks_from(body)
    d = tempfile.mkdtemp(prefix="logical_join_test_")
    cf = os.path.join(d, "corr.json")
    with open(cf, "w", encoding="utf-8") as f:
        json.dump([{"old": "剌史", "new": "刺史", "phys": 1}], f)
    miss, pending, trace, tongai, silent, ocr = apply_corrections_core(
        blocks, [cf], work_dir=None)
    out = blocks[0][3]
    assert "刺史" in out, f"FAIL: apply 后无「刺史」(got {out!r})"
    assert "见剌" not in out, "FAIL: apply 后残留「见剌」"
    assert not miss, f"FAIL: 不应有 miss（{miss}）"
    print("✅ test_apply_core_cross_line: apply_corrections_core 跨行补丁施加成功")


if __name__ == "__main__":
    test_cross_line_match()
    test_apply_core_cross_line()
    print("\nALL PASS · 跨行连行已由 find_span level-2 换行容忍 + 原坐标回拆实现，无需独立 logical_join。")
