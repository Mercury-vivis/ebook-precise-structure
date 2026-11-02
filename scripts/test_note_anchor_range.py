#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_note_anchor_range.py —— 笔记锚点区间核验回归（v2.3.1）

守两件事：
  1. **多套印页体系不误报**：次章 book_start ≤ 本章 book_start 时（代译序印页 1–12
     之后正文从印页 1 重起的双序列书），区间上界取本章 book_end，不得用
     `next.book_start - 1`（会算出 1−1=0 的退化区间，把本章全部合法锚点误报越界）。
     《中国的历史脉动》第1章 9 个锚全遭误报即此型（2026-10-24 实测）。
  2. **真越界仍抓**：引用印页落在本章区间外、且未写明来源章的，照旧报 offender；
     写明「属第N章」的跨章引用豁免（工具只报不认则该要求永远无法满足）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import note_anchor_range_report  # noqa: E402

fails = []


def check(desc, cond):
    if not cond:
        fails.append(desc)


def test_multi_series_no_false_positive():
    """双序列印页（代译序 1–12 + 正文 1–380）：第1章引用印页 1–12 全部合法。"""
    chapters = [
        {"no": 1, "name": "代译序", "phys_start": 3, "book_start": 1, "book_end": 12},
        {"no": 2, "name": "正文首章", "phys_start": 15, "book_start": 1, "book_end": 190},
        {"no": 3, "name": "次章", "phys_start": 205, "book_start": 191, "book_end": 251},
    ]
    notes = ["## 1. 代译序\n引文一（原书页 1）；引文二（原书页码 12）；引文三（原书页 7）。\n"]
    ok, offenders, disp = note_anchor_range_report(notes, chapters, kind="PHYS")
    check("双序列书第1章合法锚不得误报越界", ok and not offenders)
    check("结论应报均落入", "均落入" in disp)
    print("✅ test_multi_series_no_false_positive: 双序列印页体系不误报（区间上界取 book_end）")


def test_real_cross_chapter_still_caught():
    """真越界仍抓：第2章笔记引用印页 205（第3章内容）且未写明来源章 → offender。"""
    chapters = [
        {"no": 1, "name": "代译序", "phys_start": 3, "book_start": 1, "book_end": 12},
        {"no": 2, "name": "正文首章", "phys_start": 15, "book_start": 1, "book_end": 190},
        {"no": 3, "name": "次章", "phys_start": 205, "book_start": 191, "book_end": 251},
    ]
    notes = ["## 2. 正文首章\n此处接续（原书页码 205 起）展开。\n"]
    ok, offenders, disp = note_anchor_range_report(notes, chapters, kind="PHYS")
    check("真越界必须报 offender", not ok and offenders)
    check("offender 点名第2章与锚 205",
          offenders and offenders[0][0] == 2 and 205 in offenders[0][2])
    print("✅ test_real_cross_chapter_still_caught: 未声明来源章的真越界仍报（收窄不放漏）")


def test_declared_cross_chapter_exempt():
    """写明「属第3章」的跨章引用豁免——工具只报不认则该要求永远无法满足。"""
    chapters = [
        {"no": 1, "name": "代译序", "phys_start": 3, "book_start": 1, "book_end": 12},
        {"no": 2, "name": "正文首章", "phys_start": 15, "book_start": 1, "book_end": 190},
        {"no": 3, "name": "次章", "phys_start": 205, "book_start": 191, "book_end": 251},
    ]
    notes = ["## 2. 正文首章\n此后第3章《次章》（原书页码 191 起，属第3章）接续线索。\n"]
    ok, offenders, disp = note_anchor_range_report(notes, chapters, kind="PHYS")
    check("已声明来源章的跨章引用豁免", ok and not offenders)
    check("豁免计数要报出", "已豁免" in disp)
    print("✅ test_declared_cross_chapter_exempt: 写明「属第N章」的跨章引用依法豁免")


if __name__ == "__main__":
    test_multi_series_no_false_positive()
    test_real_cross_chapter_still_caught()
    test_declared_cross_chapter_exempt()
    if fails:
        print(f"❌ test_note_anchor_range 失败 {len(fails)} 项：")
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print("✅ test_note_anchor_range 通过")
