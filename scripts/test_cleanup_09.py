#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_cleanup_09.py —— 图版回收脚本回归（v2.6.2）

守三件事（全部来自 2026-10-26《坦克战》实战）：
  1. **00 指纹必须 sha256**：字段名是 `00=sha256:`，曾用 md5_file 计算后写入
     32 hex，与 check_book 的 sha256 校验口径冲突（报告项「指纹不符」）。
  2. **残缺指纹可修**：历史版本写坏的 32 hex 必须能被重新覆盖成 64 hex，
     指纹正则若只认 64 位就永远修不回来。
  3. **盘面变动要说实话**：dry-run 与 apply 是两次独立扫描，两次之间若有外部
     删除，只能记「本次扫描时已不在盘面」，不得写成「上游本就没有」（推断当事实）。

跑法：python test_cleanup_09.py   （纯标准库，临时目录自建自清）
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "09_cleanup_after_delivery.py")
SUFFIX = {
    "00": "00_梳理总览与目录对照.md",
    "01": "01_章节详解.md",
    "02": "02_脉络与速查卡.md",
    "03": "03_带页码全文.md",
    "04": "04_改错留痕.md",
}
PREFIX = "测试书"

fails = []


def check(desc, cond):
    if not cond:
        fails.append(desc)


def sha256_of(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def build(root, n_up_work=None, n_up_raw=None, fp_value=None):
    """搭一套最小可跑的 work/raw/deliv 三处。

    n_up_work / n_up_raw：上游各留几个图（None = 全量 3 个），用于模拟外部删除。
    fp_value：04 里预先写入的 00 指纹值（None = 写正确的 64 hex）。
    """
    raw = os.path.join(root, "Book_raw", "测试书")
    work = os.path.join(root, "work", "books", "测试书")
    deliv = os.path.join(root, "Books_final", PREFIX)
    names = ["p1_image_1.jpg", "p2_image_1.jpg", "p3_image_1.jpg"]
    for d in (os.path.join(raw, "imgs"), os.path.join(work, "imgs"), os.path.join(deliv, "imgs")):
        os.makedirs(d, exist_ok=True)
    for i, n in enumerate(names):
        body = f"JPEG-{i}".encode() * 4
        for d in (os.path.join(raw, "imgs"), os.path.join(work, "imgs"), os.path.join(deliv, "imgs")):
            with open(os.path.join(d, n), "wb") as f:
                f.write(body)

    # 模拟外部删除：上游各只留前 k 个
    if n_up_work is not None:
        for n in names[n_up_work:]:
            os.remove(os.path.join(work, "imgs", n))
    if n_up_raw is not None:
        for n in names[n_up_raw:]:
            os.remove(os.path.join(raw, "imgs", n))

    with open(os.path.join(work, "bookocr_dir.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(raw + "\n")
    with open(os.path.join(work, "gate_report.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("# 终检\n\n- 结论：通过\n")
    with open(os.path.join(work, "00_after.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("## 一、成书过程\n占位\n")

    body = {
        "00": "# 总览\n\n" + "".join(f"![图](imgs/{n})\n" for n in names),
        "01": "# 详解\n正文\n",
        "02": "# 速查卡\n正文\n",
        "03": "# 全文\n\n" + "".join(f"![图](imgs/{n})\n" for n in names),
        "04": "# 留痕\n\n## 五、交付指纹\n\n",
    }
    for k, text in body.items():
        with open(os.path.join(deliv, PREFIX + SUFFIX[k]), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    p00 = os.path.join(deliv, PREFIX + SUFFIX["00"])
    fp = fp_value if fp_value is not None else sha256_of(p00)
    with open(os.path.join(deliv, PREFIX + SUFFIX["04"]), "a", encoding="utf-8", newline="\n") as f:
        f.write(f"> 00=sha256:{fp}；01=sha256:{'0'*64}；02=sha256:{'0'*64}；03=sha256:{'0'*64}\n")
    return work, raw, deliv


def run(work, deliv, apply=True):
    cmd = [sys.executable, SCRIPT, "--work", work, "--deliv", deliv]
    if apply:
        cmd.append("--apply")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def case_full_flow():
    """场景 1：上游完整 → 正常回收，指纹为正确 sha256（64 hex）。"""
    root = tempfile.mkdtemp(prefix="t09a_")
    try:
        work, raw, deliv = build(root)
        rc, out = run(work, deliv)
        check("场景1 应 rc=0", rc == 0)
        check("场景1 应删 work 3 / raw 3", "删 work 3" in out and "raw 3" in out)
        p04 = open(os.path.join(deliv, PREFIX + SUFFIX["04"]), encoding="utf-8").read()
        import re
        m = re.search(r"00=sha256:([0-9a-f]+)", p04)
        check("场景1 指纹位应存在", bool(m))
        if m:
            want = sha256_of(os.path.join(deliv, PREFIX + SUFFIX["00"]))
            check("场景1 指纹应为 64 hex", len(m.group(1)) == 64)
            check("场景1 指纹应等于实件 sha256", m.group(1) == want)
        log = open(os.path.join(work, "cleanup_log.md"), encoding="utf-8").read()
        check("场景1 log 应含扫描快照", "扫描快照" in log)
        check("场景1 log 不应报盘面自检", "## 盘面自检" not in log)
        print("✅ case_full_flow: 正常回收、指纹 sha256(64) 与实件一致、无盘面告警")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_upstream_lost():
    """场景 2：上游被外部删掉 2 张 → 报盘面自检，措辞为「本次扫描已无」。"""
    root = tempfile.mkdtemp(prefix="t09b_")
    try:
        work, raw, deliv = build(root, n_up_work=1, n_up_raw=1)
        rc, out = run(work, deliv, apply=False)  # dry-run 也应报
        check("场景2 dry-run 应 rc=0", rc == 0)
        check("场景2 dry-run 应报盘面自检", "盘面自检" in out)
        rc, out = run(work, deliv)
        check("场景2 应 rc=0", rc == 0)
        log = open(os.path.join(work, "cleanup_log.md"), encoding="utf-8").read()
        check("场景2 log 应含盘面自检段", "## 盘面自检" in log)
        check("场景2 log 应写「本次扫描时已不在盘面」", "本次扫描时已不在盘面" in log)
        check("场景2 log 不应出现「上游本就没有」", "上游本就没有" not in log)
        p00 = open(os.path.join(deliv, PREFIX + SUFFIX["00"]), encoding="utf-8").read()
        check("场景2 00 应写「上游本次扫描已无」", "上游本次扫描已无" in p00)
        check("场景2 00 不应出现「上游本就没有」", "上游本就没有" not in p00)
        print("✅ case_upstream_lost: 上游缺图被如实报出，推断措辞已清除")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_repair_bad_fp():
    """场景 3：04 里预置 32 hex 的 md5 坏指纹 → 应被改成 64 hex sha256。"""
    root = tempfile.mkdtemp(prefix="t09c_")
    try:
        work, raw, deliv = build(root, fp_value="a" * 32)
        rc, out = run(work, deliv)
        check("场景3 应 rc=0", rc == 0)
        p04 = open(os.path.join(deliv, PREFIX + SUFFIX["04"]), encoding="utf-8").read()
        import re
        m = re.search(r"00=sha256:([0-9a-f]+)", p04)
        check("场景3 指纹位应存在", bool(m))
        if m:
            want = sha256_of(os.path.join(deliv, PREFIX + SUFFIX["00"]))
            check("场景3 坏指纹应被修成 64 hex", len(m.group(1)) == 64)
            check("场景3 修后应等于实件 sha256", m.group(1) == want)
        print("✅ case_repair_bad_fp: 32 hex 坏指纹可被重新覆盖为正确 sha256")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    case_full_flow()
    case_upstream_lost()
    case_repair_bad_fp()
    if fails:
        print("\n❌ 失败项：")
        for f in fails:
            print("   -", f)
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
