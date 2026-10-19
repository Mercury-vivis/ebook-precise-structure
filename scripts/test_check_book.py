#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_check_book.py —— 闸B 反向差异三类分流 + 交付指纹口径（2026-10-19）

守四件事：
  1. 记录源是 corrections（含 regex/systematic），不是 patch_manifest（manifest 只落 9 键）
  2. ① 已登记（消除误报）：old 与底本只差空白/全半角时，不得报成「未登记」
  3. ② 页归属错位：账 phys 写错但补丁按全局回退生效 → 报「台账 phys=A / 实测 B」，不算未登记
  4. ③ 真未登记与正向残留：③ 只报告（exit 0）；正向残留才是阻断（exit 1）
  5. 交付指纹不符只报告、不阻断（修订路径定点改 03 本来就该指纹变）

运行：python scripts/test_check_book.py
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
fails = []


def check(desc, cond):
    if not cond:
        fails.append(desc)


def run(work, out, delivery=False):
    cmd = [PY, os.path.join(HERE, "check_book.py"), "--work", work, "--out", out]
    if not delivery:
        cmd.append("--no-delivery")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def build(tmp, struct_pages, corr, out_pages, meta=None):
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "out")
    os.makedirs(os.path.join(work, "corrections"))
    os.makedirs(out, exist_ok=True)
    with io.open(os.path.join(work, "fulltext_struct.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(f"<<<PAGE phys={p}>>>\n{t}" for p, t in struct_pages))
    with io.open(os.path.join(work, "corrections", "00_corrections.json"), "w",
                 encoding="utf-8", newline="\n") as f:
        json.dump(corr, f, ensure_ascii=False)
    if meta is not None:
        with io.open(os.path.join(work, "book_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    with io.open(os.path.join(out, "书03_带页码全文.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(f"<<<PAGE phys={p}>>>\n{t}" for p, t in out_pages))
    return work, out


def test_three_classes():
    """① 宽容匹配已登记 / ② 页归属错位 / ③ 真未登记，三类互不混淆"""
    tmp = tempfile.mkdtemp(prefix="cb_three_")
    struct = [(1, "甲乙丙 丁戊己庚辛"),      # 底本带空格（① 用）
              (2, "他日自已当知此事也"),      # ② 的 old 实际所在页
              (3, "壬癸子丑")]
    out_pages = [(1, "甲乙丙丁戊己庚辛子"),   # ① 已按台账改成 new（空格被替换覆盖）
                 (2, "他日自己当知此事也"),   # ② 补丁按全局回退落在这页
                 (3, "壬癸子丑寅")]           # ③ 账上无人认领
    corr = [
        # ① old 无空格、底本有空格：只有放宽空白才命中
        {"phys": 1, "old": "甲乙丙丁戊己庚辛", "new": "甲乙丙丁戊己庚辛子", "reason": "t"},
        # ② 声明 phys=1，实际在 phys2（old ≥4 触发全局回退）
        {"phys": 1, "old": "他日自已当知此事", "new": "他日自己当知此事", "reason": "t"},
    ]
    work, out = build(tmp, struct, corr, out_pages)
    rc, o = run(work, out)
    check("三类场景应 exit 0", rc == 0)
    check("① 已登记消除误报 1 条", "① 已登记（消除误报）1 条" in o)
    check("② 页归属错位 1 条", "② 页归属错位 1 条" in o)
    check("② 报出台账页与实测页", "台账 phys=1 / 实测 2" in o)
    check("② 不得再报「账/底本 0 命中」", "全书 0 命中" not in o or "全书 0 命中 0 条" in o)
    check("③ 真未登记 1 页", "③ 真未登记 1 页" in o)
    check("③ 定位到 phys=3 的改动", "phys=3" in o.split("③ 真未登记")[1])
    print("✅ test_three_classes: ① 宽容已登记 / ② 页归属错位 / ③ 真未登记 三类分流")


def test_residual_blocks():
    """正向残留仍是唯一内容类阻断：old 还在 03 → exit 1"""
    tmp = tempfile.mkdtemp(prefix="cb_res_")
    struct = [(1, "他日自已当知此事也")]
    out_pages = [(1, "他日自已当知此事也")]   # 台账说要改，03 没改
    corr = [{"phys": 1, "old": "自已当知此事", "new": "自己当知此事", "reason": "t"}]
    work, out = build(tmp, struct, corr, out_pages)
    rc, o = run(work, out)
    check("正向残留必须 exit 1", rc == 1)
    check("残留要点名", "正向残留 1 处" in o)
    print("✅ test_residual_blocks: 正向残留阻断（exit 1）")


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def test_fingerprint_report_only():
    """指纹不符只报告、不阻断；相符时报「一致」"""
    tmp = tempfile.mkdtemp(prefix="cb_fp_")
    struct = [(1, "他日自己当知此事也")]
    out_pages = [(1, "他日自己当知此事也")]
    corr = [{"phys": 1, "old": "自已当知此事", "new": "自己当知此事", "reason": "t"}]
    work, out = build(tmp, struct, corr, out_pages)
    for name in ("00_梳理总览与目录对照.md", "01_章节详解.md", "02_脉络与速查卡.md"):
        io.open(os.path.join(out, "书" + name), "w", encoding="utf-8", newline="\n").write("内容\n")
    p04 = os.path.join(out, "书04_改错留痕.md")
    bad = "00=sha256:" + "0" * 64
    io.open(p04, "w", encoding="utf-8", newline="\n").write("## 五、交付指纹\n\n> " + bad + "\n")
    rc, o = run(work, out, delivery=True)
    check("指纹不符不得阻断", rc == 0)
    check("指纹不符要报出", "交付指纹与 04 登记不符" in o)

    rec = "；".join(f"{k}=sha256:{_sha(os.path.join(out, '书' + s))}"
                   for k, s in (("00", "00_梳理总览与目录对照.md"), ("01", "01_章节详解.md"),
                                ("02", "02_脉络与速查卡.md"), ("03", "03_带页码全文.md")))
    io.open(p04, "w", encoding="utf-8", newline="\n").write("## 五、交付指纹\n\n> " + rec + "\n")
    rc, o = run(work, out, delivery=True)
    check("指纹相符报一致", rc == 0 and "交付指纹：00/01/02/03 与 assemble 定稿一致" in o)

    # 05 不属于五件（指纹机制下由 gen_reading_report 事后生成）——缺 05 不算缺件
    check("五件不含 05", "缺失" not in o or "05" not in o.split("缺失")[1][:30])
    print("✅ test_fingerprint_report_only: 指纹不符只报告 / 相符报一致 / 05 不计入五件")


if __name__ == "__main__":
    test_three_classes()
    test_residual_blocks()
    test_fingerprint_report_only()
    if fails:
        print(f"❌ test_check_book 失败 {len(fails)} 项：")
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print("✅ test_check_book 通过")
