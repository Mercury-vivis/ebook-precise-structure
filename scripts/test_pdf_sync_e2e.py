#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF 路径端到端回归（真跑 pipeline_assemble.py，非桩测）。

对应 2026-10-05 第三轮复审（四专家）两处发现：
  1) P0 · 02 速查卡错形孤岛：pipeline_assemble 的 extract_events 吃原始 notes，
     而 01 用同步后文本——同一份交付里 01 已改正、02 仍留错形，02 名句在 03
     检索 0 命中，且交付级复扫整单拒付，PDF 标准场景无法出件。
     → 修复后 extract_events 支持 (fname, text) 元组，02 与 01 同源（同步文本）。
  2) P1 · 拒交付零残留（staging 定稿）：全部交付件先写**系统临时暂存区**（落点见
     `_common.make_staging_dir`——work 常在同步盘内，暂存区落进 work 会被文件锁撞断），
     强制闸（闸0/闸1b/闸2）+ 交付级复扫全过才定稿到真实交付目录；任一经拒的强制闸 sys.exit
     时交付目录不得含任何交付件（闸1/闸3/闸4 为非阻断项，不影响定稿与退出码）。

探针错形选「珪璧」——不在 ocr_confusion_dict（避免闸2 词典抢先于闸1b）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

from _common import make_staging_dir  # 交付暂存区落点：测试不得另行写死路径，否则实现一改就漂移

HERE = os.path.dirname(os.path.abspath(__file__))
ASSEMBLE = os.path.join(HERE, "pipeline_assemble.py")

STRUCT = (
    "<<<PAGE phys=1 book=1 title=第一章>>>\n"
    "荀或曰：秦汉之制，盖自秦汉始。\n"
    "磨宋之役，府丘制兴。\n"
    "<<<PAGE phys=2 book=2 title=第二章>>>\n"
    "夫兵者，国之大事。\n"
    "府丘制兴于两代。\n"
    "执珪璧以朝诸侯。\n"
)
# notes 刻意写成**未修正**形态（真实分章 Agent 就是这么写的：先读原文、后定台账）
NOTES_WRONG = (
    "## 1. 第一章\n**本章论题**：论制度之起源。\n**核心概念**：\n- 制度：自秦汉始（PAGE phys=1）。\n"
    "**关键原句摘录**：\n> 荀或曰：秦汉之制，盖自秦汉始。（PAGE phys=1）\n\n"
    "## 2. 第二章\n**本章论题**：论兵制。\n**核心概念**：\n- 府兵：兴起于两代（PAGE phys=2）。\n"
    "**关键原句摘录**：\n> 磨宋之役，府丘制兴。（PAGE phys=2）\n"
)
META = {
    "book": "测试书PDF", "author": "某", "type": "② 思想/理论型", "source": "测试",
    "total_phys": 2,
    "chapters": [{"no": 1, "name": "第一章", "phys_start": 1},
                 {"no": 2, "name": "第二章", "phys_start": 2}],
}
EF = ("荀或|荀彧|形近：或→彧\n府丘|府兵|形近：丘→兵\n磨宋|磨勘|形近：宋→勘\n"
      "珪璧|圭璧|形近：珪→圭\n")
CORR_ALL = [
    {"phys": "ALL", "old": "荀或", "new": "荀彧", "systematic": True, "reason": "形近：或→彧"},
    {"phys": "ALL", "old": "府丘", "new": "府兵", "systematic": True, "reason": "形近：丘→兵"},
    {"phys": "ALL", "old": "磨宋", "new": "磨勘", "systematic": True, "reason": "形近：宋→勘"},
    {"phys": "ALL", "old": "珪璧", "new": "圭璧", "systematic": True, "reason": "形近：珪→圭"},
]


def _w(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def _build(tmp, corrections, error_forms=EF, notes=NOTES_WRONG):
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "out")
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    _w(os.path.join(work, "fulltext_struct.txt"), STRUCT)
    _w(os.path.join(work, "book_meta.json"), json.dumps(META, ensure_ascii=False))
    _w(os.path.join(work, "corrections", "01.json"), json.dumps(corrections, ensure_ascii=False))
    _w(os.path.join(work, "notes", "01.md"), notes)
    if error_forms is not None:
        _w(os.path.join(work, "error_forms.txt"), error_forms)
    return work, out


