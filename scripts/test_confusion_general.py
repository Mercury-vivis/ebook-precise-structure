#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_confusion_general.py —— 通用混淆桶（GENERAL）正例/反例双向断言（2026-10-17）

为什么必须双向：
  只验"不误报"会退化成一个从不报错的空扫描器；只验"能命中"会误杀合法正文。
  故每条通用条目都要同时通过——正例必须被抓住、反例必须零命中。

语料依据：反例全部取自 Books_final 224 个已交付 03 的真实合法上下文
（实测命中位置），见 ocr_confusion_dict 模块 docstring 的实测否决清单。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ocr_confusion_dict import GENERAL, general_items, scan  # noqa: E402

fails = []


def check(desc, got, want):
    if got != want:
        fails.append(f"{desc}：期望 {want}，实得 {got}")


# ---- 1. 正例：错形必须被抓住（每条独立采样，避免互相干扰）----
POS = [
    ("自已", "他回过头看着自已的影子。"),
    ("占代", "占代的社会结构以宗族为核心。"),
    ("考占", "④山东大学历史系考占专业等：《1984年》"),
    ("股墟", "张秉权《股墟文字内编考释图版》"),
    ("殷塘", "殷塘甲骨研究"),
    ("幕葬", "幕葬制度与礼制"),
    ("幕志", "代幕志汇编中收录"),
    ("扩人", "扩人社会影响"),
    ("贯族", "一定的土地分配给某些贯族"),
]
for key, text in POS:
    hits = scan(text, "⑥ 通史/断代型")
    check(f"正例「{key}」应命中", any(h.startswith(key + "（通用混淆") for h in hits), True)

# ---- 2. 反例：真实合法上下文必须零命中 ----
NEG = [
    "院长向帝国宫廷议会提出上诉",
    "连续攻占代州（山西代县）",
    "与塞尔柱人再次开战土耳其人取得胜利",
    "李慈铭日记中写道",
    "在同一名矿工家庭进行的谈话",
    "子初才智绝人，如孤，可任用之",
    "轮回式的政治史一幕一幕地更迭排演",
    "同时问：言率可试不",
    "世代居于此的共工族是一个世代与水作斗争的",
    "祭在堂上室内故下有主人盥升阼階",
    "其父細胡，通商入蜀，遂家郫縣",
    "还继续存在着南（汉人型）、北（本土型）政治",
    "先除大害，小害自已而止",
]
for t in NEG:
    hits = [h for h in scan(t, "⑥ 通史/断代型") if "通用混淆" in h]
    check(f"反例零命中：{t[:14]}…", hits, [])

# ---- 3. 白名单：命中落在专名自身范围内时跳过，附近的真错字仍报 ----
wl = ["幕志堂"]
h1 = scan("幕志堂藏书记", "④ 制度/沿革型", whitelist=wl)
check("白名单词内不报", [h for h in h1 if "通用混淆" in h], [])
h2 = scan("幕志堂藏书，另见代幕志汇编", "④ 制度/沿革型", whitelist=wl)
check("白名单词外的真错字仍报", len([h for h in h2 if "通用混淆" in h]), 1)

# ---- 4. 结构：条目齐备、pattern 可编译、right 非空 ----
import re  # noqa: E402

check("general_items 条目数", len(general_items("")), len(GENERAL))
for key, spec in GENERAL.items():
    if key != spec.get("right") and not spec.get("right"):
        fails.append(f"{key}：right 缺失")
    try:
        re.compile(spec["pattern"])
    except re.error as e:
        fails.append(f"{key}：pattern 无法编译（{e}）")

# ---- 5. 交付级复扫接线：general_items 必须真被 final_error_form_scan 消费 ----
import shutil  # noqa: E402
import tempfile  # noqa: E402

from _common import final_error_form_scan  # noqa: E402

tmp = tempfile.mkdtemp(prefix="cg_")
try:
    open(os.path.join(tmp, "书00_总览.md"), "w", encoding="utf-8").write("干净\n")
    open(os.path.join(tmp, "书01_详解.md"), "w", encoding="utf-8").write("干净\n")
    open(os.path.join(tmp, "书03_全文.md"), "w", encoding="utf-8").write("他回过头看着自已的影子。\n")
    hit, skipped = final_error_form_scan(tmp, [], general_items=general_items(""))
    check("复扫命中 03（台账为空也能靠通用桶拦住）", sorted(hit.keys()), ["书03_全文.md"])
    check("复扫不误报 00/01", len(hit), 1)
    check("复扫无读入失败", skipped, [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if fails:
    print("❌ test_confusion_general 失败 %d 项：" % len(fails))
    for f in fails:
        print("   -", f)
    sys.exit(1)
print(f"✅ test_confusion_general 全部通过（正例 {len(POS)} / 反例 {len(NEG)} / 白名单 2 / 结构 {len(GENERAL)} 条）")
