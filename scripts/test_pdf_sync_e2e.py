#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF 路径端到端回归（真跑 pipeline_assemble.py，非桩测）。

覆盖两件事：
  1) P0 · 02 速查卡错形孤岛：pipeline_assemble 的 extract_events 吃原始 notes，
     而 01 用同步后文本——同一份交付里 01 已改正、02 仍留错形，02 名句在 03
     检索 0 命中。→ extract_events 支持 (fname, text) 元组，02 与 01 同源。
  2) 缺陷件不落盘（staging 定稿）：全部交付件先写**系统临时暂存区**（落点见
     `_common.make_staging_dir`——work 常在同步盘内，暂存区落进 work 会被文件锁撞断）。
     装配条件不成立（notes 解析不出章节等）sys.exit 时，真实交付目录不得含任何交付件。

探针错形用「珪璧」「府丘」「磨宋」——均不在 ocr_confusion_dict，避免词典类检查抢先。
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
    # v2.3.0 后置钩子：00_after.md 须被并入 00，且并入在指纹计算之前
    _w(os.path.join(work, "00_after.md"), "## 九、成书过程\n\n钩子测试：本段应出现在 00 末尾。\n")
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


def test_pdf_abort_leaves_out_empty():
    """staging 定稿：装配条件不成立时，真实交付目录不得含任何交付件（零残留）。"""
    tmp = tempfile.mkdtemp(prefix="t_pdf_rej_")
    try:
        # notes 写成空文件 → 编译不出任何章节 → 装配前硬校验 sys.exit（最典型的前置不成立）
        work, out = _build(tmp, CORR_ALL, notes="")
        rc, log = _run(work, out)
        assert rc != 0, "FAIL: notes 解析不出章节却放行，rc=%d\n%s" % (rc, log[:2500])
        assert "装配前硬校验" in log, "FAIL: 拒交付但未给出可排查的指引：\n%s" % log[:2500]
        mds = [f for f in os.listdir(out) if f.endswith(".md")]
        assert not mds, "FAIL: 中途退出后交付目录残留半成品：%s（staging 定稿语义被破坏）" % mds
        # 半成品应收敛在系统临时暂存区（可排查、不污染交付），且 work 内不得留任何 staging 痕迹
        staging = make_staging_dir(work)
        assert os.path.isdir(staging), "FAIL: 暂存区未在 %s（排查线索丢失）" % staging
        leaked = [f for f in os.listdir(work) if "staging" in f.lower()]
        assert not leaked, "FAIL: work 目录出现 staging 残留：%s（暂存区须落系统临时目录）" % leaked
        print("✅ test_pdf_abort_leaves_out_empty: 装配中断 → 交付目录零残留，半成品收敛于系统临时暂存区")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_pdf_terminal_gate():
    """S2（v2.2.0）：预检标注 ≠ 终审；定稿后自动跑完整终检；--no-gate 可跳过。

    历史缺口：assemble 内嵌 check_book 带 --no-delivery，输出形似终审，交付完整性
    （五件/格式/图引用/指纹）从未在装配链里验过（2026-10-22 实测三项真实问题漏到
    独立终检才暴露）。现接线进定稿段：默认跑、阻断项返 1、--no-gate 逃生。"""
    tmp = tempfile.mkdtemp(prefix="t_pdf_gate_")
    try:
        work, out = _build(tmp, CORR_ALL)
        rc, log = _run(work, out)
        assert rc == 0, "FAIL: 标准场景应终检通过（rc=0）：\n%s" % log[-3000:]
        assert "【预检】" in log and "--no-delivery" in log, \
            "FAIL: 内嵌预检未标注（预检≠终审的口径没写进输出）：\n%s" % log[-2000:]
        assert "终检：对交付目录跑完整 check_book" in log, \
            "FAIL: 定稿后未跑完整终检：\n%s" % log[-2000:]
        assert "✅ 终检通过" in log, "FAIL: 终检通过标记缺失：\n%s" % log[-2000:]
        # --no-gate 逃生：跳过终检且不出现过关标记
        cmd = [sys.executable, ASSEMBLE, "--work", work, "--out", out,
               "--meta", os.path.join(work, "book_meta.json"), "--no-gate"]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=HERE, timeout=180)
        log2 = (r.stdout or "") + (r.stderr or "")
        assert r.returncode == 0, "FAIL: --no-gate 场景 rc 应为 0：\n%s" % log2[-2000:]
        assert "--no-gate 指定，跳过" in log2, "FAIL: --no-gate 未跳过终检：\n%s" % log2[-2000:]
        assert "✅ 终检通过" not in log2, "FAIL: --no-gate 不应出现终检通过标记"
        print("✅ test_pdf_terminal_gate: 预检标注 / 定稿后自动终检 / --no-gate 跳过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_pdf_00_after_hook():
    """v2.3.0：work/00_after.md 并入 00 末尾，且并入在指纹计算之前（否则 check_book 必报不符）。"""
    tmp = tempfile.mkdtemp(prefix="t_pdf_after_")
    try:
        work, out = _build(tmp, CORR_ALL)
        rc, log = _run(work, out)
        assert rc == 0, "FAIL: 带 00_after.md 的装配未通过（rc=%d）：\n%s" % (rc, log[:3000])
        c00 = _read(out, "00_")
        marker = "钩子测试：本段应出现在 00 末尾。"
        assert marker in c00, "FAIL: 00_after.md 未并入 00"
        assert c00.count(marker) == 1, "FAIL: 钩子内容重复并入"
        c04 = _read(out, "04_")
        assert "sha256:" in c04, "FAIL: 04 未记交付指纹"
        # 终检已跑（rc=0 即指纹一致）；再把「指纹包含钩子后的 00」正面确认一次：
        # 若钩子在指纹之后追加，终检会报指纹不符、rc 非 0，上面已拦住。
        print("✅ test_pdf_00_after_hook: 钩子并入 00 / 不重复 / 指纹自洽")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_pdf_02_synced_no_island()
    test_pdf_abort_leaves_out_empty()
    test_pdf_terminal_gate()
    test_pdf_00_after_hook()
    print("\nALL PASS · PDF 路径端到端回归通过（02 同源同步 / 装配中断零残留 / 定稿终检接线 / 00 后置钩子）。")
