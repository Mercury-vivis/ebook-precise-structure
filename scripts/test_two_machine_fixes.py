#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_two_machine_fixes.py —— 2026-11-04 两机工单合并修复的四条回归闸。

覆盖 Earth 1 条 + Moon 4 条，合计 5 个问题点 / 4 个测试函数：

  1. Earth    check_book.check_file_hygiene：04 §四 豁免边界按**行首恰好二级**识别，
              4.4 里并入的 `#### 五、` 不再提前截断豁免区（描述性 XXX 不误报阻断）。
  2. Moon #1  00b_audit_tables：新增「行结构塌陷 / 单元格爆量」提示判据，
              且合法短格单行目录表不误伤（avg 下界）、旧 metrics 不 KeyError。
  3. Moon #2  gen_dispatch_brief.render_brief：派工 brief 必含笔记结构硬约束块。
     Moon #3  pipeline_assemble.extract_events：前辅文 `（phys N）` 不计入非规范 stats，
              multi / numeric 两档仍如实计数。
  4. Moon #4  _common.citation_verbatim_scan：异体字差只作第五节归因，
              不参与逐字命中、不虚增逐字率、不写回底本；真改写不得被归一捞回。

每条都带正向样本 + 反向样本（收窄不放漏）。
运行：python scripts/test_two_machine_fixes.py
"""
import importlib.util
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _common               # noqa: E402
import check_book            # noqa: E402
import gen_dispatch_brief    # noqa: E402
import pipeline_assemble     # noqa: E402

# 00b 以数字开头，不能按模块名 import，按文件路径加载
_spec = importlib.util.spec_from_file_location(
    "audit_tables_00b", os.path.join(HERE, "00b_audit_tables.py"))
t00b = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(t00b)

fails = []


def check(desc, cond):
    if not cond:
        fails.append(desc)


def _write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def test_section4_exempt_boundary():
    """Earth 第08册：4.4 的 `#### 五、` 不得截断 §四 豁免；§五 之外仍照抓。"""
    tmp = tempfile.mkdtemp(prefix="tm_exempt_")
    p = os.path.join(tmp, "04.md")
    sha = "0" * 64
    s4 = ("## 四、撤销与人工修订\n\n"
          "### 4.4 考据裁定与预处理处置登记\n\n"
          "#### 五、裁定不修\n\n"
          "- phys=8 原表 XXX 系 OCR 占位符，TODO 待核 FIXME 说明。\n\n"
          "## 五、交付指纹\n\n"
          f"- 00=sha256:{sha}\n")
    _write(p, s4)
    issues = check_book.check_file_hygiene(p, exempt_placeholder_section=True)
    check("4.4 描述性 XXX/TODO/FIXME 应被 §四 豁免覆盖",
          not any("占位符残留" in x for x in issues))
    check("BOM/CRLF/空文件仍全查且无误", issues == [])

    # 反向①：exempt=False（普通交付件）仍须抓到 XXX——豁免只在 04 生效
    issues2 = check_book.check_file_hygiene(p, exempt_placeholder_section=False)
    check("不豁免时 XXX 仍命中（收窄不放漏）",
          any("占位符残留" in x for x in issues2))

    # 反向②：真 `## 五、` 之后的 XXX 不在豁免区，仍须报
    s5 = ("## 四、撤销与人工修订\n\n- （无）\n\n"
          "## 五、交付指纹\n\n"
          f"- 00=sha256:{sha}\n- 遗留 XXX 未清\n")
    _write(p, s5)
    issues3 = check_book.check_file_hygiene(p, exempt_placeholder_section=True)
    check("§五 之后的 XXX 仍报占位符残留",
          any("占位符残留" in x for x in issues3))

    # 反向③：无 §五 时豁免到文末（\Z 分支），4.4 含四级「五、」亦不得截断
    s6 = ("## 四、撤销与人工修订\n\n"
          "#### 五、裁定不修\n\n"
          "- 该处 XXX 为描述性引用\n")
    _write(p, s6)
    issues4 = check_book.check_file_hygiene(p, exempt_placeholder_section=True)
    check("无 §五 时豁免至文末", issues4 == [])
    print("✅ test_section4_exempt_boundary: §四 豁免边界按行首二级识别（4 样本）")


def _cell(text, colspan=1, rowspan=1, img=False):
    return {"text": text, "colspan": colspan, "rowspan": rowspan, "img": img}


def test_table_hint_judgements():
    """Moon 第09册：单行多列塌陷 / 单格爆量要提示；合法短格目录表不误伤。"""
    # ① 塌陷 + 爆量：1 行 7 列，单格 2535 字（工单实测形态）
    burst = "杭州、" * 845                      # 2535 字
    rows = [[_cell(burst)] + [_cell("") for _ in range(6)]]
    m = t00b.analyze_table(rows)
    check("metrics 新增 max_cell_chars", m.get("max_cell_chars") == len(burst))
    check("metrics 新增 row_cell_counts", m.get("row_cell_counts") == [7])
    tags = t00b.classify(m)
    check("单行 7 列长格应报『行结构塌陷』", any("行结构塌陷" in t for t in tags))
    check("单格 2535 字应报『单元格爆量』", any("单元格爆量" in t for t in tags))

    # ② 合法短格单行目录表（《秦汉史讲义》phys 131 实测 1×10、单格 5 字）
    toc = ["2 ", "有度第六", "第二卷", "主道第五", "愛臣第四",
           "難言第三", "存韓第二", "初見秦第一", "第一卷", "韓非子目錄"]
    m2 = t00b.analyze_table([[_cell(x) for x in toc]])
    check("合法目录表 avg 应 <50", m2["avg_cell_chars"] < 50)
    tags2 = t00b.classify(m2)
    check("合法短格单行目录表不得报塌陷", not any("行结构塌陷" in t for t in tags2))
    check("合法短格单行目录表不得报爆量", not any("单元格爆量" in t for t in tags2))

    # ③ 正常多行表无提示
    m3 = t00b.analyze_table([[_cell("甲"), _cell("乙")],
                             [_cell("丙"), _cell("丁")]])
    tags3 = t00b.classify(m3)
    check("正常 2×2 表不得报两条新提示",
          not any(("行结构塌陷" in t or "单元格爆量" in t) for t in tags3))

    # ④ 兼容：老调用方传的 metrics 缺新键时不得 KeyError
    legacy = {"img_ratio": 0.0, "empty_ratio": 0.0, "header_empty": 0,
              "adhesion_cells": 0, "total_rows": 2, "logic_cols": 2,
              "avg_cell_chars": 3.0}
    check("旧 metrics 缺新键仍可分类", t00b.classify(legacy) == [])
    print("✅ test_table_hint_judgements: 塌陷/爆量提示 + 目录表不误伤 + 旧 dict 兼容")


def test_dispatch_contract_and_phys_stats():
    """Moon#2 派工 brief 注入结构契约；Moon#3 前辅文 phys 不计非规范。"""
    tmp = tempfile.mkdtemp(prefix="tm_brief_")
    meta = {"book": "测试书", "type": "史书"}
    group = {"group": 1, "label": "第一组", "chapters": [1],
             "phys_range": [1, 10], "chars": 100}
    chapters_meta = {1: {"no": 1, "name": "第一章",
                         "phys_start": 1, "phys_end": 10}}
    brief = gen_dispatch_brief.render_brief(
        meta, group, chapters_meta, [], [], [], tmp)
    for token in ("笔记结构硬约束", "## N. 章名", "**过程要点**",
                  "**关键原句摘录**", "独立粗体行",
                  "references/book-type-output-templates.md"):
        check(f"brief 含结构契约要点：{token}", token in brief)
    check("页码硬约束未被结构块挤掉", "（phys N）" in brief)

    note = ("## 1. 第一章\n\n"
            "**过程要点**：\n"
            "- 卷首题记一条（phys 3）\n"
            "- 正文条目一条（原书页码 100）\n"
            "- 多值枚举一条（原书页码 14、29）\n"
            "- 行尾纯数字一条（328）\n")
    stats = {}
    chapters = pipeline_assemble.extract_events(
        [("notes/01.md", note)], valid_pages=None, stats=stats)
    check("extract_events 抽到 1 章", len(chapters) == 1)
    check("4 条条目全入 02", len(chapters[0]["events"]) == 4 if chapters else False)
    check("phys 不计入非规范 stats", "phys" not in stats)
    check("multi 仍如实计数（不放漏）", stats.get("multi") == 1)
    check("numeric 仍如实计数（不放漏）", stats.get("numeric") == 1)
    ev_pages = [e[1] for e in chapters[0]["events"]] if chapters else []
    check("前辅文定位列写 phys 3（不折算成印页）", "phys 3" in ev_pages)
    print("✅ test_dispatch_contract_and_phys_stats: brief 结构契约 + phys 合法形态")


