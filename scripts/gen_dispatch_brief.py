#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_dispatch_brief.py —— 派工 brief 自动生成（P1-A1，v1.27.0）。

消费 A2 的 group_plan.json + book_meta.json + chapters/_chapters.json，
为每个并行精读 group 渲染独立 brief，输出 work/briefs/group_NN.md。

替代主 Agent 手工拼接 brief 的重复劳动，杜绝：
  ① 章列表漏前辅文/目录页（三国志案例，SKILL.md:702）；
  ② 专名清单 proper_names 人工抄录遗漏；
  ③ 前卷经验跨卷传递靠记忆；
  ④ 行范围/phys 范围人工数错。

用法：
    python scripts/gen_dispatch_brief.py --work <work> [--meta <book_meta.json>]
        [--groups <group_plan.json>] [--prev-experience <上一卷work>]
        [--simplify-leak <simplify_leak_report.md>]

输出：
    work/briefs/group_01.md ... group_NN.md
    work/briefs/_index.md（各组概览）

设计原则：
  - brief 是给子 agent 看的自然语言，模板基于 SKILL.md §5 派工模板；
  - 不侵入现有 assemble 链，纯只读输入 + 写 briefs/；
  - 前卷经验仅作"提示性"注入，不强制改字（防污染）；
  - 专名清单从 book_meta.proper_names 取，按本组章范围过滤（若章名含专名则保留）。
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _common import read_text_cn, read_json_cn, safe_write, expand_patch_data  # #24 单一入口 / N3 原子写 / 补丁归一


def load_chapters_meta(work):
    """读 chapters/_chapters.json，返回 {no: {name, phys_start, phys_end, ...}}。"""
    p = os.path.join(work, "chapters", "_chapters.json")
    if not os.path.isfile(p):
        return {}
    data = read_json_cn(p)
    return {c["no"]: c for c in data}


def load_group_plan(work, groups_path):
    """读 group_plan.json（A2 产出）。"""
    if groups_path and os.path.isfile(groups_path):
        return read_json_cn(groups_path)
    default = os.path.join(work, "group_plan.json")
    if os.path.isfile(default):
        return read_json_cn(default)
    return None


def extract_prev_experience(prev_work):
    """从上一卷 work 目录提取高频错形经验（提示性，不强制）。

    读 corrections/*.json 的 systematic 补丁和高频 per-phys 补丁，
    提取 old→new 对，按出现次数排序，取 top 20。
    """
    if not prev_work or not os.path.isdir(prev_work):
        return []
    corr_dir = os.path.join(prev_work, "corrections")
    if not os.path.isdir(corr_dir):
        return []
    pairs = {}  # (old, new) -> count
    for fp in sorted(glob.glob(os.path.join(corr_dir, "*.json"))):
        try:
            data = read_json_cn(fp)
        except Exception:
            continue
        if isinstance(data, dict):
            data = [data]
        for c in data:
            if not isinstance(c, dict):
                continue
            old = c.get("old", "")
            new = c.get("new", "")
            if not old or old == new:
                continue
            if len(old) < 2:  # 跳过单字，避免噪声
                continue
            key = (old, new)
            pairs[key] = pairs.get(key, 0) + 1
    # 按次数降序，取 top 20
    top = sorted(pairs.items(), key=lambda x: -x[1])[:20]
    return [{"old": o, "new": n, "count": c} for (o, n), c in top]


def extract_simplify_leak(leak_path):
    """从 simplify_leak_report.md 提取简繁抖动结论（通改候选 / 简形独占 / 混用）。"""
    if not leak_path or not os.path.isfile(leak_path):
        return ""
    try:
        text = read_text_cn(leak_path)
    except Exception:
        return ""
    # 提取三档表的摘要行
    lines = []
    for line in text.splitlines():
        if any(k in line for k in ["通改候选", "简形独占", "混用占比", "结论", "建议"]):
            lines.append(line.strip())
    return "\n".join(lines[:10]) if lines else text[:500]


