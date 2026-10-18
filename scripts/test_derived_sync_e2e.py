#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""派生件一致性与报告项 · 端到端回归（真跑 assemble_epub.py，非桩测）。

对应四处 P0 教训：
  1) 错形孤岛：notes 写于台账定稿之前、援引未修正原文。不同步则 01/02 保留已判定错形，
     02 的名句到 03 检索 0 命中，「可检索 / 可回溯」契约在派生件上断裂。
     → assemble 须用同一份已裁定台账同步 notes，并把每一处改动记入 04 §四。
  2) 交付级全件错形复扫：错形只出现在派生件（底本里没有）时，清单复扫只扫 03 必为通过。
     → 四件落盘后复扫 00/01/02/03；有残留**点名报告**（不阻断，属台账/笔记该修的事）。
  3) 错字清单缺失：不能把「跳过检测」读成「检测通过」。
     → 报告里显式写「本项跳过」，不阻断交付。
  4) 前置缺失须在写任何交付件之前拦下 → 交付目录不得留下半成品（这一条是真阻断）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ASSEMBLE = os.path.join(HERE, "assemble_epub.py")

STRUCT = (
    "<<<PAGE phys=1 book=1 title=第一章>>>\n"
    "荀或曰：秦汉之制，盖自秦汉始。\n"
    "磨宋之役，府丘制兴。\n"
    "<<<PAGE phys=2 book=2 title=第二章>>>\n"
    "夫兵者，国之大事。\n"
    "府丘制兴于两代。\n"
    "<<<PAGE phys=3 book=3 title=第三章>>>\n"
    "故曰：知彼知己，百战不殆。\n"
)
RAW = (
    "<<<SEC idx=1 title=第一章>>>\n荀或曰：秦汉之制，盖自秦汉始。\n磨宋之役，府丘制兴。\n"
    "<<<SEC idx=2 title=第二章>>>\n夫兵者，国之大事。\n府丘制兴于两代。\n"
    "<<<SEC idx=3 title=第三章>>>\n故曰：知彼知己，百战不殆。\n"
)
# notes 刻意写成**未修正**形态（真实分章 Agent 就是这么写的：先读原文、后定台账）
NOTES_WRONG = (
    "## 1. 第一章\n**本章论题**：论制度之起源。\n**核心概念**：\n- 制度：自秦汉始（SEC idx=1）。\n"
    "**关键原句摘录**：\n> 荀或曰：秦汉之制，盖自秦汉始。（SEC idx=1）\n\n"
    "## 2. 第二章\n**本章论题**：论兵制。\n**核心概念**：\n- 府丘：兴起于两代（SEC idx=2）。\n"
    "**关键原句摘录**：\n> 磨宋之役，府丘制兴。（SEC idx=1）\n\n"
    "## 3. 第三章\n**本章论题**：论战略。\n**核心概念**：\n- 知彼知己（SEC idx=3）。\n"
    "**关键原句摘录**：\n> 故曰：知彼知己，百战不殆。（SEC idx=3）\n"
)
META = {
    "book": "测试书", "author": "某", "type": "② 思想/理论型", "source": "测试",
    "total_phys": 3,
    "chapters": [{"no": 1, "name": "第一章", "phys_start": 1},
                 {"no": 2, "name": "第二章", "phys_start": 2},
                 {"no": 3, "name": "第三章", "phys_start": 3}],
}
PAGE_INDEX = {"total": 3, "pages": [{"phys": 1, "book": 1},
                                    {"phys": 2, "book": 2},
                                    {"phys": 3, "book": 3}]}
EF = "荀或|荀彧|形近：或→彧\n府丘|府兵|形近：丘→兵\n磨宋|磨勘|形近：宋→勘\n"
CORR_ALL = [
    {"phys": "ALL", "old": "荀或", "new": "荀彧", "systematic": True, "reason": "形近：或→彧"},
    {"phys": "ALL", "old": "府丘", "new": "府兵", "systematic": True, "reason": "形近：丘→兵"},
    {"phys": "ALL", "old": "磨宋", "new": "磨勘", "systematic": True, "reason": "形近：宋→勘"},
]


