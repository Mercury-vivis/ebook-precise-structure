#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_01c_add_headings.py —— 标题落 struct（01c）判据回归（v2.3.0）

覆盖四项：
  1. build_specs：headings 优先，chapters 只补齐 headings 未覆盖的篇名（防同篇两条 spec）；
  2. plan_prefix：只认「归一后等值」的行；每锚块最多产出一处（防「原　序」类重复误标）；
  3. 幂等：已带 `#` 的行不再认领 —— 加一遍后再跑，命中数归零；
  4. norm：去全角/半角空白 + 去句末脚注号。
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "add_headings", os.path.join(HERE, "01c_add_headings.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = load_mod()

STRUCT = [
    "<<<PAGE phys=1 book=1 title=\"前言\">>>",
    "前　言",
    "这是前言正文，提到 原　序 二字但不在原序块里。",
    "<<<PAGE phys=2 book=2 title=\"原序\">>>",
    "原　序",
    "原序正文。",
    "正文里又一次出现",
    "原　序",
    "<<<PAGE phys=3 book=3 title=\"内篇 六家第一\">>>",
    "内篇　六家第一(1)",
    "六家正文。",
]


def t_norm():
    assert M.norm("前　言") == "前言"
    assert M.norm("内篇 六家第一(1)") == "内篇六家第一"
    assert M.norm(" Ａ B ") == "ＡB"
    print("✅ test_norm：去全角空白 + 去脚注号")


def t_specs_headings_win():
    meta = {
        "headings": [{"text": "前言", "level": 3, "phys": 1}],
        "chapters": [{"no": 1, "name": "前言", "phys_start": 1, "phys_end": 1},
                     {"no": 2, "name": "原序", "phys_start": 2, "phys_end": 2}],
    }
    specs = M.build_specs(meta, 2)
    texts = [s["text"] for s in specs]
    assert texts.count("前言") == 1, texts          # headings 已覆盖，chapters 不重复认领
    assert "原序" in texts
    assert specs[0]["level"] == 3                   # headings 的 level 生效
    print("✅ test_specs_headings_win：headings 优先、chapters 只补齐")


def t_plan_one_per_block():
    meta = {"chapters": [{"no": 1, "name": "前言", "phys_start": 1, "phys_end": 1},
                         {"no": 2, "name": "原序", "phys_start": 2, "phys_end": 2},
                         {"no": 3, "name": "内篇 六家第一", "phys_start": 3, "phys_end": 3}]}
    specs = M.build_specs(meta, 2)
    hits, missed = M.plan_prefix(STRUCT, specs)
    got = sorted((STRUCT[i].strip(), lvl) for i, lvl, _t in hits)
    want = sorted([("前　言", 2), ("原　序", 2), ("内篇　六家第一(1)", 2)])
    assert got == want, got
    # phys=2 块内第二处「原　序」不得被再标一次
    assert sum(1 for i, _l, _t in hits if i == 7) == 0
    assert not missed
    print("✅ test_plan_one_per_block：等值命中 + 每块一处（重复行不被二次认领）")


def t_idempotent():
    meta = {"chapters": [{"no": 1, "name": "前言", "phys_start": 1, "phys_end": 1},
                         {"no": 2, "name": "原序", "phys_start": 2, "phys_end": 2}]}
    specs = M.build_specs(meta, 2)
    hits, _ = M.plan_prefix(STRUCT, specs)
    lines = list(STRUCT)
    for i, lvl, _t in hits:
        lines[i] = "#" * lvl + " " + lines[i].lstrip()
    hits2, _ = M.plan_prefix(lines, specs)
    assert hits2 == [], hits2
    print("✅ test_idempotent：加过一遍后再跑，命中归零")


def t_no_fuzzy():
    """非等值不得命中：正文句含「原序」二字，但不是独立标题行。"""
    lines = ["<<<PAGE phys=1 book=1 title=\"原序\">>>",
             "这是原序里的一句话，不是标题。"]
    meta = {"chapters": [{"no": 1, "name": "原序", "phys_start": 1, "phys_end": 1}]}
    hits, missed = M.plan_prefix(lines, M.build_specs(meta, 2))
    assert hits == [] and len(missed) == 1
    print("✅ test_no_fuzzy：只认等值，正文句不被误标")


if __name__ == "__main__":
    t_norm()
    t_specs_headings_win()
    t_plan_one_per_block()
    t_idempotent()
    t_no_fuzzy()
    print("\n✅ test_01c_add_headings 全部通过")
