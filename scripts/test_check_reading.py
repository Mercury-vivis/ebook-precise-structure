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


_EV_STRUCT = ("<<<PAGE phys=1 book=1>>>\n荀彧曰：秦汉之制，天下之势也。\n"
              "<<<PAGE phys=2 book=2>>>\n夫兵者，国之大事，死生之地。\n")
_EV_LINES = len(_EV_STRUCT.splitlines())


def _ev_dir(evidence=None, top=None, ranges_ok=True):
    d = tempfile.mkdtemp(prefix="rd_ev_")
    with open(os.path.join(d, "fulltext_struct.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(_EV_STRUCT)
    with open(os.path.join(d, "reading_progress.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f"| A | 1-{_EV_LINES} | {_EV_LINES} |\n" if ranges_ok else f"| A | 1-1 | 1 |\n")
    if evidence is not None or top is not None:
        rec = {"name": "组A", "total_lines": _EV_LINES, "read_lines": _EV_LINES}
        if evidence is not None:
            rec["evidence"] = evidence
        data = {"records": [rec]}
        if top is not None:
            data["evidence"] = top
        with open(os.path.join(d, "reading_progress.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False)
    return d


def _run_ev(d, *extra):
    r = subprocess.run([sys.executable, os.path.join(HERE, "check_reading.py"),
                        "--work", d, *extra],
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def test_evidence():
    """逐页关键句：命中 / 未命中 / 页号错 / 无证据四态，且默认不加参数不受影响。"""
    # ① 两条都命中（一条来自 records、一条来自顶层）→ --evidence 报命中 2
    d = _ev_dir(evidence=[{"phys": 1, "quote": "荀彧曰：秦汉之制"}],
                top=[{"phys": 2, "quote": "夫兵者，国之大事"}])
    rc, out = _run_ev(d, "--evidence")
    check("证据全部命中应 exit 0", rc == 0)
    check("应报命中 2 条", "命中 2" in out and "未命中 0" in out)

    # ② 不加参数：不校验证据，行为与旧版一致
    rc2, out2 = _run_ev(d)
    check("默认不校验证据（不加参数）", rc2 == 0 and "逐页关键句" not in out2)

    # ③ 未逐字命中（换了个字）→ 报告未命中；--require-evidence 才失败
    d2 = _ev_dir(evidence=[{"phys": 1, "quote": "荀彧曰：秦汉之制，天下大势也"}])
    rc, out = _run_ev(d2, "--evidence")
    check("未命中在 --evidence 下只报告（exit 0）", rc == 0 and "未命中 1" in out)
    rc, out = _run_ev(d2, "--require-evidence")
    check("未命中在 --require-evidence 下 exit 1", rc == 1)

    # ④ 页号不在底本里 → 报「不在底本页锚里」
    d3 = _ev_dir(evidence=[{"phys": 99, "quote": "夫兵者"}])
    rc, out = _run_ev(d3, "--evidence")
    check("页号错应点名", "不在底本页锚里" in out)

    # ⑤ 完全没有 evidence → --require-evidence 判失败，--evidence 只提示
    d4 = _ev_dir()
    rc, out = _run_ev(d4, "--evidence")
    check("无证据时 --evidence 不失败", rc == 0 and "没有 evidence 条目" in out)
    rc, out = _run_ev(d4, "--require-evidence")
    check("无证据时 --require-evidence exit 1", rc == 1)


if __name__ == "__main__":
    test_extract_ranges_excludes_pages()
    test_global_coverage()
    test_chapter_local()
    test_e2e()
    test_evidence()
    if fails:
        print(f"❌ test_check_reading 失败 {len(fails)} 项：")
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print("✅ test_check_reading 通过（范围提取 / 全局覆盖 / 章内口径 / 端到端三态 / 逐页关键句证据）")
