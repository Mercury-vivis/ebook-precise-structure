#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
emit_apply_report 回归测试（残留口径：全文权威 / A+C 修正 / 留痕豁免）。

验证统一后的 emit_apply_report（位于 _common.py，被 pipeline_assemble 与 assemble_epub
共用）行为：
  1) 权威口径＝全文：old 出现在**任何**位置（含他页合法同串）都判残留——
     与 check_book 闸B 同口径（pre_delivery_verifier 已退场），杜绝"apply_report 无残留 / 闸B 判有残留"打架。
     页块仅作诊断：残留行标注【phys错位·全文仍见，真残留】/【声明页内残留】。
  2) A+C 修正：增字型补丁（old 是 new 子串）先剔 new 再判 old，已改正形态不误报。
  3) OCR 与 EPUB 两路径行为一致（锚同为 <<<PAGE phys=N ...>>>）。

不依赖大书数据，用合成 03 + 临时 work/out 目录。
"""
import os, sys, json, tempfile, shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import emit_apply_report


def _run(patches, clean_03, struct=""):
    """搭临时 work/out，写 corrections + 03 + meta，跑 emit_apply_report，返回 (summary,bad,rn,report_text)。"""
    tmp = tempfile.mkdtemp(prefix="t_eap_")
    try:
        work = os.path.join(tmp, "work")
        out = os.path.join(tmp, "out")
        os.makedirs(os.path.join(work, "corrections"))
        os.makedirs(out)
        # corrections/*.json（active）
        with open(os.path.join(work, "corrections", "c.json"), "w", encoding="utf-8") as f:
            json.dump({"patches": patches}, f, ensure_ascii=False)
        # 03 交付件（文件名含 03_）
        with open(os.path.join(out, "X03_带页码全文.md"), "w", encoding="utf-8") as f:
            f.write(clean_03)
        if struct:
            with open(os.path.join(work, "fulltext_struct.txt"), "w", encoding="utf-8") as f:
                f.write(struct)
        meta = os.path.join(work, "book_meta.json")
        with open(meta, "w", encoding="utf-8") as f:
            json.dump({"book": "测试书"}, f, ensure_ascii=False)
        summary, bad, rn = emit_apply_report(work, out, meta, pending=[])
        report = open(os.path.join(work, "apply_report.md"), encoding="utf-8").read()
        return summary, bad, rn, report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _residual_olds(report):
    import re
    olds = []
    in_res = False
    for ln in report.splitlines():
        if ln.strip().startswith("## 残留明细"):
            in_res = True
            continue
        if in_res and ln.strip().startswith("- "):
            m = re.match(r"- (.+?)→", ln)
            if m:
                olds.append(m.group(1))
    return olds


def test_cross_page_fulltext_scope():
    # 权威口径＝全文：old 只在 phys=2 块、补丁声明 phys=1 → 仍判残留（phys错位·全文仍见），
    # 与闸B 全文口径一致（旧版只查声明页块会漏报——天朝的崩溃 phys=43 错位即此型）。
    clean = ("<<<PAGE phys=1 book=1>>>\n正文一，正确词在此。\n"
             "<<<PAGE phys=2 book=2>>>\n正文二，错误词在此。\n")
    patches = [{"old": "错误词", "new": "正确词", "phys": 1}]
    _, _, rn, rep = _run(patches, clean)
    assert rn == 1, f"FAIL: 全文口径未生效，跨页同串应判残留 rn=1，得 {rn}"
    assert "【phys错位·全文仍见，真残留】" in rep, "FAIL: 残留行未标注 phys错位 诊断定性"
    print("✅ test_cross_page_fulltext_scope: 全文权威口径生效（跨页同串不漏报，带 phys错位 诊断）")


def test_epub_real_residual_caught():
    # old 真在声明 phys=1 块 → 报残留，且标注【声明页内残留】。
    clean = ("<<<PAGE phys=1 book=1>>>\n真错在此。\n"
             "<<<PAGE phys=2 book=2>>>\n别处无错。\n")
    patches = [{"old": "真错", "new": "改正", "phys": 1}]
    _, _, rn, rep = _run(patches, clean)
    assert rn == 1 and "真错" in _residual_olds(rep), f"FAIL: 真残留未报，rn={rn}"
    assert "【声明页内残留】" in rep, "FAIL: 残留行未标注 声明页内 定性"
    print("✅ test_epub_real_residual_caught: EPUB 声明页内真残留正确报出")


def test_ac_fix_inject_type():
    # 增字型：old=史 new=刺史。03 已改正为「刺史」→ A+C 修正应先剔刺史再判史，不误报。
    clean = ("<<<PAGE phys=1 book=1>>>\n通鉴载刺史出镇。\n"
             "<<<PAGE phys=2 book=2>>>\n他页无关。\n")
    patches = [{"old": "史", "new": "刺史", "phys": 1, "systematic": True}]
    _, _, rn, rep = _run(patches, clean)
    assert rn == 0, f"FAIL: A+C 修正失效，已改正形态误报残留，rn={rn}，残留={_residual_olds(rep)}"
    print("✅ test_ac_fix_inject_type: 增字型 A+C 修正生效（已改正形态不误报）")


def test_ocr_unchanged():
    # OCR 数字页锚与 EPUB 行为一致：全文口径（跨页同串判残留）+ A+C 修正。
    clean = ("<<<PAGE phys=10>>>\n正确词。\n"
             "<<<PAGE phys=11>>>\n错误词。\n")
    patches = [{"old": "错误词", "new": "正确词", "phys": 10}]  # 目标 phys=10 不含错误词 → 全文仍见
    _, _, rn, rep = _run(patches, clean)
    assert rn == 1 and "【phys错位·全文仍见，真残留】" in rep, f"FAIL: OCR 全文口径异常，rn={rn}"
    # 增字型
    clean2 = "<<<PAGE phys=10>>>\n刺史出镇。\n<<<PAGE phys=11>>>\n他页。\n"
    patches2 = [{"old": "史", "new": "刺史", "phys": 10, "systematic": True}]
    _, _, rn2, _ = _run(patches2, clean2)
    assert rn2 == 0, f"FAIL: OCR A+C 异常，rn={rn2}"
    print("✅ test_ocr_unchanged: OCR 路径与 EPUB 行为一致（全文口径 + A+C 修正）")


def test_trace_mark_old_not_residual():
    # v1.18.4：留痕形态——主文已改正、old 合法保留在〔原书作「old」〕留痕内 → 不误报残留。
    # （东晋门阀政治 4 处假阳性即此型：apply_report 未剥留痕、闸B 剥了 → 两处口径打架。）
    clean = ("<<<PAGE phys=1 book=1>>>\n《晋书》卷九一《儒林传》〔原书作「《晋书）卷九一《儒林传》」〕，改正留痕。\n"
             "<<<PAGE phys=2 book=2>>>\n他页无关。\n")
    patches = [{"old": "《晋书）卷九一《儒林传》", "new": "《晋书》卷九一《儒林传》", "phys": 1}]
    _, _, rn, rep = _run(patches, clean)
    assert rn == 0, f"FAIL: 留痕内 old 误报残留，rn={rn}，残留={_residual_olds(rep)}"
    print("✅ test_trace_mark_old_not_residual: 留痕形态 old 合法保留不误报（剥留痕后判定，与闸B 同口径）")


if __name__ == "__main__":
    test_cross_page_fulltext_scope()
    test_epub_real_residual_caught()
    test_ac_fix_inject_type()
    test_ocr_unchanged()
    test_trace_mark_old_not_residual()
    print("\nALL PASS · emit_apply_report 回归通过（全文权威口径 + A+C 修正 + 留痕豁免，EPUB/OCR 一致）。")
