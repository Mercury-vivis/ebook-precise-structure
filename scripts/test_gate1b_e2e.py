#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""错字清单复扫与交付卫生 · 端到端回归（真跑 assemble_epub.py，非桩测）。

不搭桩而直接子进程跑真实组装，因为要验的是真实行为：
  1) 错形在 03 残留 → 只报告并点名，不阻断交付（残留属台账该修的事，不是书不能交的理由；
     书本身好坏由 check_book 闸B 判）；
  2) 错形清零 → 报告「无残留」，且 03 确无该错形；
  3) 幂等重跑后交付目录只有交付件、无 `.bak`（备份落点隔离，五件可一眼核验）；
  4) 前置文件（fulltext_raw / page_index）缺失 → 明确报错退出，不裸 traceback、不降级放行。

【用例设计要点】底本与清单刻意避开 ocr_confusion_dict 的 COMPOUND 错形（如「磨宋」「剌史」），
否则词典类检查会先命中，测不到清单复扫本身。这里用「府丘」（不在词典）作探针。
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
    "刺史之职，掌监察。\n"
    "<<<PAGE phys=2 book=2 title=第二章>>>\n"
    "夫兵者，国之大事。\n"
    "府丘制兴于两代。\n"
    "<<<PAGE phys=3 book=3 title=第三章>>>\n"
    "故曰：知彼知己，百战不殆。\n"
)
RAW = (
    "<<<SEC idx=1 title=第一章>>>\n荀或曰：秦汉之制，盖自秦汉始。\n刺史之职，掌监察。\n"
    "<<<SEC idx=2 title=第二章>>>\n夫兵者，国之大事。\n府丘制兴于两代。\n"
    "<<<SEC idx=3 title=第三章>>>\n故曰：知彼知己，百战不殆。\n"
)
NOTES = (
    "## 1. 第一章\n**本章论题**：论制度之起源。\n**核心概念**：\n- 制度：自秦汉始（SEC idx=1）。\n"
    "**关键原句摘录**：\n> 荀彧曰：秦汉之制，盖自秦汉始。（SEC idx=1）\n\n"
    "## 2. 第二章\n**本章论题**：论兵制。\n**核心概念**：\n- 府兵：兴起于两代（SEC idx=2）。\n"
    "**关键原句摘录**：\n> 夫兵者，国之大事。（SEC idx=2）\n\n"
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
EF = "府丘|府兵|形近：丘→兵\n荀或|荀彧|形近：或→彧\n"


def _w(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def _build(tmp, corrections, error_forms=None, drop=()):
    """搭最小 EPUB work/out；drop 可传要故意缺失的前置文件名。"""
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
    _w(os.path.join(work, "notes", "01.md"), NOTES)
    if error_forms is not None:
        _w(os.path.join(work, "error_forms.txt"), error_forms)
    return work, out


def _run_assemble(work, out):
    cmd = [sys.executable, ASSEMBLE, "--work", work, "--out", out,
           "--meta", os.path.join(work, "book_meta.json")]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=HERE, timeout=180)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


CORR_XUN = [{"phys": "ALL", "old": "荀或", "new": "荀彧",
             "systematic": True, "reason": "形近：或→彧"}]
CORR_BOTH = CORR_XUN + [{"phys": "ALL", "old": "府丘", "new": "府兵",
                         "systematic": True, "reason": "形近：丘→兵"}]


def test_residual_reported_not_blocking():
    """error_forms 列了「府丘」，台账没建补丁 → 03 残留 → 只报告点名，不阻断交付。"""
    tmp = tempfile.mkdtemp(prefix="t_g1b_")
    try:
        work, out = _build(tmp, CORR_XUN, error_forms=EF)
        rc, log = _run_assemble(work, out)
        assert rc == 0, "FAIL: 残留属报告项却拦了交付，rc=%d\n%s" % (rc, log[:2000])
        _mds = [f for f in os.listdir(out) if f.endswith(".md")]
        assert _mds, "FAIL: 放行了却没产出交付件：%s" % os.listdir(out)
        assert "错字清单复扫" in log, "FAIL: 放行但未说清单复扫结论（无从排查）：\n%s" % log[:2000]
        assert "府丘" in log, "FAIL: 报告未点名残留错形：\n%s" % log[:2000]
        # 如实反映：不阻断不等于已修好——03 里那处错形应当仍在
        f03 = [f for f in os.listdir(out) if f.endswith("03_带页码全文.md")]
        body = open(os.path.join(out, f03[0]), encoding="utf-8").read()
        assert "府丘" in body, "FAIL: 台账没这条补丁，03 却已改正（说明有静默改动）"
        # 暂存区须落在系统临时目录（work 常在同步盘，落进 work 会被文件锁撞断；与 PDF 路径同口径）
        leaked = [f for f in os.listdir(work) if "staging" in f.lower()]
        assert not leaked, "FAIL: work 目录出现 staging 残留：%s（EPUB 暂存区须落系统临时目录）" % leaked
        print("✅ test_residual_reported_not_blocking: 错形残留 → 报告点名、不阻断，03 如实保留原状")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_gate1b_passes_when_clean():
    """台账补上「府丘→府兵」通改 → 03 清零 → 报告无残留。"""
    tmp = tempfile.mkdtemp(prefix="t_g1b_")
    try:
        work, out = _build(tmp, CORR_BOTH, error_forms=EF)
        rc, log = _run_assemble(work, out)
        assert rc == 0, "FAIL: 错形已清零却未通过，rc=%d\n%s" % (rc, log[:2500])
        f03 = [f for f in os.listdir(out) if f.endswith("03_带页码全文.md")]
        assert f03, "FAIL: 未产出 03：%s" % os.listdir(out)
        body = open(os.path.join(out, f03[0]), encoding="utf-8").read()
        assert "府丘" not in body and "荀或" not in body, "FAIL: 03 仍有错形：%r" % body[:300]
        assert "残留 0" in log, "FAIL: 已清零但报告未写「残留 0」：\n%s" % log[:2000]
        print("✅ test_gate1b_passes_when_clean: 错形清零 → 报告残留 0，且 03 确无残留错字")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_delivery_dir_has_no_bak_after_rerun():
    """幂等重跑后交付目录不得混入 .bak（五件要能一眼核验），备份应落 work/_bak。"""
    tmp = tempfile.mkdtemp(prefix="t_g1b_")
    try:
        work, out = _build(tmp, CORR_BOTH, error_forms=EF)
        rc1, log1 = _run_assemble(work, out)
        assert rc1 == 0, "FAIL: 首跑未通过：\n%s" % log1[:1500]
        # 改一笔内容制造真实差异，触发覆盖保护备份
        _w(os.path.join(work, "notes", "01.md"), NOTES + "\n## 4. 补章\n**本章论题**：补。\n")
        rc2, log2 = _run_assemble(work, out)
        assert rc2 == 0, "FAIL: 重跑未通过：\n%s" % log2[:1500]
        leftovers = [f for f in os.listdir(out) if ".bak" in f or f.endswith(".tmp")]
        assert not leftovers, "FAIL: 交付目录混入备份/临时文件：%s" % leftovers
        baks = []
        for root, _d, files in os.walk(work):
            baks += [os.path.join(root, f) for f in files if ".bak" in f]
        assert baks, "FAIL: 覆盖保护未留备份（可恢复性丢失）——备份应落在 work 下"
        assert all(os.path.basename(os.path.dirname(b)) == "_bak" for b in baks), \
            "FAIL: 备份未收敛到 _bak：%s" % baks
        print("✅ test_delivery_dir_has_no_bak_after_rerun: 重跑后交付目录无 .bak，备份收敛于 work/_bak")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_prereq_fails_loudly():
    """前置缺失：明确报错退出（不裸 traceback、不静默降级放行）。"""
    tmp = tempfile.mkdtemp(prefix="t_g1b_")
    try:
        work, out = _build(tmp, CORR_BOTH, error_forms=EF, drop=("page_index.json",))
        rc, log = _run_assemble(work, out)
        assert rc != 0, "FAIL: 缺前置却放行，rc=%d" % rc
        assert "前置缺失" in log, "FAIL: 未给出「前置缺失」的明确指引：\n%s" % log[:1500]
        assert "Traceback" not in log, "FAIL: 裸 traceback（排查成本高）：\n%s" % log[:1500]
        print("✅ test_missing_prereq_fails_loudly: 前置缺失 → 明确报错退出（非 traceback、不降级）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_residual_reported_not_blocking()
    test_gate1b_passes_when_clean()
    test_delivery_dir_has_no_bak_after_rerun()
    test_missing_prereq_fails_loudly()
    print("\nALL PASS · 错字清单复扫与交付卫生端到端回归通过"
          "（残留只报告 / 清零如实报 0 / 交付目录无 .bak / 前置缺失明确报错）。")
