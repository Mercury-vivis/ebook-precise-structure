#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_split_volumes.py —— 卷拆分器回归测试（run_tests.py 自动发现）。

覆盖：
  A. 语义检测（连续编码 book==phys）：3 卷，标题页落在 phys 4/40/80，验证拆分为 3 卷、区间无重叠无遗漏。
  B. book= 印页重置自动检测：phys 11 处 book 回落，验证 2 卷。
  C. 同卷标题近距离重复（phys 4 与 6 均命中 Vol1）→ 去重为 1 个卷起点。
均校验：每卷切片首/末 phys 与区间一致、union(phys)==全集、dropped==0。
"""
import os, sys, tempfile, runpy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import split_volumes as sv

def make_struct(pages):
    """pages: list of (phys, book, body_text)。返回 fulltext_struct.txt 内容。"""
    return "\n".join(f"<<<PAGE phys={p} book={b}>>>\n{body}\n" for p, b, body in pages)

def run_split(work, extra_argv):
    """在 work 目录跑拆分器，argv 不含脚本名。返回 SystemExit code。"""
    old_argv, old_dir = sys.argv, os.getcwd()
    sys.argv = ["split_volumes.py", work] + extra_argv
    os.chdir(work)
    try:
        runpy.run_path(os.path.join(os.path.dirname(__file__), "split_volumes.py"),
                       run_name="__main__")
        return 0
    except SystemExit as e:
        return e.code if e.code is not None else 0
    finally:
        sys.argv, os.chdir = old_argv, os.chdir
        os.chdir(old_dir)

def phys_in_slice(path):
    """返回切片文件中的 phys 列表（升序）。"""
    import re
    txt = open(path, encoding="utf-8").read()
    return sorted(int(x) for x in re.findall(r"<<<PAGE phys=(\d+)", txt))

def main():
    tmp = tempfile.mkdtemp(prefix="sv_test_")
    failures = []

    # ---------- 用例 A：语义检测，连续编码 ----------
    pages = []
    for p in range(1, 101):
        if p == 4:
            body = "History and Culture of the Indian People, Volume 1, The Vedic Age"
        elif p == 40:
            body = "History and Culture of the Indian People, Volume 2, The Age of X"
        elif p == 80:
            body = "History and Culture of the Indian People, Volume 3, The Age of Y"
        else:
            body = f"content page {p}"
        pages.append((p, p, body))  # 连续编码：book == phys
    wa = os.path.join(tmp, "A")
    os.makedirs(wa)
    open(os.path.join(wa, "fulltext_struct.txt"), "w", encoding="utf-8").write(make_struct(pages))

    # dry-run 应先于 apply（不落盘）；用例 A 仅 100 页，用 --threshold 50 触发
    rc = run_split(wa, ["--threshold", "50",
                        "--title-pattern", r"History and Culture of the Indian People, Volume (\d+)"])
    if rc != 0:
        failures.append(f"A dry-run 应返回 0，实得 {rc}")
    if os.path.isdir(os.path.join(wa, "volumes")):
        failures.append("A dry-run 不应落盘 volumes/")

    # apply + confirm（语义需 confirm）
    rc = run_split(wa, ["--threshold", "50",
                        "--title-pattern", r"History and Culture of the Indian People, Volume (\d+)",
                        "--apply", "--confirm"])
    if rc != 0:
        failures.append(f"A apply 应返回 0，实得 {rc}")
    vroot = os.path.join(wa, "volumes")
    vol_dirs = sorted(d for d in os.listdir(vroot) if d.startswith("V") and os.path.isdir(os.path.join(vroot, d)))
    if len(vol_dirs) != 3:
        failures.append(f"A 应拆 3 卷，实得 {len(vol_dirs)}: {vol_dirs}")
    else:
        # 期望区间 [1,39] [40,79] [80,100]（卷1 从 phys1 起，含前页；切点取 ordinal>=2 标题）
        expect = [(1, 39), (40, 79), (80, 100)]
        all_phys = []
        for d, (a, b) in zip(vol_dirs, expect):
            phys = phys_in_slice(os.path.join(vroot, d, "fulltext_struct.txt"))
            if not phys or phys[0] != a or phys[-1] != b:
                failures.append(f"A {d} 区间应为 {a}-{b}，实得 {phys[0]}-{phys[-1]}")
            all_phys += phys
        # union 覆盖 1..100，无重叠无遗漏
        if sorted(all_phys) != list(range(1, 101)):
            failures.append(f"A phys 全集不匹配 1..100（重叠/遗漏）")

    # ---------- 用例 B：book= 重置自动检测 ----------
    pages = [(p, (p if p <= 10 else p - 10), f"pg{p}") for p in range(1, 21)]  # phys11 起 book 回落到 1
    wb = os.path.join(tmp, "B")
    os.makedirs(wb)
    open(os.path.join(wb, "fulltext_struct.txt"), "w", encoding="utf-8").write(make_struct(pages))
    rc = run_split(wb, ["--threshold", "10", "--apply"])  # 不应需 confirm（book_reset 高置信）
    if rc != 0:
        failures.append(f"B apply 应返回 0，实得 {rc}")
    vroot_b = os.path.join(wb, "volumes")
    vb = sorted(d for d in os.listdir(vroot_b) if d.startswith("V") and os.path.isdir(os.path.join(vroot_b, d)))
    if len(vb) != 2:
        failures.append(f"B 应拆 2 卷（book 重置），实得 {len(vb)}")
    else:
        phys1 = phys_in_slice(os.path.join(vroot_b, vb[0], "fulltext_struct.txt"))
        phys2 = phys_in_slice(os.path.join(vroot_b, vb[1], "fulltext_struct.txt"))
        if phys1 != list(range(1, 11)) or phys2 != list(range(11, 21)):
            failures.append(f"B 区间应为 [1-10] 与 [11-20]，实得 {phys1[:1]}..{phys1[-1]} / {phys2[:1]}..{phys2[-1]}")

    # ---------- 用例 C：同卷标题近距离重复去重 ----------
    pages = []
    for p in range(1, 31):
        if p in (4, 6):  # 同一卷标题在 phys4 与 phys6 重复（<20 去重窗）
            body = "History and Culture of the Indian People, Volume 1, The Vedic Age"
        elif p == 20:
            body = "History and Culture of the Indian People, Volume 2, Second"
        else:
            body = f"content page {p}"
        pages.append((p, p, body))
    wc = os.path.join(tmp, "C")
    os.makedirs(wc)
    open(os.path.join(wc, "fulltext_struct.txt"), "w", encoding="utf-8").write(make_struct(pages))
    rc = run_split(wc, ["--threshold", "10",
                        "--title-pattern", r"History and Culture of the Indian People, Volume (\d+)",
                        "--apply", "--confirm"])
    if rc != 0:
        failures.append(f"C apply 应返回 0，实得 {rc}")
    vroot_c = os.path.join(wc, "volumes")
    vc = sorted(d for d in os.listdir(vroot_c) if d.startswith("V") and os.path.isdir(os.path.join(vroot_c, d)))
    if len(vc) != 2:
        failures.append(f"C 应去重为 2 卷（4 与 6 同卷），实得 {len(vc)}: {vc}")

    # ---------- 阈值以下不拆分 ----------
    wd = os.path.join(tmp, "D")
    os.makedirs(wd)
    open(os.path.join(wd, "fulltext_struct.txt"), "w", encoding="utf-8").write(
        make_struct([(p, p, f"pg{p}") for p in range(1, 50)]))  # 49 页 < 3000
    rc = run_split(wd, [])
    if rc != 0:
        failures.append(f"D 阈值以下应返回 0，实得 {rc}")

    if failures:
        print("❌ test_split_volumes 失败：")
        for f in failures:
            print("   -", f)
        sys.exit(1)
    print("✅ test_split_volumes 全部用例通过（语义/重置/去重/阈值）。")
    sys.exit(0)

if __name__ == "__main__":
    main()
