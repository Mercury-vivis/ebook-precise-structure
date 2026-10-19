#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""06c EPUB 切片型占位对齐通道 --apply 冒烟 + dry-run 不写盘（2026-10-19 P0 回归）。

固化 bug：06c:194 曾把未定义的 `new` 传给 safe_write（实际改写 newtext），--apply
100% NameError——与 06b:387 同族同疾（复制粘贴丢变量），崩前备份已落盘。
另覆盖 k↔k 对齐核验与"小图元占位符原样保留"契约。

须经 run_tests.py（或 venv-ebook 解释器）驱动，勿用 WorkBuddy 自带解释器。
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "06c_embed_epub_slice_figs.py")

# PLACEHOLDER 与实现同源（禁硬编码漂移）：按路径加载数字前缀模块取常量
_spec = importlib.util.spec_from_file_location("m06c", SCRIPT)
_m06c = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m06c)
PLACEHOLDER = _m06c.PLACEHOLDER

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf">
  <manifest>
    <item id="c1" href="c1.xhtml"/>
  </manifest>
  <spine><itemref idref="c1"/></spine>
</package>
"""
# 小图元（脚注星号 png）在前、真图版（大 jpg）在后；各带一个 <p> 题
XHTML = """<html><body>
<p>正文起。</p>
<p>脚注星号</p>
<img src="img/star.png"/>
<p>图版说明甲</p>
<img src="img/big.jpg"/>
<p>正文续。</p>
</body></html>
"""
MD = """<<<PAGE phys=1 book=1>>>
正文起。
%s
图版说明甲
%s
正文续。
""" % (PLACEHOLDER, PLACEHOLDER)
BIG = b"\xff\xd8\xff\xe0" + b"BIGFIG" * 8000   # ≈40KB ≥ 30000B 阈值
SMALL = b"\x89PNG" + b"S" * 100               # 100B < 阈值，判小图元


def _w(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def _build(tmp):
    epub = os.path.join(tmp, "book.epub")
    with zipfile.ZipFile(epub, "w") as z:
        z.writestr("content.opf", OPF)
        z.writestr("c1.xhtml", XHTML)
        z.writestr("img/big.jpg", BIG)
        z.writestr("img/star.png", SMALL)
    md = os.path.join(tmp, "03.md")
    _w(md, MD)
    bak = os.path.join(tmp, "_bak")
    return epub, md, bak


def _run(epub, md, bak, apply_):
    env = dict(os.environ)
    env["EBOOK_BAK_DIR"] = bak
    cmd = [sys.executable, SCRIPT, epub, md] + (["--apply"] if apply_ else [])
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=HERE, timeout=120, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _bak_files(bak):
    if not os.path.isdir(bak):
        return []
    return [f for f in os.listdir(bak) if "bak-embed" in f]


def test_06c_apply_writes_figure():
    """--apply：rc=0、真图版嵌 imgs/、小图元占位符原样保留、备份落盘（崩点回归）。"""
    tmp = tempfile.mkdtemp(prefix="t_06c_")
    try:
        epub, md, bak = _build(tmp)
        rc, log = _run(epub, md, bak, True)
        assert rc == 0, "FAIL: 06c --apply 崩（rc=%d）——194 行变量断裂可能复现：\n%s" % (rc, log[:2500])
        c03 = open(md, encoding="utf-8").read()
        assert "![图版说明甲](imgs/fig_001_01.jpg)" in c03, \
            "FAIL: 03 未按 imgs/ 前缀写入图引用：\n%s" % c03[:800]
        img = os.path.join(tmp, "imgs", "fig_001_01.jpg")
        assert os.path.isfile(img), "FAIL: 真图版未落 imgs/"
        assert os.path.getsize(img) == len(BIG), "FAIL: 落盘图内容与 EPUB 源不一致"
        assert c03.count(PLACEHOLDER) == 1, \
            "FAIL: 小图元占位符未原样保留（k↔k 契约）：%d 个" % c03.count(PLACEHOLDER)
        assert _bak_files(bak), "FAIL: 改前备份未落 %s" % bak
        assert "真图版（jpg≥30000B）1" in log, "FAIL: 真图版计数异常：\n%s" % log[:1500]
        print("✅ test_06c_apply_writes_figure: --apply 真图版入 imgs/、小图元占位保留（194 崩点已固化为绿）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_06c_dry_run_no_write():
    """dry-run：rc=0、零写盘（无 imgs/、占位符不动、无备份）。"""
    tmp = tempfile.mkdtemp(prefix="t_06c_dry_")
    try:
        epub, md, bak = _build(tmp)
        rc, log = _run(epub, md, bak, False)
        assert rc == 0, "FAIL: dry-run 异常 rc=%d：\n%s" % (rc, log[:2000])
        c03 = open(md, encoding="utf-8").read()
        assert not os.path.isdir(os.path.join(tmp, "imgs")), "FAIL: dry-run 却建了 imgs/"
        assert c03.count(PLACEHOLDER) == 2, "FAIL: dry-run 却动了占位符"
        assert not _bak_files(bak), "FAIL: dry-run 却建了备份"
        assert "[DRY-RUN]" in log, "FAIL: 未走 dry-run 分支"
        print("✅ test_06c_dry_run_no_write: dry-run 零写盘")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_06c_apply_writes_figure()
    test_06c_dry_run_no_write()
    print("\nALL PASS · 06c EPUB 切片型通道回归通过（apply 落 imgs/ / dry-run 零写）。")
