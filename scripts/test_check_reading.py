#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_check_reading.py —— 精读覆盖率检查（check_reading.py）

取代 test_validate_subagent_outputs.py（2026-10-19）：那个脚本已退场，覆盖率职责
归 check_reading.py、多命中职责归 check_ledger.py，测试随之改指向存活脚本。

守四件事：
  1. 行范围提取排除 phys/book/页码范围与年份范围（否则覆盖数会凭空变多）
  2. 全局口径：连续覆盖通过 / 缺口被发现 / 重叠被发现
  3. 章内行口径判定，且不被「行1-全总」这类全书汇总范围带偏
  4. 端到端：半覆盖必须 exit 1，全覆盖 exit 0（无记录同样 exit 1）

运行：python scripts/test_check_reading.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from check_reading import extract_line_ranges, is_chapter_local, verify_global  # noqa: E402

fails = []


def check(desc, cond):
    if not cond:
        fails.append(desc)


def run(work):
    r = subprocess.run([sys.executable, os.path.join(HERE, "check_reading.py"), "--work", work],
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def test_extract_ranges_excludes_pages():
    text = (
        "| A | 1-1643 | 1643 |\n"
        "分配 phys 170-234（book 147-211）\n"
        "覆盖 1920-1924 年\n"
    )
    pairs = [(s, e) for s, e, _ in extract_line_ranges(text)]
    check("应提取表格行范围 1-1643", (1, 1643) in pairs)
    check("不应提取 phys 范围", (170, 234) not in pairs)
    check("不应提取 book 范围", (147, 211) not in pairs)
    check("不应提取年份范围", (1920, 1924) not in pairs)


def test_global_coverage():
    probs, covered = verify_global([(1, 100, False), (101, 200, False)], 200)
    check("连续覆盖应通过", not probs and covered == 200)
    probs, _ = verify_global([(1, 100, False), (150, 200, False)], 200)
    check("缺口应被发现", any("101..149" in p for p in probs))
    probs, _ = verify_global([(1, 100, False), (80, 200, False)], 200)
    check("重叠应被发现", any("重叠" in p for p in probs))
    probs, _ = verify_global([(1, 100, False)], 200)
    check("结尾未覆盖应被发现", any("结尾未覆盖" in p for p in probs))


def test_chapter_local():
    check("多个从 1 起应判章内行", is_chapter_local([(1, 389, True), (1, 265, True)], 654))
    check("全书汇总范围不应误判章内行",
          not is_chapter_local([(1, 1643, False), (1644, 3171, False), (1, 9879, False)], 9879))


def test_e2e():
    d = tempfile.mkdtemp(prefix="rd_e2e_")
    with open(os.path.join(d, "fulltext_struct.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(f"行{i}内容" for i in range(1, 1001)))
    with open(os.path.join(d, "reading_progress.md"), "w", encoding="utf-8") as f:
        f.write("| A | 1-500 | 500 |\n")
    rc, out = run(d)
    check("半覆盖必须 exit 1", rc == 1 and "未覆盖" in out)

    with open(os.path.join(d, "reading_progress.md"), "w", encoding="utf-8") as f:
        f.write("| A | 1-1000 | 1000 |\n")
    rc, out = run(d)
    check("全覆盖应 exit 0", rc == 0 and "100%" in out)

    d2 = tempfile.mkdtemp(prefix="rd_none_")
    with open(os.path.join(d2, "fulltext_struct.txt"), "w", encoding="utf-8") as f:
        f.write("行1\n行2\n")
    rc, out = run(d2)
    check("无记录必须 exit 1（不能当通过）", rc == 1 and "无法证明全量覆盖" in out)


if __name__ == "__main__":
    test_extract_ranges_excludes_pages()
    test_global_coverage()
    test_chapter_local()
    test_e2e()
    if fails:
        print(f"❌ test_check_reading 失败 {len(fails)} 项：")
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print("✅ test_check_reading 通过（范围提取 / 全局覆盖 / 章内口径 / 端到端三态）")