def test_citation_variant_attribution_only():
    """Moon#4：异体字差只作第五节归因，不虚增逐字率、不改底本、不做第五档匹配。"""
    tmp = tempfile.mkdtemp(prefix="tm_variant_")
    note_path = _write(os.path.join(tmp, "01.md"),
                       "## 1. 第一章\n\n**关键原句摘录**：\n\n"
                       "> 天下爲公（原书页码 1）\n")
    clean = "昔者仲尼有言天下為公此之謂大同焉"
    raw = clean
    scan = _common.citation_verbatim_scan(tmp, [note_path], clean, raw)
    check("扫描到 1 条引文", scan["total"] == 1)
    check("异体差不得虚增 ok（逐字口径不变）", scan["ok"] == 0)
    check("归因计数 1 条", scan["variant"] == 1)
    it = scan["items"][0] if scan["items"] else {}
    check("条目打上 variant 归因", it.get("variant") is True)
    check("归因列出 爲/為 字对", "爲/為" in it.get("variant_pairs", ""))
    check("底本原形不被改写", clean == "昔者仲尼有言天下為公此之謂大同焉")
    check("normalize_citation_text 本身不折合异体",
          _common.normalize_citation_text("天下爲公") !=
          _common.normalize_citation_text("天下為公"))
    check("normalize_citation_variants 才折合",
          _common.normalize_citation_variants("天下爲公")[0] == "天下為公")

    report = _common.citation_verbatim_report(scan, tmp)
    with io.open(report, encoding="utf-8") as f:
        rtext = f.read()
    check("报告含第五节归因栏", "## 五、异体字差归因" in rtext)
    check("报告明写不计入逐字率", "不计入逐字率" in rtext)
    summary = _common.citation_verbatim_summary(tmp, [note_path], clean, raw)
    check("摘要逐字仍为 0（不虚增）", "✅ 03 逐字 0" in summary)
    check("摘要另列归因说明", "仅作归因" in summary)

    # 反向：真改写不得靠异体归一冒充逐字命中
    note2 = _write(os.path.join(tmp, "02.md"),
                   "## 1. 第一章\n\n**关键原句摘录**：\n\n"
                   "> 天下爲公選賢與能另造之句（原书页码 1）\n")
    scan2 = _common.citation_verbatim_scan(tmp, [note2], clean, raw)
    check("真改写不得被归一捞成 variant", scan2["variant"] == 0)
    check("真改写仍判未逐字命中", scan2["ok"] == 0)
    print("✅ test_citation_variant_attribution_only: 异体只归因 + 真改写不冒充")


if __name__ == "__main__":
    test_section4_exempt_boundary()
    test_table_hint_judgements()
    test_dispatch_contract_and_phys_stats()
    test_citation_variant_attribution_only()
    if fails:
        print(f"❌ test_two_machine_fixes 失败 {len(fails)} 项：")
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print("✅ test_two_machine_fixes 通过（4 组 / 5 个问题点）")
