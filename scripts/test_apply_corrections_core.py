#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apply_corrections_core 回归测试（补丁施加核心，PDF/EPUB 两路径共用）。

八条不变量——任一被破坏都直接损害精读质量，故固化：
  1) systematic 通改先于逐页补丁，且全书生效；
  2) 同页多补丁必须基于【本页 body0】批量收集 span 后整体消解：短 old 与长 old 重叠时
     短者跳过并记 miss，绝不产生嵌套留痕（见 SKILL.md §3.4）；
  3) 普通纠错须留痕 `〔原书作「X」〕`（可溯源、可逆）；
  4) ocr:true（转录层）修正照常施加，但**不留痕**——我方 OCR 之误不该记到原书账上；
  5) status=deleted 的补丁不被施加（deleted 不物理删，靠单条 status 在加载期过滤）；
  6) 单字 systematic 默认被护栏拦截（误标即全书静默损坏），须显式放行才施加；
  7) `{"patches":[...]}` 包裹层必须照常加载（与预检四处同口径，曾整份被静默丢弃）；
  8) 凭空编造的 old（底本无此串）必须记 miss，绝不静默改字（防幻觉补丁）。
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import apply_corrections_core


def _run(patches, blocks, manifest=None, force_systematic=False, wrapped=False):
    """搭临时 work 目录写 corrections，跑施加核心，返回 (blocks, 返回元组, work)。"""
    tmp = tempfile.mkdtemp(prefix="t_acc_")
    try:
        work = os.path.join(tmp, "work")
        os.makedirs(os.path.join(work, "corrections"))
        cf = os.path.join(work, "corrections", "01.json")
        payload = {"patches": patches} if wrapped else patches
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        if manifest is not None:
            with open(os.path.join(work, "patch_manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
        ret = apply_corrections_core(blocks, [cf], merge_label="SEC",
                                     force_systematic=force_systematic, work_dir=work)
        return blocks, ret, work
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_systematic_applies_bookwide():
    blocks = [[1, 1, "第一章", "磨宋之制，盖自秦汉始。"],
              [2, 2, "第二章", "磨宋两代，府兵制兴。"]]
    patches = [{"phys": "ALL", "old": "磨宋", "new": "唐宋",
                "systematic": True, "reason": "形近：磨→唐"}]
    blocks, (miss, pending, a_trace, tongai, a_silent, a_ocr), _ = _run(patches, blocks)
    b1, b2 = blocks[0][3], blocks[1][3]
    assert "唐宋之制" in b1 and "磨宋" not in b1, "FAIL: systematic 未在第1页生效：%r" % b1
    assert "唐宋两代" in b2 and "磨宋" not in b2, "FAIL: systematic 未在第2页生效：%r" % b2
    assert "磨宋" in tongai, "FAIL: 通改清单未记录该条：%r" % tongai
    assert not miss, "FAIL: 合法通改被误判未命中：%r" % miss
    print("✅ test_systematic_applies_bookwide: systematic 通改全书生效且进通改清单")


def test_same_page_batch_no_nested_trace():
    # 同页两条补丁，短的「磨宋」是长的「磨宋之制」的子串 → 须基于 body0 批量消解：
    # 长者胜、短者跳过并记 miss，正文只留一条留痕，绝不出现〔原书作「…〔原书作…」〕嵌套。
    blocks = [[1, 1, "第一章", "磨宋之制，盖自秦汉始。"]]
    patches = [{"phys": 1, "old": "磨宋之制", "new": "唐宋之制", "reason": "形近"},
               {"phys": 1, "old": "磨宋", "new": "唐宋", "reason": "形近（子串）"}]
    blocks, (miss, pending, a_trace, *_), _ = _run(patches, blocks)
    body = blocks[0][3]
    assert "唐宋之制" in body, "FAIL: 长补丁未施加：%r" % body
    assert body.count("〔原书作") == 1, "FAIL: 留痕条数异常（疑似嵌套留痕）：%r" % body
    assert "〔原书作「磨宋之制」〕" in body, "FAIL: 留痕引述应为长串：%r" % body
    assert any("重叠" in m for m in miss), "FAIL: 重叠短补丁未记 miss：%r" % miss
    print("✅ test_same_page_batch_no_nested_trace: 同页补丁基于 body0 批量消解，无嵌套留痕")


def test_normal_correction_leaves_trace():
    blocks = [[1, 1, "第一章", "荀或曰：兵者国之大事。"]]
    patches = [{"phys": 1, "old": "荀或", "new": "荀彧", "reason": "形近：或→彧"}]
    blocks, (miss, pending, a_trace, *_), _ = _run(patches, blocks)
    body = blocks[0][3]
    assert "荀彧" in body, "FAIL: 修正未施加：%r" % body
    assert "〔原书作「荀或」〕" in body, "FAIL: 未留痕（零静默改动被破坏）：%r" % body
    assert a_trace == 1, "FAIL: 留痕计数异常：%r" % a_trace
    print("✅ test_normal_correction_leaves_trace: 普通纠错施加并留痕（可溯源）")


def test_ocr_layer_applies_without_trace():
    # 转录层（错在我方 OCR）：修正照常进正文，但不写「原书作」——否则冤枉古籍。
    blocks = [[1, 1, "第一章", "荀或曰：兵者国之大事。"]]
    patches = [{"phys": 1, "old": "荀或", "new": "荀彧", "ocr": True, "reason": "我方OCR误"}]
    blocks, (miss, pending, a_trace, tongai, a_silent, a_ocr), _ = _run(patches, blocks)
    body = blocks[0][3]
    assert "荀彧" in body, "FAIL: 转录层修正未施加：%r" % body
    assert "〔原书作" not in body, "FAIL: 转录层不该留痕（会冤枉原书）：%r" % body
    assert a_ocr == 1, "FAIL: 转录层计数异常：%r" % a_ocr
    print("✅ test_ocr_layer_applies_without_trace: 转录层施加修正但不留痕")


def test_deleted_patch_not_applied():
    # deleted 不物理删 corrections/*.json（台账可回溯），靠加载期单条 status=deleted 过滤。
    blocks = [[1, 1, "第一章", "荀或曰：兵者国之大事。"]]
    patches = [{"phys": 1, "old": "荀或", "new": "荀彧", "reason": "形近",
                "status": "deleted"}]
    blocks, (miss, pending, a_trace, *_), _ = _run(patches, blocks)
    body = blocks[0][3]
    assert "荀彧" not in body and "荀或" in body, \
        "FAIL: deleted 补丁仍被施加（台账保护失效）：%r" % body
    print("✅ test_deleted_patch_not_applied: status=deleted 的补丁不施加（deleted 不物理删）")


def test_single_char_systematic_blocked_by_guard():
    # 单字通改误标即全书静默损坏（如「二」→「于」），默认拦截、须显式放行。
    blocks = [[1, 1, "第一章", "二者，国之大事。"]]
    patches = [{"phys": "ALL", "old": "二", "new": "于", "systematic": True, "reason": "形近"}]
    blocks, (miss, *_), _ = _run(patches, blocks)
    assert "于者" not in blocks[0][3], "FAIL: 单字 systematic 护栏失效：%r" % blocks[0][3]
    assert miss, "FAIL: 拦截后未记 miss（执行者将无从知晓）：%r" % miss
    # 显式放行则应施加
    blocks2 = [[1, 1, "第一章", "二者，国之大事。"]]
    blocks2, (miss2, *_), _ = _run(patches, blocks2, force_systematic=True)
    assert "于者" in blocks2[0][3], "FAIL: --force-systematic 放行后仍未施加：%r" % blocks2[0][3]
    print("✅ test_single_char_systematic_blocked_by_guard: 单字通改默认拦截、显式放行才施加")


def test_wrapped_patches_object_loaded():
    # {"patches":[...]} 包裹层：validate_corrections / check_patch / gen_patch_manifest /
    # gen_error_forms 四处都展开，施加侧曾不展开——整份被当成单条 dict 静默丢弃，
    # 一条补丁都不施加、03 保留全部错字，而预检四道全绿看不出异常。
    blocks = [[1, 1, "第一章", "磨宋之制，荀或曰。"]]
    patches = [{"phys": "ALL", "old": "磨宋", "new": "唐宋",
                "systematic": True, "reason": "形近"},
               {"phys": 1, "old": "荀或", "new": "荀彧", "reason": "形近"}]
    blocks, (miss, pending, a_trace, tongai, *_), _ = _run(patches, blocks, wrapped=True)
    body = blocks[0][3]
    assert "唐宋之制" in body, "FAIL: 包裹层下通改未施加（整份被丢弃）：%r" % body
    assert "荀彧" in body, "FAIL: 包裹层下逐页补丁未施加：%r" % body
    assert "磨宋" in tongai, "FAIL: 包裹层下通改清单为空：%r" % tongai
    print("✅ test_wrapped_patches_object_loaded: {\"patches\":[...]} 包裹层照常加载施加")


def test_plain_list_patches_loaded():
    # 原生顶层数组格式，与包裹层等价（防为修包裹层而破坏原生路径）。
    blocks = [[1, 1, "第一章", "磨宋之制，荀或曰。"]]
    patches = [{"phys": "ALL", "old": "磨宋", "new": "唐宋",
                "systematic": True, "reason": "形近"},
               {"phys": 1, "old": "荀或", "new": "荀彧", "reason": "形近"}]
    blocks, (miss, pending, a_trace, tongai, *_), _ = _run(patches, blocks, wrapped=False)
    body = blocks[0][3]
    assert "唐宋之制" in body and "荀彧" in body, "FAIL: 顶层数组格式施加异常：%r" % body
    print("✅ test_plain_list_patches_loaded: 顶层数组格式照常加载施加")


def test_fabricated_old_recorded_as_miss():
    # 底本无此串（凭空编造 / phys 标错）→ 必须记 miss 暴露，绝不静默跳过、更不改字。
    blocks = [[1, 1, "第一章", "荀彧曰：兵者国之大事。"]]
    patches = [{"phys": 1, "old": "根本不存在的错字串", "new": "某正字", "reason": "凭空"}]
    blocks, (miss, pending, a_trace, *_), _ = _run(patches, blocks)
    assert "某正字" not in blocks[0][3], "FAIL: 凭空补丁改了正文：%r" % blocks[0][3]
    assert miss, "FAIL: 凭空补丁未记 miss（静默跳过＝掩盖漏标）：%r" % miss
    print("✅ test_fabricated_old_recorded_as_miss: 凭空 old 记 miss 且不改字")


if __name__ == "__main__":
    test_systematic_applies_bookwide()
    test_same_page_batch_no_nested_trace()
    test_normal_correction_leaves_trace()
    test_ocr_layer_applies_without_trace()
    test_deleted_patch_not_applied()
    test_single_char_systematic_blocked_by_guard()
    test_wrapped_patches_object_loaded()
    test_plain_list_patches_loaded()
    test_fabricated_old_recorded_as_miss()
    print("\nALL PASS · apply_corrections_core 回归通过"
          "（通改 / body0 批量消解 / 留痕 / 转录层 / deleted / 单字护栏 / 两种补丁格式 / 幻觉补丁）。")
