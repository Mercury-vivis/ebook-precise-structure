#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""06 PDF 路径整页图版嵌入回归（2026-10-19 随图落地收口新增）。

覆盖：--apply 落图+引用（fig/ 子目录口径）、dry-run 零写盘、幂等重跑不重复插图、
存量旧形态（裸名引用）不重复插。此前 06 无任何测试，收口改动由此回归守着。

须经 run_tests.py（或 venv-ebook 解释器）驱动，勿用 WorkBuddy 自带解释器
（本测试用 pymupdf 造 PDF 夹具，06 生产同样依赖它）。
"""
import os
import shutil
import subprocess
import sys
import tempfile

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "06_embed_pdf_figures.py")

MD = """<<<PAGE phys=1 book=1>>>
附图1
成皋之战示意图
（本页为图版页，文字层仅剩散字碎片）
"""


def _w(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def _build(tmp):
    pdf = os.path.join(tmp, "book.pdf")
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)  # 空白页即够：06 整页渲染提图
    doc.save(pdf)
    doc.close()
    md = os.path.join(tmp, "03.md")
    _w(md, MD)
    bak = os.path.join(tmp, "_bak")
    return pdf, md, bak


def _run(pdf, md, bak, apply_):
    env = dict(os.environ)
    env["EBOOK_BAK_DIR"] = bak
    cmd = [sys.executable, SCRIPT, "--pdf", pdf, "--md", md,
           "--pages", "1"] + (["--apply"] if apply_ else [])
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=HERE, timeout=180, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _bak_files(bak):
    if not os.path.isdir(bak):
        return []
    return [f for f in os.listdir(bak) if "bak-embed" in f]


def test_06_apply_writes_figure():
    """--apply：rc=0、图落 fig/ 子目录、引用带 fig/ 前缀、备份落盘。"""
    tmp = tempfile.mkdtemp(prefix="t_06_")
    try:
        pdf, md, bak = _build(tmp)
        rc, log = _run(pdf, md, bak, True)
        assert rc == 0, "FAIL: 06 --apply 崩（rc=%d）：\n%s" % (rc, log[:2500])
        c03 = open(md, encoding="utf-8").read()
        assert "![附图1 成皋之战示意图](fig/fig_1.jpeg)" in c03, \
            "FAIL: 03 未按 fig/ 前缀写入图引用：\n%s" % c03[:800]
        assert os.path.isfile(os.path.join(tmp, "fig", "fig_1.jpeg")), "FAIL: 图未落 fig/ 子目录"
        assert not os.path.isfile(os.path.join(tmp, "fig_1.jpeg")), "FAIL: 图仍散落 03 同层（未收口）"
        assert _bak_files(bak), "FAIL: 改前备份未落 %s" % bak
        print("✅ test_06_apply_writes_figure: --apply 落图+fig/ 前缀引用+备份")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_06_dry_run_no_write():
    """dry-run：rc=0、零写盘（无 fig/、03 无引用、无备份）。"""
    tmp = tempfile.mkdtemp(prefix="t_06_dry_")
    try:
        pdf, md, bak = _build(tmp)
        rc, log = _run(pdf, md, bak, False)
        assert rc == 0, "FAIL: dry-run 异常 rc=%d：\n%s" % (rc, log[:2000])
        c03 = open(md, encoding="utf-8").read()
        assert not os.path.isdir(os.path.join(tmp, "fig")), "FAIL: dry-run 却建了 fig/"
        assert "fig_1.jpeg" not in c03, "FAIL: dry-run 却写了 03"
        assert not _bak_files(bak), "FAIL: dry-run 却建了备份"
        assert "[DRY-RUN]" in log, "FAIL: 未走 dry-run 分支"
        print("✅ test_06_dry_run_no_write: dry-run 零写盘")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_06_rerun_and_legacy_idempotent():
    """幂等：① 新形态（fig/ 前缀）重跑不重复插图；② 存量旧形态（裸名引用）重跑也不重复插图。"""
    tmp = tempfile.mkdtemp(prefix="t_06_idem_")
    try:
        pdf, md, bak = _build(tmp)
        rc, _ = _run(pdf, md, bak, True)
        assert rc == 0, "FAIL: 首遍 apply 失败"
        n1 = open(md, encoding="utf-8").read().count("](fig/fig_1.jpeg)")
        rc2, log2 = _run(pdf, md, bak, True)
        assert rc2 == 0, "FAIL: 新形态二遍 apply 崩（rc=%d）：\n%s" % (rc2, log2[:2000])
        n2 = open(md, encoding="utf-8").read().count("](fig/fig_1.jpeg)")
        assert n1 == n2 == 1, "FAIL: 新形态二遍重复插图（%d→%d）" % (n1, n2)

        # 存量旧形态：03 里是裸名引用 + 同层裸图（收口前的历史交付物）
        legacy = MD.replace("\n", "\n") + "\n\n![附图1 成皋之战示意图](fig_1.jpeg)\n"
        _w(md, legacy)
        with open(os.path.join(tmp, "fig_1.jpeg"), "wb") as f:
            f.write(b"LEGACY")
        rc3, log3 = _run(pdf, md, bak, True)
        assert rc3 == 0, "FAIL: 存量旧形态重跑崩（rc=%d）：\n%s" % (rc3, log3[:2000])
        c03 = open(md, encoding="utf-8").read()
        assert c03.count("](fig/fig_1.jpeg)") == 0, "FAIL: 存量旧形态被重复插图"
        assert c03.count("](fig_1.jpeg)") == 1, "FAIL: 存量旧形态引用被改动"
        assert "已嵌入·跳过" in log3, "FAIL: 存量旧形态未判已嵌入：\n%s" % log3[:1500]
        print("✅ test_06_rerun_and_legacy_idempotent: 新形态/存量旧形态重跑均不重复插图")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_06_apply_writes_figure()
    test_06_dry_run_no_write()
    test_06_rerun_and_legacy_idempotent()
    print("\nALL PASS · 06 PDF 路径整页图版回归通过（落 fig/ / dry-run 零写 / 幂等含存量兼容）。")