def extract_round1_patterns(work, top_n=15):
    """P2-F1（v1.27.0）：从本书第一轮 corrections 提取高频漏校模式，作第二轮软提示。

    第二轮精读时，把第一轮已发现的高频错形注入 brief（软提示，不强制），
    提醒子 agent 重点核对同类错形是否还有遗漏。返回 [(old, new, count), ...]。
    """
    corr_dir = os.path.join(work, "corrections")
    if not os.path.isdir(corr_dir):
        return []
    pairs = {}
    for fp in sorted(glob.glob(os.path.join(corr_dir, "*.json"))):
        try:
            data = read_json_cn(fp)
        except Exception:
            continue
        data = expand_patch_data(data) if isinstance(data, (dict, list)) else []
        for p in data:
            if not isinstance(p, dict) or p.get("merge"):
                continue
            if p.get("status") == "deleted":
                continue
            old = (p.get("old") or "").strip()
            new = (p.get("new") or "").strip()
            if not old or not new or old == new or len(old) < 2:
                continue
            key = (old, new)
            pairs[key] = pairs.get(key, 0) + 1
    top = sorted(pairs.items(), key=lambda x: -x[1])[:top_n]
    return [(o, n, c) for (o, n), c in top]


def filter_proper_names_for_group(proper_names, group_chapters, chapters_meta):
    """按本组章范围过滤专名：若专名出现在本组任一章名中则优先保留。

    简单策略：章名含专名 → 标记"本章重点"；其余专名全部保留（专名库通常不大，
    全量注入比过滤更安全，避免漏校）。返回 (重点专名列表, 全部专名列表)。
    """
    if not proper_names:
        return [], []
    group_chapter_names = []
    for no in group_chapters:
        ch = chapters_meta.get(no, {})
        if ch.get("name"):
            group_chapter_names.append(ch["name"])
    key_names = []
    for pn in proper_names:
        for cn in group_chapter_names:
            if pn in cn:
                key_names.append(pn)
                break
    return key_names, proper_names


