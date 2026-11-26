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


def _run(patches, clean_03, struct="", manifest=None):
    """搭临时 work/out，写 corrections + 03 + meta，跑 emit_apply_report，返回 (summary,bad,rn,report_text)。

    manifest 可选：模拟派生件存在（且不带 regex 字段）的情形，验证记录源不被它改写。
    """
    tmp = tempfile.mkdtemp(prefix="t_eap_")
    try:
        work = os.path.join(tmp, "work")
        out = os.path.join(tmp, "out")
        os.makedirs(os.path.join(work, "corrections"))
        os.makedirs(out)
        # corrections/*.json（active）
        with open(os.path.join(work, "corrections", "c.json"), "w", encoding="utf-8") as f:
            json.dump({"patches": patches}, f, ensure_ascii=False)
        if manifest is not None:
            with open(os.path.join(work, "patch_manifest.json"), "w", encoding="utf-8") as f:
                json.dump({"patches": manifest}, f, ensure_ascii=False)
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


def _counts(rep):
    """取报告首部四行计数：已应用 / 底本0命中 / 残留。"""
    out = {}
    for ln in rep.splitlines():
        for key in ("- 已应用", "- 底本 0 命中", "- ⚠️ 残留"):
            if ln.startswith(key):
                out[key] = ln
    return out


def test_regex_self_matching_not_residual():
    """自匹配正则正例：new 仍落在 old 的匹配集，改净后不得判残留。

    `[○〇](?=年)` → `〇`：改净为「一九八〇年」后 re.search(old) 依旧为真，
    用「正文仍匹配该模式」判残留会整本误报；幂等判据才给出「无残留」。
    """
    import re as _re
    struct = "<<<PAGE phys=1>>>\n一九八○年，次年又见一九八〇年。\n"
    clean = ("<<<PAGE phys=1 book=1>>>\n一九八〇年，次年又见一九八〇年。\n"
             "<<<PAGE phys=2 book=2>>>\n他页无关。\n")
    patches = [{"old": r"[○〇](?=年)", "new": "〇", "phys": "ALL",
                "regex": True, "systematic": True}]
    assert _re.search(patches[0]["old"], clean) is not None, "样本应确为自匹配正则"
    _, _, rn, rep = _run(patches, clean, struct=struct)
    counts = _counts(rep)
    assert rn == 0, f"FAIL: 自匹配正则改净后误报残留，rn={rn}，残留={_residual_olds(rep)}"
    assert "1 处" in counts.get("- 已应用", ""), f"FAIL: 底本命中未计入已应用：{counts}"
    assert "0 处" in counts.get("- 底本 0 命中", ""), f"FAIL: 正则按字面判了 0 命中：{counts}"
    print("✅ test_regex_self_matching_not_residual: 自匹配正则按幂等判据不误报，底本命中 1")


def test_regex_non_self_matching_residual_and_scope():
    """非自匹配正则正例 + phys 错位反例：03 仍留 (1) 要报残留，且按全文定性。"""
    struct = "<<<PAGE phys=2>>>\n篇题(1)与(2)\n"
    clean = ("<<<PAGE phys=1 book=1>>>\n他页无错。\n"
             "<<<PAGE phys=2 book=2>>>\n篇题(1)与(2)\n")
    patches = [{"old": r"\((\d+)\)", "new": r"〔\1〕", "phys": 1,
                "regex": True, "reason": "注号归一"}]
    _, _, rn, rep = _run(patches, clean, struct=struct)
    assert rn == 1, f"FAIL: 非自匹配正则真残留漏报，rn={rn}"
    assert r"\((\d+)\)" in _residual_olds(rep), "FAIL: 残留明细未点名该正则"
    assert "【phys错位·全文仍见，真残留】" in rep, "FAIL: 未标注全文口径的 phys 错位定性"
    print("✅ test_regex_non_self_matching_residual_and_scope: 真残留报出且按全文定性")


def test_manifest_is_not_patch_source():
    """单一真相源正例：派生 manifest 不带 regex 字段时仍按 corrections 判定；deleted 墓碑不参与。"""
    struct = "<<<PAGE phys=1>>>\n一九八○年。\n"
    clean = "<<<PAGE phys=1 book=1>>>\n一九八〇年。\n"
    patches = [
        {"old": r"[○〇](?=年)", "new": "〇", "regex": True, "phys": "ALL", "systematic": True},
        {"old": r"\((\d+)\)", "new": r"〔\1〕", "regex": True, "status": "deleted"},
    ]
    manifest = [
        {"id": "a", "old": r"[○〇](?=年)", "new": "〇", "phys": "ALL",
         "type": "systematic", "reason": "", "source": "c.json", "status": "active"},
        {"id": "b", "old": r"\((\d+)\)", "new": r"〔\1〕", "phys": "ALL",
         "type": "systematic", "reason": "", "source": "c.json", "status": "deleted"},
    ]
    _, _, rn, rep = _run(patches, clean, struct=struct, manifest=manifest)
    assert rn == 0, f"FAIL: manifest 覆盖了 corrections 的正则语义，rn={rn}"
    assert r"\((\d+)\)" not in rep, "FAIL: deleted 墓碑的正则 old 混进判定"
    assert "1 处" in _counts(rep).get("- 已应用", ""), "FAIL: active 正则未记为已应用"
    print("✅ test_manifest_is_not_patch_source: 记录源只认 corrections，墓碑已排除")


if __name__ == "__main__":
    test_cross_page_fulltext_scope()
    test_epub_real_residual_caught()
    test_ac_fix_inject_type()
    test_ocr_unchanged()
    test_trace_mark_old_not_residual()
    test_regex_self_matching_not_residual()
    test_regex_non_self_matching_residual_and_scope()
    test_manifest_is_not_patch_source()
    print("\nALL PASS · emit_apply_report 回归通过（全文权威口径 + A+C 修正 + 留痕豁免 + 正则幂等判据，EPUB/OCR 一致）。")
