#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""施加侧幂等护栏回归测试（v1.34.3，任务E 后续）。

加字型通改（new 是 old 的子串超集）历史上会把「已是正确 new」的位置重复加字，
实测改坏三本交付件（「《《唐会要》」/「可可能性」/「KKaraman」）。本文件固化护栏
_idemp_skip 的不变量：

  B-class（已是正确 new，命中位置前后恰为插入串）→ 跳过，绝不重复加字；
  裸错形（old 前后不是 ins）→ 正常修正；
  A-class（误伤正确词，如「整个社会」含「个社会」）→ 不在幂等护栏范围，仍按补丁字面施加，
        属 check_patch 的 WARN 人工复核项（不在此处静默丢弃修正）；
  per-phys 同页 / 跨页回退路径同样接护栏；
  非超串补丁（old 非 new 子串）不受护栏影响。
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import apply_corrections_core


def _run(patches, blocks, force_systematic=False):
    """搭临时 work 目录写 corrections，跑施加核心，返回 (blocks, 返回元组)。"""
    tmp = tempfile.mkdtemp(prefix="t_idem_")
    try:
        work = os.path.join(tmp, "work")
        os.makedirs(os.path.join(work, "corrections"))
        cf = os.path.join(work, "corrections", "01.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(patches, f, ensure_ascii=False)
        ret = apply_corrections_core(blocks, [cf], merge_label="SEC",
                                     force_systematic=force_systematic, work_dir=work)
        return blocks, ret
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_head_insertion_idempotent():
    # 已是「一个社会」→ 不再重复加字
    blocks = [[1, 1, "", "一个社会已经形成。"]]
    patches = [{"phys": "ALL", "old": "个社会", "new": "一个社会", "systematic": True, "reason": "补字"}]
    blocks, _ = _run(patches, blocks)
    body = blocks[0][3]
    assert body == "一个社会已经形成。", "FAIL: 头插 B-class 重复加字：%r" % body
    print("✅ test_head_insertion_idempotent: 已是「一个社会」不再重复加字")


def test_head_insertion_fixes_error():
    # 裸错形「个社会」→ 修正为「一个社会」
    blocks = [[1, 1, "", "他说个社会很复杂。"]]
    patches = [{"phys": "ALL", "old": "个社会", "new": "一个社会", "systematic": True, "reason": "补字"}]
    blocks, _ = _run(patches, blocks)
    body = blocks[0][3]
    assert body == "他说一个社会很复杂。", "FAIL: 头插裸错形未修正：%r" % body
    print("✅ test_head_insertion_fixes_error: 裸「个社会」修正为「一个社会」")


def test_head_insertion_a_class_still_applies():
    # A-class（误伤正确词「整个社会」含「个社会」）不在幂等护栏范围：
    # 护栏只防「已是正确 new」的重复加字；A-class 由 check_patch 以 WARN 提示人工复核，
    # 此处仍按补丁字面施加（设计如此，不在此静默丢弃修正）。
    blocks = [[1, 1, "", "整个社会在变革。"]]
    patches = [{"phys": "ALL", "old": "个社会", "new": "一个社会", "systematic": True, "reason": "补字"}]
    blocks, _ = _run(patches, blocks)
    body = blocks[0][3]
    assert body == "整一个社会在变革。", "FAIL: A-class 行为被护栏改变（应保持补丁字面施加）：%r" % body
    print("✅ test_head_insertion_a_class_still_applies: A-class 仍按补丁字面施加（人工复核项）")


def test_tail_insertion_idempotent():
    blocks = [[1, 1, "", "这种可能性很大。"]]
    patches = [{"phys": "ALL", "old": "能性", "new": "可能性", "systematic": True, "reason": "补字"}]
    blocks, _ = _run(patches, blocks)
    body = blocks[0][3]
    assert body == "这种可能性很大。", "FAIL: 尾插 B-class 重复加字：%r" % body
    print("✅ test_tail_insertion_idempotent: 已是「可能性」不再重复加字")


def test_tail_insertion_fixes_error():
    blocks = [[1, 1, "", "存在能性风险。"]]
    patches = [{"phys": "ALL", "old": "能性", "new": "可能性", "systematic": True, "reason": "补字"}]
    blocks, _ = _run(patches, blocks)
    body = blocks[0][3]
    assert body == "存在可能性风险。", "FAIL: 尾插裸错形未修正：%r" % body
    print("✅ test_tail_insertion_fixes_error: 裸「能性」修正为「可能性」")


def test_middle_insertion_idempotent():
    # 中间插：new = p + old + suf
    blocks = [[1, 1, "", "结果是 aXYb 形式。"]]
    patches = [{"phys": "ALL", "old": "XY", "new": "aXYb", "systematic": True, "reason": "包夹补字"}]
    blocks, _ = _run(patches, blocks)
    body = blocks[0][3]
    assert body == "结果是 aXYb 形式。", "FAIL: 中间插 B-class 重复加字：%r" % body
    print("✅ test_middle_insertion_idempotent: 中间插超串「aXYb」不再重复加字")


def test_per_phys_idempotent():
    # per-phys 同页：本页同时含已正确形与裸错形 → 正确形跳过、裸错形修正，不产生「一个一个社会」
    blocks = [[1, 1, "", "一个社会已经形成，但他说个社会很复杂。"]]
    patches = [{"phys": 1, "old": "个社会", "new": "一个社会", "reason": "补字"}]
    blocks, _ = _run(patches, blocks)
    body = blocks[0][3]
    assert "一个一个社会" not in body, "FAIL: per-phys 同页重复加字：%r" % body
    # 已正确形「一个社会」被跳过（无留痕）；裸错形「个社会」修正并留痕（per-phys 默认留痕）
    assert "一个社会已经形成，但他说一个社会" in body, "FAIL: per-phys 同页修正异常：%r" % body
    assert "〔原书作「个社会」〕" in body, "FAIL: per-phys 修正未留痕（零静默改动被破坏）：%r" % body
    print("✅ test_per_phys_idempotent: per-phys 同页幂等（已正确跳过、裸错形修正+留痕）")


def test_per_phys_fallback_idempotent():
    # per-phys 跨页回退：声明 phys=1 但错形落在 phys=2 且已是正确形 → 回退施加时跳过
    blocks = [[1, 1, "", "（本页无错形）"],
              [2, 2, "", "见《唐会要》记载其事。"]]
    patches = [{"phys": 1, "old": "唐会要》", "new": "《唐会要》", "reason": "补书名号"}]
    blocks, _ = _run(patches, blocks)
    body = blocks[1][3]
    assert body == "见《唐会要》记载其事。", "FAIL: per-phys 跨页回退重复加字：%r" % body
    print("✅ test_per_phys_fallback_idempotent: per-phys 跨页回退同样幂等")


def test_normal_patch_unaffected():
    # 非超串补丁（old 非 new 子串）不应被护栏误伤：仍正常施加
    blocks = [[1, 1, "", "荀或曰：兵者大事。"]]
    patches = [{"phys": "ALL", "old": "荀或", "new": "荀彧", "systematic": True, "reason": "形近"}]
    blocks, _ = _run(patches, blocks)
    body = blocks[0][3]
    assert body == "荀彧曰：兵者大事。", "FAIL: 正常通改被护栏干扰：%r" % body
    print("✅ test_normal_patch_unaffected: 非超串补丁正常施加")


if __name__ == "__main__":
    test_head_insertion_idempotent()
    test_head_insertion_fixes_error()
    test_head_insertion_a_class_still_applies()
    test_tail_insertion_idempotent()
    test_tail_insertion_fixes_error()
    test_middle_insertion_idempotent()
    test_per_phys_idempotent()
    test_per_phys_fallback_idempotent()
    test_normal_patch_unaffected()
    print("\nALL PASS · 施加侧幂等护栏回归通过"
          "（头/尾/中间插 B-class 幂等 + 裸错形修正 + A-class 仍字面施加 + per-phys 同页/跨页回退 + 正常补丁不受影响）。")