def render_brief(book_title, book_type, group, chapters_meta, proper_names,
                 prev_exp, simplify_leak, work_dir, cross_book=None, round1_patterns=None):
    """渲染单个 group 的 brief（基于 SKILL.md §5 派工模板）。

    cross_book: 跨卷专名标准库（proper_names_cross_book.json），提示性注入。
    round1_patterns: P2-F1 本书第一轮漏校模式，第二轮软提示。
    """
    gno = group["group"]
    label = group.get("label", f"第{gno}组")
    chapter_nos = group.get("chapters", [])
    phys_range = group.get("phys_range", [0, 0])
    chars = group.get("chars", 0)
    is_preface = group.get("is_preface", False)

    # 章文件路径列表
    chapter_lines = []
    for no in chapter_nos:
        ch = chapters_meta.get(no, {})
        name = ch.get("name", f"第{no}章")
        # 匹配实际文件名（chapters/NN_名称.txt）
        pattern = os.path.join(work_dir, "chapters", f"{no:02d}_*.txt")
        matches = glob.glob(pattern)
        if matches:
            rel = os.path.relpath(matches[0], work_dir)
            chapter_lines.append(f"{no:02d}. `{rel}`（{name}，phys{ch.get('phys_start','?')}-{ch.get('phys_end','?')}）")
        else:
            chapter_lines.append(f"{no:02d}. chapters/{no:02d}_{name}.txt（{name}，phys{ch.get('phys_start','?')}-{ch.get('phys_end','?')}）")

    # 专名
    key_names, all_names = filter_proper_names_for_group(proper_names, chapter_nos, chapters_meta)

    # 跨卷专名标准（P1-E1：提示性注入，不强制）
    cross_lines = []
    if cross_book:
        pn_cross = cross_book.get("proper_names", {})
        wd_cross = cross_book.get("compound_words", {})
        if pn_cross or wd_cross:
            cross_lines.append("以下为**跨卷专名标准**（来源：已完成卷实战确认，**提示性参考，不强制**，须结合本卷上下文判断；有歧义须语境核实）：")
            if pn_cross:
                cross_lines.append("")
                cross_lines.append("**人名/地名**：")
                for std, info in sorted(pn_cross.items()):
                    if not info.get("confirmed", True):
                        continue
                    aliases = "、".join(f"「{a}」" for a in info.get("aliases", []))
                    note = info.get("note", "")
                    cross_lines.append(f"- 标准「{std}」：OCR 常见误形 {aliases}。{note}")
            if wd_cross:
                cross_lines.append("")
                cross_lines.append("**高频词语**：")
                for std, info in sorted(wd_cross.items()):
                    if not info.get("confirmed", True):
                        continue
                    aliases = "、".join(f"「{a}」" for a in info.get("aliases", []))
                    note = info.get("note", "")
                    cross_lines.append(f"- 标准「{std}」：OCR 常见误形 {aliases}。{note}")

    # 前卷经验
    prev_lines = []
    if prev_exp:
        prev_lines.append("以下为前卷高频 OCR 错形（**提示性参考，不强制**，须结合本卷上下文判断）：")
        for i, e in enumerate(prev_exp[:15], 1):
            prev_lines.append(f"  {i}. 「{e['old']}」→「{e['new']}」（前卷出现 {e['count']} 次）")

    lines = []
    lines.append(f"# 派工 Brief · 第{gno:02d}组（{label}）")
    lines.append("")
    lines.append(f"- **书名**：{book_title}")
    lines.append(f"- **本书类型**：{book_type}")
    lines.append(f"- **本组范围**：phys {phys_range[0]}-{phys_range[1]}，约 {chars} 字符")
    lines.append(f"- **前辅文组**：{'是' if is_preface else '否'}")
    lines.append("")
    lines.append("## 一、需读取文件（页间有锚标记，引用必用锚，勿自行编造页码）")
    lines.append("")
    lines.append("锚格式：`<<<PAGE phys=N book=M>>>`（M 即原书页码）；笔记用 `（原书页码 M）` 标注。")
    lines.append("")
    for cl in chapter_lines:
        lines.append(cl)
    lines.append("")

    if key_names:
        lines.append("## 二、本章重点专名（章名中出现，须重点核对）")
        lines.append("")
        lines.append("、".join(key_names))
        lines.append("")

    if all_names:
        lines.append("## 三、全书专名清单（纠错时须核对，OCR 形近误读高发区）")
        lines.append("")
        # 每行 5 个
        for i in range(0, len(all_names), 5):
            batch = all_names[i:i+5]
            lines.append("、".join(batch))
        lines.append("")

    if cross_lines:
        lines.append("## 四、跨卷专名标准（提示性）")
        lines.append("")
        for cl in cross_lines:
            lines.append(cl)
        lines.append("")

    if prev_lines:
        lines.append("## 五、前卷经验（提示性）")
        lines.append("")
        for pl in prev_lines:
            lines.append(pl)
        lines.append("")

    if round1_patterns:
        lines.append("## 六、第一轮漏校模式（第二轮软提示）")
        lines.append("")
        lines.append("以下为本书第一轮精读已发现的高频错形（**第二轮软提示，不强制**，请重点核对同类错形是否还有遗漏）：")
        lines.append("")
        for i, (old, new, cnt) in enumerate(round1_patterns[:12], 1):
            lines.append(f"  {i}. 「{old}」→「{new}」（第一轮出现 {cnt} 次）")
        lines.append("")

    if simplify_leak:
        lines.append("## 七、简繁码位抖动结论")
        lines.append("")
        lines.append("```")
        lines.append(simplify_leak)
        lines.append("```")
        lines.append("")

    lines.append("## 八、纠错要求（核心）")
    lines.append("")
    lines.append("文本来自 OCR，可能个别错字，**请结合上下文与领域常识主动纠错**，但：")
    lines.append("")
    lines.append("- 每个非平凡纠错都写进 `corrections/` 台账，**先按 Stage 3「纠错判层与留痕落点」判层**：")
    lines.append("  - 属转录层（错在我方 OCR）的条目加 `\"ocr\": true`；")
    lines.append("  - 属底本层的记明原书原形与判据；")
    lines.append("  - **03 正文一律不留记号**（〔〕留痕不写入正文）；")
    lines.append("  - 拿不准的按存疑登记、不臆改；")
    lines.append("- 高频系统噪声在返回开头列「通改清单」，正文直接写修正后字；")
    lines.append("- 绝不无声改字、绝不臆改年代数字（疑则标不猜）；")
    lines.append("- 古籍集注本务必保留夹注（〔夹注〕标记），不可当噪声删。")
    lines.append("")

    lines.append("## 九、产出路径")
    lines.append("")
    lines.append(f"- **纠错补丁**：`corrections/{gno:02d}_corrections.json`（Write 工具，JSON 数组）")
    lines.append(f"- **分析笔记**：`notes/{gno:02d}.md`（Write 工具，全带原书页码锚点）")
    lines.append(f"- **阅读进度**：`reading_progress.md`（或 `.json`，记录行范围 + 已读行数 + 发现错误数）")
    lines.append("")
    lines.append("要求：忠实原文，数字/人名/年代/术语不臆造；每条笔记带原书页码锚点。")
    lines.append("")
    lines.append("---")
    lines.append(f"*本 brief 由 gen_dispatch_brief.py 自动生成（v1.27.0），基于 group_plan.json + book_meta.json。*")

    return "\n".join(lines)


