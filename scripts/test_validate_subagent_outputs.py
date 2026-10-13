"""回归测试：validate_subagent_outputs.py 子 agent 产出校验（P0-A3）。

验证：
- 行范围提取（排除 phys/book/页码范围、年份范围）；
- 全局行覆盖率校验（连续覆盖 1..total / 缺口 / 重叠）；
- 章内行口径判断（多个范围从 1 开始，排除全书汇总范围）；
- 冗余补丁识别（0命中但 new 已在 body0 → 冗余，非真 ERROR）；
- 端到端临时目录校验（覆盖率 + 多命中）。

运行：python scripts/test_validate_subagent_outputs.py
"""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from validate_subagent_outputs import (
    extract_line_ranges, verify_global_coverage, is_chapter_local_ranges,
    check_reading_progress, check_uniqueness, build_body0
)
from _common import _page_blocks, iter_spans


def test_extract_ranges_excludes_pages():
    """行范围提取排除 phys/book/页码范围。"""
    text = (
        "| A | 1-1643 | 1643 |\n"
        "分配 phys 170-234（book 147-211）\n"
        "覆盖 1920-1924 年\n"
    )
    ranges = extract_line_ranges(text)
    pairs = [(s, e) for s, e, _ in ranges]
    assert (1, 1643) in pairs, "FAIL: 应提取表格行范围 1-1643"
    assert (170, 234) not in pairs, "FAIL: 不应提取 phys 范围"
    assert (147, 211) not in pairs, "FAIL: 不应提取 book 范围"
    assert (1920, 1924) not in pairs, "FAIL: 不应提取无行前缀的年份范围"
    print("✅ test_extract_ranges_excludes_pages: 行范围提取 + 排除页码/年份")


def test_global_coverage_ok():
    """全局行连续覆盖通过。"""
    ranges = [(1, 1643, False), (1644, 3171, False), (3172, 4905, False)]
    ok, problems, covered = verify_global_coverage(ranges, 4905)
    assert ok and not problems, f"FAIL: 应通过（{problems}）"
    assert covered == 4905, "FAIL: 覆盖行数错误"
    print("✅ test_global_coverage_ok: 全局行连续覆盖")


def test_global_coverage_gap():
    """全局行缺口被发现。"""
    ranges = [(1, 100, False), (150, 200, False)]
    ok, problems, covered = verify_global_coverage(ranges, 200)
    assert not ok, "FAIL: 缺口应判不通过"
    assert any("101..149" in p for p in problems), "FAIL: 应报 101..149 缺口"
    print("✅ test_global_coverage_gap: 全局行缺口")


def test_global_coverage_overlap():
    """全局行重叠被发现。"""
    ranges = [(1, 100, False), (80, 200, False)]
    ok, problems, covered = verify_global_coverage(ranges, 200)
    assert any("重叠" in p for p in problems), "FAIL: 应报重叠"
    print("✅ test_global_coverage_overlap: 全局行重叠")


def test_chapter_local_detection():
    """章内行口径判断，排除全书汇总范围。"""
    # 章内行：多个范围从 1 开始
    local = [(1, 389, True), (1, 265, True)]
    assert is_chapter_local_ranges(local, total_lines=654), "FAIL: 应判章内行"
    # 表格全局行 + 全书汇总（1,total）：不应判章内行
    glob = [(1, 1643, False), (1644, 3171, False), (1, 9879, False)]
    assert not is_chapter_local_ranges(glob, total_lines=9879), \
        "FAIL: 汇总范围不应导致章内行误判"
    print("✅ test_chapter_local_detection: 章内行口径 + 排除汇总范围")


def test_redundant_patch_identification():
    """0命中但 new 已在 body0 → 冗余 WARN（非真 ERROR）。"""
    struct = "<<<PAGE phys=1>>>\n因此一幕烂污外交，大限重信之相座不保。"
    # systematic 大限->大隈
    patches = [
        ({"systematic": True, "phys": "ALL", "old": "大限", "new": "大隈"},
         "00_sys.json"),
        ({"phys": 1, "old": "因此一幕烂污外交，大限重信之相座",
          "new": "因此一幕烂污外交，大隈重信之相座"}, "01.json"),
    ]
    body0 = build_body0(struct, patches)
    # 临时目录跑 check_uniqueness
    d = tempfile.mkdtemp(prefix="val_sub_")
    os.makedirs(os.path.join(d, "corrections"))
    with open(os.path.join(d, "corrections", "00.json"), "w",
              encoding="utf-8") as f:
        json.dump([patches[0][0], patches[1][0]], f)
    with open(os.path.join(d, "fulltext_struct.txt"), "w",
              encoding="utf-8") as f:
        f.write(struct)
    errors, warnings, multi, redundant = check_uniqueness(d)
    assert redundant == 1, f"FAIL: 应识别 1 条冗余（got {redundant}）"
    assert not errors, f"FAIL: 冗余不应算 ERROR（{errors}）"
    print("✅ test_redundant_patch_identification: 冗余补丁识别为 WARN")


def test_e2e_real_gap_detected():
    """端到端：reading_progress 只覆盖半本书 → ERROR。"""
    d = tempfile.mkdtemp(prefix="val_e2e_")
    struct = "\n".join(f"行{i}内容" for i in range(1, 1001))
    with open(os.path.join(d, "fulltext_struct.txt"), "w",
              encoding="utf-8") as f:
        f.write(struct)
    # 只声明覆盖 1-500
    with open(os.path.join(d, "reading_progress.md"), "w",
              encoding="utf-8") as f:
        f.write("| A | 1-500 | 500 |\n")
    meta = {"chapters": [{"no": 1, "name": "唯一章",
                          "phys_start": 1, "phys_end": 1}]}
    with open(os.path.join(d, "book_meta.json"), "w",
              encoding="utf-8") as f:
        json.dump(meta, f)
    errors, warnings, info = check_reading_progress(d, meta, write_md=False)
    assert errors, "FAIL: 半覆盖应报 ERROR"
    print("✅ test_e2e_real_gap_detected: 端到端发现半覆盖")


if __name__ == "__main__":
    test_extract_ranges_excludes_pages()
    test_global_coverage_ok()
    test_global_coverage_gap()
    test_global_coverage_overlap()
    test_chapter_local_detection()
    test_redundant_patch_identification()
    test_e2e_real_gap_detected()
    print("\nALL PASS · 子 agent 产出校验（范围提取/覆盖/口径/冗余/端到端）通过。")
