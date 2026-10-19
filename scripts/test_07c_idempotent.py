#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""07c 正文内嵌图版嵌入器幂等回归（2026-10-19 P0 回归）。

固化 bug：07c:143 的 kept 元组是 3 元素（(fname, None, "已嵌入")），而 :148 是 4 元素、
:156/:170 两处均按 4 解包 → 03 已有 fig/ 引用时（幂等重跑或预置引用），打印阶段即
ValueError——连 dry-run 都崩，与 docstring 自称的"幂等"直接相反。
本测试在当前（修复后）代码上须全绿；若 regression 则幂等两例必红。

须经 run_tests.py（或 venv-ebook 解释器）驱动，勿用 WorkBuddy 自带解释器
（本测试用 pymupdf 造 PDF 夹具，07c 生产同样依赖它）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "07c_embed_figures.py")

MD = "<<<PAGE phys=1 book=1>>>\n正文第一段，此页有一幅线描示意图。\n"


def _probe(n=1):
    figs = [{"rect": [50, 50, 300, 400]}]
    if n == 2:
        figs.append({"rect": [50, 450, 300, 700]})
    return {"records": [{"phys": 1, "figures": figs,
                         "captions": [{"text": "示意图"}], "note": {}}]}


def _classify(n=1):
    figs = [{"k": 1, "verdict": "line", "features": {}}]
    if n == 2:
        figs.append({"k": 2, "verdict": "line", "features": {}})
    return {"verdicts": [{"phys": 1, "captions": [], "note": "", "figures": figs}]}


def _w(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def _build(tmp):
    pdf = os.path.join(tmp, "book.pdf")
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)  # 空白页即够：07c 只按 bbox 裁切渲染
    doc.save(pdf)
    doc.close()
    md = os.path.join(tmp, "03.md")
    _w(md, MD)
    probe = os.path.join(tmp, "probe.json")
    cls = os.path.join(tmp, "classify.json")
    _w(probe, json.dumps(_probe(1), ensure_ascii=False))
    _w(cls, json.dumps(_classify(1), ensure_ascii=False))
    bak = os.path.join(tmp, "_bak")
    return pdf, md, probe, cls, bak


def _run(pdf, md, probe, cls, bak, apply_):
    env = dict(os.environ)
    env["EBOOK_BAK_DIR"] = bak
    cmd = [sys.executable, SCRIPT, "--pdf", pdf, "--md", md,
           "--probe", probe, "--classify", cls] + (["--apply"] if apply_ else [])
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=HERE, timeout=180, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def test_07c_first_apply():
    """首遍 --apply：rc=0、裁图落 fig/、引用插在锚后。"""
    tmp = tempfile.mkdtemp(prefix="t_07c_")
    try:
        pdf, md, probe, cls, bak = _build(tmp)
        rc, log = _run(pdf, md, probe, cls, bak, True)
        assert rc == 0, "FAIL: 07c 首遍 --apply 崩（rc=%d）：\n%s" % (rc, log[:2500])
        c03 = open(md, encoding="utf-8").read()
        assert "![示意图（原书页1）](fig/fig_p0001_1.png)" in c03, \
            "FAIL: 03 未写入 fig/ 引用：\n%s" % c03[:800]
        assert os.path.isfile(os.path.join(tmp, "fig", "fig_p0001_1.png")), "FAIL: 裁图未落 fig/"
        print("✅ test_07c_first_apply: 首遍 --apply 落图+引用")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_07c_idempotent_rerun():
    """幂等重跑：第二遍 --apply 与 dry-run 均 rc=0、不重复插图（3/4 元组崩点回归）。"""
    tmp = tempfile.mkdtemp(prefix="t_07c_idem_")
    try:
        pdf, md, probe, cls, bak = _build(tmp)
        rc, _ = _run(pdf, md, probe, cls, bak, True)
        assert rc == 0, "FAIL: 首遍 apply 失败，无法验幂等"
        c03 = open(md, encoding="utf-8").read()
        n_before = c03.count("fig/fig_p0001_1.png")

        rc2, log2 = _run(pdf, md, probe, cls, bak, True)   # 第二遍 --apply（03 已有引用）
        assert rc2 == 0, "FAIL: 幂等 --apply 崩（rc=%d）——kept 元组宽度可能复现：\n%s" % (rc2, log2[:2500])
        c03 = open(md, encoding="utf-8").read()
        assert c03.count("fig/fig_p0001_1.png") == n_before, "FAIL: 幂等重跑重复插图"
        assert len(os.listdir(os.path.join(tmp, "fig"))) == 1, "FAIL: 幂等重跑重复裁图"

        rc3, log3 = _run(pdf, md, probe, cls, bak, False)  # 第三遍 dry-run（同样已有引用）
        assert rc3 == 0, "FAIL: 幂等 dry-run 也崩（rc=%d）——打印阶段解包可能复现：\n%s" % (rc3, log3[:2500])
        assert "已嵌入" in log3, "FAIL: dry-run 未识别已嵌入项：\n%s" % log3[:1500]
        print("✅ test_07c_idempotent_rerun: 二遍 --apply / dry-run 均不崩、不重复插图（幂等自称成立）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_07c_mixed_shape_dry_run():
    """混合形状：03 已有部分引用 + probe 新增一幅 → dry-run 打印阶段混合解包须不崩。"""
    tmp = tempfile.mkdtemp(prefix="t_07c_mix_")
    try:
        pdf, md, probe, cls, bak = _build(tmp)
        rc, _ = _run(pdf, md, probe, cls, bak, True)
        assert rc == 0, "FAIL: 首遍 apply 失败"
        # 升级 probe/classify 到 2 幅：第 1 幅走"已嵌入"分支、第 2 幅走正常分支
        _w(probe, json.dumps(_probe(2), ensure_ascii=False))
        _w(cls, json.dumps(_classify(2), ensure_ascii=False))
        rc2, log2 = _run(pdf, md, probe, cls, bak, False)
        assert rc2 == 0, "FAIL: 混合形状 dry-run 崩（rc=%d）——3/4 元组并存解包：\n%s" % (rc2, log2[:2500])
        assert "已嵌入" in log2 and "fig_p0001_2.png" in log2, \
            "FAIL: 混合计划输出不完整：\n%s" % log2[:1500]
        print("✅ test_07c_mixed_shape_dry_run: 混合形状（已嵌入+待嵌）dry-run 不崩")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_07c_first_apply()
    test_07c_idempotent_rerun()
    test_07c_mixed_shape_dry_run()
    print("\nALL PASS · 07c 幂等回归通过（首嵌 / 重跑不崩 / 混合形状）。")
