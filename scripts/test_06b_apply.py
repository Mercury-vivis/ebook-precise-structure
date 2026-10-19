#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""06b EPUB 图号通道 --apply 冒烟 + dry-run 不写盘 + 空 plan 不崩（2026-10-19 P0 回归）。

固化 bug：06b:387 曾把未定义的 `new` 传给 safe_write（实际改写 text），--apply 100%
NameError，且崩前备份已落盘——每次重跑白留一份备份、图永远写不进 03。
本测试在当前（修复后）代码上须全绿；若 regression（new 复活）则 test_apply 必红。

须经 run_tests.py（或 venv-ebook 解释器）驱动，勿用 WorkBuddy 自带解释器。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "06b_embed_epub_figures.py")

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf">
  <manifest>
    <item id="c1" href="c1.xhtml"/>
    <item id="img1" href="img/fig1.jpeg"/>
  </manifest>
  <spine><itemref idref="c1"/></spine>
</package>
"""
# tuti 题必须在 <img> 之后（06b 从 img 后 600 字符窗口抓 class="tuti" 题，权威结构）
XHTML = """<html><body>
<p>第一章正文。</p>
<img id="fig1" src="img/fig1.jpeg"/>
<p class="tuti"><b>图1　测试图</b></p>
<p>后续正文。</p>
</body></html>
"""
MD = """<<<PAGE phys=1 book=1>>>
第一章正文。
图1　测试图
后续正文提到（见图1）。
"""
MD_NO_ANCHOR = """<<<PAGE phys=1 book=1>>>
正文，没有任何图号行或引用。
"""
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"FAKEJPEGDATA" * 64  # 内容随意，06b 只解zip落盘


def _w(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def _build(tmp, md_text=MD):
    epub = os.path.join(tmp, "book.epub")
    with zipfile.ZipFile(epub, "w") as z:
        z.writestr("content.opf", OPF)
        z.writestr("c1.xhtml", XHTML)
        z.writestr("img/fig1.jpeg", FAKE_JPEG)
    md = os.path.join(tmp, "03.md")
    _w(md, md_text)
    bak = os.path.join(tmp, "_bak")
    return epub, md, bak


def _run(epub, md, bak, apply_):
    env = dict(os.environ)
    env["EBOOK_BAK_DIR"] = bak  # 备份隔离到 tmp，不污染交付目录
    cmd = [sys.executable, SCRIPT, epub, md] + (["--apply"] if apply_ else [])
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=HERE, timeout=120, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _bak_files(bak):
    if not os.path.isdir(bak):
        return []
    return [f for f in os.listdir(bak) if "bak-embed" in f]


def test_06b_apply_writes_figure():
    """--apply：rc=0、03 写入图片引用、原图落 03 同层、备份落盘（崩点回归）。"""
    tmp = tempfile.mkdtemp(prefix="t_06b_")
    try:
        epub, md, bak = _build(tmp)
        rc, log = _run(epub, md, bak, True)
        assert rc == 0, "FAIL: 06b --apply 崩（rc=%d）——387 行变量断裂可能复现：\n%s" % (rc, log[:2500])
        c03 = open(md, encoding="utf-8").read()
        # alt 口径（06b:372-373）：题文本以编号开头时 alt 只取编号，不重复题
        # 落点口径（2026-10-19 收口）：图归 03 同层 fig/ 子目录，引用带 fig/ 前缀
        assert "![图1](fig/fig_图1.jpeg)" in c03, \
            "FAIL: 03 未写入图引用（apply 未生效或形态变了）：\n%s" % c03[:800]
        assert os.path.isfile(os.path.join(tmp, "fig", "fig_图1.jpeg")), "FAIL: 原图未复制到 fig/ 子目录"
        assert not os.path.isfile(os.path.join(tmp, "fig_图1.jpeg")), "FAIL: 图仍散落 03 同层（未收口）"
        assert _bak_files(bak), "FAIL: 改前备份未落 %s" % bak
        assert "计划嵌入图数: 1" in log, "FAIL: 计划路径异常：\n%s" % log[:1500]
        print("✅ test_06b_apply_writes_figure: --apply 落图+引用+备份（387 崩点已固化为绿）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_06b_dry_run_no_write():
    """dry-run：rc=0、零写盘（03 无引用、无图文件、无备份）。"""
    tmp = tempfile.mkdtemp(prefix="t_06b_dry_")
    try:
        epub, md, bak = _build(tmp)
        rc, log = _run(epub, md, bak, False)
        assert rc == 0, "FAIL: dry-run 异常 rc=%d：\n%s" % (rc, log[:2000])
        c03 = open(md, encoding="utf-8").read()
        assert "fig_图1.jpeg" not in c03, "FAIL: dry-run 却写了 03"
        assert not os.path.isfile(os.path.join(tmp, "fig_图1.jpeg")), "FAIL: dry-run 却落了图"
        assert not _bak_files(bak), "FAIL: dry-run 却建了备份"
        assert "[DRY-RUN]" in log, "FAIL: 未走 dry-run 分支"
        print("✅ test_06b_dry_run_no_write: dry-run 零写盘")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_06b_empty_plan_apply_ok():
    """空 plan --apply：曾必崩（无论 plan 有无图都流到 387）；修复后 rc=0 且 03 原样不动。"""
    tmp = tempfile.mkdtemp(prefix="t_06b_empty_")
    try:
        epub, md, bak = _build(tmp, md_text=MD_NO_ANCHOR)
        before = open(md, encoding="utf-8").read()
        rc, log = _run(epub, md, bak, True)
        assert rc == 0, "FAIL: 空 plan --apply 崩（rc=%d）：\n%s" % (rc, log[:2500])
        after = open(md, encoding="utf-8").read()
        assert after == before, "FAIL: 空 plan 却改坏了 03"
        assert not os.path.isfile(os.path.join(tmp, "fig_图1.jpeg")), "FAIL: 空 plan 却落了图（同层）"
        assert not os.path.isfile(os.path.join(tmp, "fig", "fig_图1.jpeg")), "FAIL: 空 plan 却落了图（fig/）"
        print("✅ test_06b_empty_plan_apply_ok: 空 plan --apply 不崩、不误写（崩点全形态覆盖）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_06b_rerun_idempotent():
    """幂等（2026-10-19 补）：第二遍 --apply 不重复插图（06b 原无幂等检测，曾重跑即双引用）。"""
    tmp = tempfile.mkdtemp(prefix="t_06b_idem_")
    try:
        epub, md, bak = _build(tmp)
        rc, _ = _run(epub, md, bak, True)
        assert rc == 0, "FAIL: 首遍 apply 失败"
        n_before = open(md, encoding="utf-8").read().count("](fig/fig_图1.jpeg)")
        rc2, log2 = _run(epub, md, bak, True)
        assert rc2 == 0, "FAIL: 二遍 --apply 崩（rc=%d）：\n%s" % (rc2, log2[:2000])
        n_after = open(md, encoding="utf-8").read().count("](fig/fig_图1.jpeg)")
        assert n_after == n_before == 1, "FAIL: 二遍 apply 重复插图（%d→%d）" % (n_before, n_after)
        print("✅ test_06b_rerun_idempotent: 二遍 --apply 不重复插图")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_06b_apply_writes_figure()
    test_06b_dry_run_no_write()
    test_06b_empty_plan_apply_ok()
    test_06b_rerun_idempotent()
    print("\nALL PASS · 06b EPUB 图号通道回归通过（apply 落图 / dry-run 零写 / 空 plan 不崩 / 幂等重跑）。")
