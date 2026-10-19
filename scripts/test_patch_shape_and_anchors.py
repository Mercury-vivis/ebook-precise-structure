#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补丁形态一致性与锚点硬闸 · 单元测试（2026-10-05 第二轮审查 P0）。

  1) 顶层单条 dict 的补丁文件：施加侧会改 03，但旧版 patch_manifest / 04 改错留痕 /
     verifier / apply_report 各自写 `data.get("patches", [])` 把整份丢弃 ——
     形成「正文改了、台账无记录」的假通过链。现统一走 expand_patch_data 单一加载口径。
  2) 锚点部分丢失：旧版只在**全部**锚丢失时硬失败；部分锚不被识别时对应页正文
     根本不进 03（整页静默丢失），而所有闸仍报通过。现一律硬失败。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from _common import expand_patch_data, check_anchors  # noqa: E402

STRUCT = ("<<<PAGE phys=1 book=1 title=第一章>>>\n荀或曰：秦汉之制。\n"
          "<<<PAGE phys=2 book=2 title=第二章>>>\n夫兵者，国之大事。\n")


def _w(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def test_single_dict_patch_in_manifest_and_ledger():
    """顶层单条 dict 的补丁文件：施加侧与台账侧口径必须一致（不得改了正文却无记录）。"""
    tmp = tempfile.mkdtemp(prefix="t_shape_")
    try:
        work = os.path.join(tmp, "work")
        out = os.path.join(tmp, "out")
        os.makedirs(os.path.join(work, "corrections"), exist_ok=True)
        os.makedirs(out, exist_ok=True)
        # 顶层单条 dict（不是 list、也不是 {"patches": [...]}）
        single = {"phys": "ALL", "old": "荀或", "new": "荀彧",
                  "systematic": True, "reason": "形近：或→彧"}
        _w(os.path.join(work, "corrections", "01.json"),
            json.dumps(single, ensure_ascii=False))
        # ① expand_patch_data：单条 dict 须展开成 1 条（旧口径返回 0 条 → 整份丢弃）
        got = expand_patch_data(single)
        assert len(got) == 1, "FAIL: 顶层单条 dict 未展开（%r）——台账侧会整份丢弃" % (got,)
        # ② gen_patch_manifest 须把它记进 manifest（否则 04 / verifier 无从对账）
        r = subprocess.run([sys.executable, os.path.join(HERE, "gen_patch_manifest.py"),
                            "--work", work],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=HERE, timeout=120)
        assert r.returncode == 0, "FAIL: gen_patch_manifest 失败：\n%s" % r.stderr[:800]
        mp = os.path.join(work, "patch_manifest.json")
        assert os.path.isfile(mp), "FAIL: 未产出 patch_manifest.json"
        manifest = json.load(open(mp, encoding="utf-8-sig"))
        n = len(manifest.get("patches", []))
        assert n == 1, "FAIL: 单条 dict 补丁未进 manifest（记录数 %d）——改了正文却无台账" % n
        assert manifest["patches"][0]["old"] == "荀或", "FAIL: manifest 内容不符"
        # ③ 04 改错留痕须有该条
        _w(os.path.join(work, "book_meta.json"),
            json.dumps({"book": "测试书", "chapters": []}, ensure_ascii=False))
        r2 = subprocess.run([sys.executable, os.path.join(HERE, "gen_correction_ledger.py"),
                             "--work", work, "--out", out,
                             "--meta", os.path.join(work, "book_meta.json"),
                             "--prefix", "t_"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=HERE, timeout=120)
        assert r2.returncode == 0, "FAIL: gen_correction_ledger 失败：\n%s" % r2.stderr[:800]
        f04 = [f for f in os.listdir(out) if f.endswith("04_改错留痕.md")]
        assert f04, "FAIL: 未产出 04"
        body = open(os.path.join(out, f04[0]), encoding="utf-8").read()
        assert "荀或" in body and "荀彧" in body, \
            "FAIL: 04 台账缺该条单条 dict 补丁（正文改了却无留痕）"
        print("✅ test_single_dict_patch_in_manifest_and_ledger: "
              "单条 dict 补丁在 manifest 与 04 均有记录（施加侧/台账侧口径一致）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_partial_anchor_loss_hard_fails():
    """部分锚不被识别 → 硬失败（该页正文根本不进 03，属整页静默丢失）。"""
    # 03 只解析出 1 个锚，struct 有 2 个 → 差 1 个（旧版只告警并继续）
    clean03 = "# 《测试书》\n\n<<<PAGE phys=1 book=1>>>\n荀彧曰：秦汉之制。\n"
    try:
        check_anchors(STRUCT, clean03)
    except SystemExit as e:
        assert e.code != 0, "FAIL: 部分锚丢失以 0 退出（等同放行）"
        print("✅ test_partial_anchor_loss_hard_fails: 部分锚丢失 → 硬失败（整页静默丢失不可放行）")
        return
    raise AssertionError("FAIL: 部分锚丢失未终止（该页正文不进 03，全闸仍报通过）")


def test_anchor_all_present_passes():
    """锚齐全 → 正常通过（不得误伤）。"""
    clean03 = ("# 《测试书》\n\n<<<PAGE phys=1 book=1>>>\n荀彧曰：秦汉之制。\n"
               "<<<PAGE phys=2 book=2>>>\n夫兵者，国之大事。\n")
    n_struct, n_anchor = check_anchors(STRUCT, clean03)
    assert n_struct == 2 and n_anchor == 2, "FAIL: 锚齐全却判不一致：%s/%s" % (n_struct, n_anchor)
    print("✅ test_anchor_all_present_passes: 锚齐全 → 正常通过（无误伤）")


def test_char_class_activates_for_mapped_book_types():
    """P2-10：char_class 的 book_types 须与 ①–⑩ 类号对齐，否则规则永不激活。

    磨 的 ctx（磨(?=文)）仅对 ④/⑥ 书型触发，对 ② 不触发。通过「上下文命中」
    字样隔离 char_class 命中，避免与 COMPOUND（磨文难灭）混淆。
    """
    from ocr_confusion_dict import scan
    text = "磨文难灭，宰相之职。"  # 磨后跟 文 → 命中 ctx；同时是 COMPOUND 键（复合串另计）
    hits_04 = scan(text, "④ 制度/沿革型")
    assert any("上下文命中" in h for h in hits_04), \
        "FAIL: ④ 书型下 磨 char_class 未激活：%s" % hits_04
    hits_06 = scan(text, "⑥ 通史/断代型")
    assert any("上下文命中" in h for h in hits_06), \
        "FAIL: ⑥ 书型下 磨 char_class 未激活：%s" % hits_06
    hits_02 = scan(text, "② 思想/理论型")
    assert not any("上下文命中" in h for h in hits_02), \
        "FAIL: ② 书型不该触发 磨 char_class：%s" % hits_02
    # 带括号子类标注的书型仍应命中（前缀匹配）
    hits_04b = scan(text, "④ 制度/沿革型（职官）")
    assert any("上下文命中" in h for h in hits_04b), \
        "FAIL: ④ 子类标注书型未命中（前缀匹配失效）：%s" % hits_04b
    print("✅ test_char_class_activates_for_mapped_book_types: 磨 仅对 ④/⑥ 激活（修复命名错位）")


if __name__ == "__main__":
    test_single_dict_patch_in_manifest_and_ledger()
    test_partial_anchor_loss_hard_fails()
    test_anchor_all_present_passes()
    test_char_class_activates_for_mapped_book_types()
    print("\nALL PASS · 补丁形态一致性与锚点硬闸回归通过"
          "（单条 dict 进台账 / 部分锚丢失硬失败 / 锚齐全不误伤 / char_class 书型对齐）。")