def render_index(book_title, groups, chapters_meta):
    """渲染 briefs/_index.md 概览。"""
    lines = []
    lines.append(f"# {book_title} · 派工分组概览")
    lines.append("")
    lines.append("| 组 | 标签 | 章号 | phys范围 | 字符数 | 前辅文 |")
    lines.append("|---|---|---|---|---|---|")
    for g in groups:
        chs = ",".join(str(c) for c in g.get("chapters", []))
        pr = g.get("phys_range", [0, 0])
        lines.append(f"| {g['group']:02d} | {g.get('label','')} | {chs} | {pr[0]}-{pr[1]} | {g.get('chars',0)} | {'是' if g.get('is_preface') else '否'} |")
    lines.append("")
    lines.append("各组详细 brief 见同目录 group_NN.md。")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="派工 brief 自动生成（P1-A1）")
    ap.add_argument("--work", required=True, help="work 目录")
    ap.add_argument("--meta", default=None, help="book_meta.json 路径（默认 work/book_meta.json）")
    ap.add_argument("--groups", default=None, help="group_plan.json 路径（默认 work/group_plan.json）")
    ap.add_argument("--prev-experience", default=None, help="上一卷 work 目录（提取高频错形经验）")
    ap.add_argument("--simplify-leak", default=None, help="simplify_leak_report.md 路径")
    args = ap.parse_args()

    work = args.work
    if not os.path.isdir(work):
        print(f"❌ work 目录不存在：{work}", file=sys.stderr)
        sys.exit(2)

    meta_path = args.meta or os.path.join(work, "book_meta.json")
    if not os.path.isfile(meta_path):
        print(f"❌ book_meta.json 不存在：{meta_path}", file=sys.stderr)
        sys.exit(2)
    meta = read_json_cn(meta_path)

    plan = load_group_plan(work, args.groups)
    if plan is None:
        print("❌ group_plan.json 不存在，请先跑 gen_group_plan.py（A2）", file=sys.stderr)
        sys.exit(2)

    chapters_meta = load_chapters_meta(work)
    proper_names = meta.get("proper_names", [])
    book_title = meta.get("title", plan.get("book", "未知书名"))
    book_type = meta.get("type", plan.get("type", "未知类型"))

    # 前卷经验
    prev_exp = extract_prev_experience(args.prev_experience) if args.prev_experience else []
    # 简繁抖动
    simplify_leak = extract_simplify_leak(args.simplify_leak) if args.simplify_leak else ""
    # 跨卷专名标准库（P1-E1：默认加载 resources/proper_names_cross_book.json）
    cross_book = None
    cross_book_path = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "resources", "proper_names_cross_book.json"))
    if os.path.isfile(cross_book_path):
        try:
            cross_book = read_json_cn(cross_book_path)
        except Exception as e:
            print(f"⚠ 跨卷专名标准库加载失败（{e}），跳过", file=sys.stderr)

    # 输出目录
    out_dir = os.path.join(work, "briefs")
    os.makedirs(out_dir, exist_ok=True)

    groups = plan.get("groups", [])
    print(f"书名：{book_title}")
    print(f"类型：{book_type}")
    print(f"分组数：{len(groups)}")
    print(f"专名数：{len(proper_names)}")
    if prev_exp:
        print(f"前卷经验：{len(prev_exp)} 条高频错形")
    if cross_book:
        n_pn = len(cross_book.get("proper_names", {}))
        n_wd = len(cross_book.get("compound_words", {}))
        print(f"跨卷专名标准：{n_pn} 人名/地名 + {n_wd} 高频词语")
    # P2-F1：第二轮软提示——从本书第一轮 corrections 提取高频漏校模式
    round1_patterns = extract_round1_patterns(work) if os.path.isdir(os.path.join(work, "corrections")) else []
    if round1_patterns:
        print(f"第一轮漏校模式：{len(round1_patterns)} 条（第二轮软提示）")
    print()

    for g in groups:
        gno = g["group"]
        brief = render_brief(book_title, book_type, g, chapters_meta, proper_names,
                             prev_exp, simplify_leak, work, cross_book, round1_patterns)
        out_path = os.path.join(out_dir, f"group_{gno:02d}.md")
        safe_write(out_path, brief)
        label = g.get("label", "")
        chars = g.get("chars", 0)
        print(f"  ✅ group_{gno:02d}.md（{label}，{chars} 字符，{len(g.get('chapters',[]))} 章）")

    # index
    index = render_index(book_title, groups, chapters_meta)
    safe_write(os.path.join(out_dir, "_index.md"), index)
    print(f"\n✅ 共生成 {len(groups)} 份 brief + _index.md，输出目录：{out_dir}")


if __name__ == "__main__":
    main()