def _w(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def _build(tmp, corrections, error_forms=None, notes=NOTES_WRONG, drop=()):
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "out")
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    if "fulltext_struct.txt" not in drop:
        _w(os.path.join(work, "fulltext_struct.txt"), STRUCT)
    if "fulltext_raw.txt" not in drop:
        _w(os.path.join(work, "fulltext_raw.txt"), RAW)
    if "page_index.json" not in drop:
        _w(os.path.join(work, "page_index.json"), json.dumps(PAGE_INDEX, ensure_ascii=False))
    _w(os.path.join(work, "book_meta.json"), json.dumps(META, ensure_ascii=False))
    _w(os.path.join(work, "corrections", "01.json"), json.dumps(corrections, ensure_ascii=False))
    _w(os.path.join(work, "notes", "01.md"), notes)
    if error_forms is not None:
        _w(os.path.join(work, "error_forms.txt"), error_forms)
    return work, out


def _run(work, out, extra=()):
    cmd = [sys.executable, ASSEMBLE, "--work", work, "--out", out,
           "--meta", os.path.join(work, "book_meta.json")] + list(extra)
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=HERE, timeout=180)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _item(out, suffix):
    fs = [f for f in os.listdir(out) if f.endswith(suffix)]
    assert fs, "FAIL: 交付目录缺少 %s：%s" % (suffix, os.listdir(out))
    return open(os.path.join(out, fs[0]), encoding="utf-8").read()