def _run(work, out):
    cmd = [sys.executable, ASSEMBLE, "--work", work, "--out", out,
           "--meta", os.path.join(work, "book_meta.json")]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=HERE, timeout=180)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _read(out, suffix):
    for f in os.listdir(out):
        if suffix in f and f.endswith(".md"):
            with open(os.path.join(out, f), encoding="utf-8") as fh:
                return fh.read()
    return ""


def test_pdf_02_synced_no_island():
    """P0：02 速查卡须与 01 同源（同步后文本）——02 无错形孤岛、名句可在 03 命中。"""
    tmp = tempfile.mkdtemp(prefix="t_pdf_")
    try:
        work, out = _build(tmp, CORR_ALL)
        rc, log = _run(work, out)
        assert rc == 0, "FAIL: PDF 标准场景未出件（rc=%d）——02 同步或 staging 定稿异常：\n%s" % (rc, log[:3000])
        c02 = _read(out, "02_")
        c03 = _read(out, "03_")
        assert c02, "FAIL: 02 未生成"
        assert c03, "FAIL: 03 未生成"
        # 02 不得残留已判定错形（01 同步、02 也必须同步）
        for bad in ("荀或", "磨宋", "府丘", "珪璧"):
            assert bad not in c02, "FAIL: 02 仍残留错形「%s」（02 未用同步文本，P0 未修复）" % bad
        # 02 名句与 03 同形态、可检索（「可检索/可回溯」契约）
        assert "磨勘之役，府兵制兴" in c02, "FAIL: 02 名句未同步为正确形态：\n%s" % c02[:800]
        assert "磨勘之役，府兵制兴" in c03, "FAIL: 03 未含正确形态名句"
        # 交付卫生：无 .bak/.tmp，staging 不泄漏进交付目录
        leftovers = [f for f in os.listdir(out) if ".bak" in f or f.endswith(".tmp") or "staging" in f]
        assert not leftovers, "FAIL: 交付目录混入备份/临时/staging 文件：%s" % leftovers
        print("✅ test_pdf_02_synced_no_island: 02 与 01 同源（同步文本），无错形孤岛，名句可在 03 命中")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_pdf_reject_leaves_out_empty():
    """staging 定稿：闸1b 拒交付时真实交付目录不得含任何交付件（零残留）。"""
    tmp = tempfile.mkdtemp(prefix="t_pdf_rej_")
    try:
        # 台账缺珪璧补丁 → 03 残留珪璧 → 闸1b 拒付（珪璧不在词典，闸2 不抢占）
        corr_no_gb = [c for c in CORR_ALL if c["old"] != "珪璧"]
        work, out = _build(tmp, corr_no_gb)
        rc, log = _run(work, out)
        assert rc != 0, "FAIL: 03 残留珪璧却放行，rc=%d\n%s" % (rc, log[:2500])
        assert "闸1b" in log, "FAIL: 拒交付但未点名闸1b：\n%s" % log[:2500]
        assert "珪璧" in log, "FAIL: 拒交付但未点名错形：\n%s" % log[:2500]
        mds = [f for f in os.listdir(out) if f.endswith(".md")]
        assert not mds, "FAIL: 拒交付后交付目录残留半成品：%s（staging 定稿语义被破坏）" % mds
        # 半成品应收敛在系统临时暂存区（可排查、不污染交付），且 work 内不得留任何 staging 痕迹
        staging = make_staging_dir(work)
        assert os.path.isdir(staging), "FAIL: 暂存区未在 %s（排查线索丢失）" % staging
        leaked = [f for f in os.listdir(work) if "staging" in f.lower()]
        assert not leaked, "FAIL: work 目录出现 staging 残留：%s（暂存区须落系统临时目录）" % leaked
        print("✅ test_pdf_reject_leaves_out_empty: 闸1b 拒交付 → 交付目录零残留，半成品收敛于系统临时暂存区")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_pdf_02_synced_no_island()
    test_pdf_reject_leaves_out_empty()
    print("\nALL PASS · PDF 路径端到端回归通过（02 同源同步 / staging 拒交付零残留）。")