def test_notes_synced_with_03():
    """notes 里的未修正错形须被同步到与 03 一致——01/02 不得留错形孤岛。"""
    tmp = tempfile.mkdtemp(prefix="t_sync_")
    try:
        work, out = _build(tmp, CORR_ALL, error_forms=EF)
        rc, log = _run(work, out)
        assert rc == 0, "FAIL: 同步后仍未通过，rc=%d\n%s" % (rc, log[:2500])
        f01 = _item(out, "01_章节详解.md")
        f03 = _item(out, "03_带页码全文.md")
        for wrong in ("荀或", "府丘", "磨宋"):
            assert wrong not in f01, "FAIL: 01 仍留已判定错形「%s」（错形孤岛）" % wrong
        # 可检索契约：02 的名句须能在权威底本 03 里原样命中
        f02 = _item(out, "02_脉络与速查卡.md")
        quote = "磨勘之役，府兵制兴"
        assert quote in f02, "FAIL: 02 名句未同步：%r" % f02[:400]
        assert quote in f03, "FAIL: 02 的名句在 03 检索 0 命中（可回溯契约断裂）"
        print("✅ test_notes_synced_with_03: notes 已同步，01/02 无错形孤岛，02 名句可在 03 命中")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sync_recorded_in_ledger():
    """派生件同步不得是静默改动——每一处须落 04 改错留痕 §四。"""
    tmp = tempfile.mkdtemp(prefix="t_sync_")
    try:
        work, out = _build(tmp, CORR_ALL, error_forms=EF)
        rc, log = _run(work, out)
        assert rc == 0, "FAIL: 首跑未通过：\n%s" % log[:2000]
        # 04 由独立脚本生成（与 assemble 同一次交付流程）
        rc2 = subprocess.run([sys.executable, os.path.join(HERE, "gen_correction_ledger.py"),
                              "--work", work, "--out", out,
                              "--meta", os.path.join(work, "book_meta.json"),
                              "--prefix", os.path.basename(out) + "_"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", cwd=HERE, timeout=120).returncode
        assert rc2 == 0, "FAIL: gen_correction_ledger 未通过"
        f04s = [f for f in os.listdir(out) if f.endswith("04_改错留痕.md")]
        assert f04s, "FAIL: 未产出 04：%s" % os.listdir(out)
        f04 = open(os.path.join(out, f04s[0]), encoding="utf-8").read()
        assert "派生件同步" in f04, "FAIL: 04 无派生件同步节（同步成了静默改动）"
        assert "「荀或」→「荀彧」" in f04, "FAIL: 04 §四 未逐条记录同步项：\n%s" % f04[-800:]
        print("✅ test_sync_recorded_in_ledger: 派生件同步逐条记入 04 §四（零静默）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_final_scan_reports_derived_only_error():
    """错形只存在于派生件（底本里没有）时，清单复扫扫 03 必过——须由四件复扫点名。

    探针用「待臣」（应为「侍臣」）：底本与台账都没有它，故清单复扫（扫 03）
    与补丁回放（03 对台账）都必然通过；只有派生件复扫能看见 01/02 里的残留。
    残留属笔记该修的事，故只报告、不阻断。
    """
    tmp = tempfile.mkdtemp(prefix="t_scan_")
    try:
        notes_bad = NOTES_WRONG.replace("府丘：兴起于两代", "待臣：兴起于两代")
        ef_bad = EF + "待臣|侍臣|形近：待→侍\n"
        work, out = _build(tmp, CORR_ALL, error_forms=ef_bad, notes=notes_bad)
        rc, log = _run(work, out)
        assert rc == 0, "FAIL: 派生件残留属报告项，却拦了交付，rc=%d\n%s" % (rc, log[:2500])
        _mds = [f for f in os.listdir(out) if f.endswith(".md")]
        assert _mds, "FAIL: 放行了却没产出交付件：%s" % os.listdir(out)
        assert "交付级错形复扫" in log, "FAIL: 未点名复扫：\n%s" % log[:2500]
        assert "待臣" in log, "FAIL: 复扫结论未点名残留错形：\n%s" % log[:2500]
        print("✅ test_final_scan_reports_derived_only_error: 派生件专属错形 → 复扫点名、不阻断")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_error_forms_reports_skip():
    """无 error_forms.txt → 报告显式写「本项跳过」，不阻断（跳过不等于通过）。"""
    tmp = tempfile.mkdtemp(prefix="t_g1bm_")
    try:
        work, out = _build(tmp, CORR_ALL, error_forms=None)
        rc, log = _run(work, out)
        assert rc == 0, "FAIL: 清单缺失属报告项，却拦了交付，rc=%d\n%s" % (rc, log[:2000])
        _mds = [f for f in os.listdir(out) if f.endswith(".md")]
        assert _mds, "FAIL: 放行了却没产出交付件：%s" % os.listdir(out)
        assert "本项跳过" in log, "FAIL: 跳过却未在报告写明（会被读成检测通过）：\n%s" % log[:2000]
        # --no-gate1b 仍可用：显式省略，日志同样留痕迹
        rc2, log2 = _run(work, out, extra=("--no-gate1b",))
        assert rc2 == 0, "FAIL: --no-gate1b 用法失效：\n%s" % log2[:2000]
        assert "省略" in log2, "FAIL: 省略未在日志留声明"
        print("✅ test_missing_error_forms_reports_skip: 清单缺失 → 明写「本项跳过」，不阻断")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_prereq_missing_leaves_no_half_delivery():
    """前置缺失须在写任何交付件之前拦下——交付目录不得留半成品。"""
    tmp = tempfile.mkdtemp(prefix="t_pre_")
    try:
        work, out = _build(tmp, CORR_ALL, error_forms=EF, drop=("page_index.json",))
        rc, log = _run(work, out)
        assert rc != 0, "FAIL: 缺前置却放行，rc=%d" % rc
        assert "前置缺失" in log, "FAIL: 未给出「前置缺失」明确指引：\n%s" % log[:1500]
        left = [f for f in os.listdir(out) if f.endswith(".md")]
        assert not left, "FAIL: 交付目录留下半成品 %s（前置校验落在写盘之后）" % left
        print("✅ test_prereq_missing_leaves_no_half_delivery: 前置缺失即拦，交付目录无半成品")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_notes_synced_with_03()
    test_sync_recorded_in_ledger()
    test_final_scan_reports_derived_only_error()
    test_missing_error_forms_reports_skip()
    test_prereq_missing_leaves_no_half_delivery()
    print("\nALL PASS · 派生件一致性与报告项端到端回归通过"
          "（notes 同步 / 04 §四留痕 / 派生件残留只报告 / 清单缺失明写跳过 / 前置不留半成品）。")
