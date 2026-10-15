#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_source_tags.py —— 陈述来源标签扫描（v1.28.0 新增 · P0）

用途：精读笔记（notes/*.md，含 01_章节详解.md）里的每条陈述，其来源归属必须可辨。
本工具机械扫描四类来源标签的覆盖情况，并抓出「疑似推断但未标注」的句子——
这是把「正确性优先」从纠错层推进到**陈述层**的关键一环：错字查完还不够，
还得说清楚哪些话是原文、哪些话是精读者自己的推断。

四类来源标签（写法见 SKILL.md §6.7，全角括号，与既有 〔存疑〕 记号同族）：
  〔A 原意〕  作者原意 / 原文直引——可在底本逐字回查
  〔B 事实〕  原文载明的事实或数据（转述，非直引，但可回查到）
  〔C 推断〕  精读者（AI）的合理推断，原文无直接依据
  〔D 未验证〕 不可验证 / 待核（含联网也查不到的）

**负向标注原则（精简优先）**：A/B 属「原文层」，是笔记的默认层，**不强制逐条标**；
只有 C/D 这类「非原文层」陈述**必须标**。本工具因此只把 C/D 缺失判为问题。

用法：
  python scan_source_tags.py --work <work目录>            # 扫 work/notes/
  python scan_source_tags.py --notes <notes目录>          # 直接指定目录
  python scan_source_tags.py --work <work> --strict       # 疑似未标 >0 即退出 1（可作交付前自检）
  python scan_source_tags.py --work <work> --report <路径> # 指定报告落点（默认 work/source_tag_report.md）

退出码：0=无问题（或仅报告）；1=--strict 下存在疑似未标推断；2=入参/目录缺失。
"""

import argparse
import os
import re
import sys

from _common import safe_write

# 四类标签（顺序即报告输出顺序）
TAGS = [
    ("〔A 原意〕", "A", "作者原意 / 原文直引"),
    ("〔B 事实〕", "B", "原文载明事实或数据"),
    ("〔C 推断〕", "C", "精读者推断（原文无直接依据）"),
    ("〔D 未验证〕", "D", "不可验证 / 待核"),
]
TAG_RE = re.compile(r"〔([ABCD])\s*(原意|事实|推断|未验证)?〕")
# 已有任何来源标签的行视为「已标注」
ANY_TAG_RE = re.compile(r"〔[ABCD]\s*(?:原意|事实|推断|未验证)?〕")

# 推断信号词：句含其一且行内无 C/D 标签 → 疑似推断未标
INFER_WORDS = [
    "推测", "推断", "大概", "大约", "似乎", "似乎", "应当", "应该", "估计",
    "或许", "也许", "可能", "恐怕", "恐系", "当是", "疑为", "疑似", "不排除",
    "倾向于", "据此判断", "由此推见", "看来", "我以为", "我们判断", "很可能是",
]
INFER_RE = re.compile("|".join(re.escape(w) for w in INFER_WORDS))

# 跳过行：原文引用行（引用块 / 整句被书名号或引号包裹 / 纯标题 / 表格分隔）
SKIP_LINE_RE = re.compile(r"^\s*(?:>|#{1,6}\s|\||[-*]\s*$|---)")


def iter_note_files(notes_dir):
    """返回 notes 目录下的 .md 文件（按名排序）。"""
    if not os.path.isdir(notes_dir):
        return []
    out = []
    for name in sorted(os.listdir(notes_dir)):
        if name.lower().endswith(".md"):
            p = os.path.join(notes_dir, name)
            if os.path.isfile(p):
                out.append(p)
    return out


def scan_file(path):
    """扫描单个 md 文件，返回 (counts, suspects)。

    counts: {"A":n, "B":n, "C":n, "D":n}
    suspects: [(行号, 行内容)]  疑似推断但无 C/D 标签的行
    """
    counts = {k: 0 for k in "ABCD"}
    suspects = []
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    for i, line in enumerate(lines, 1):
        for m in TAG_RE.finditer(line):
            counts[m.group(1)] += 1
        if SKIP_LINE_RE.match(line):
            continue
        if ANY_TAG_RE.search(line):
            continue
        # 整句被「」/""包裹 → 视为原文直引，跳过（作者自己说"可能"不算推断）
        stripped = line.strip()
        if (stripped.startswith("「") and stripped.rstrip("。！？").endswith("」")) or \
           (stripped.startswith('"') and stripped.rstrip("。！？").endswith('"')):
            continue
        if INFER_RE.search(line):
            # 命中词需有实质上下文，过短的行（如仅标题残片）忽略
            if len(stripped) >= 8:
                suspects.append((i, stripped))
    return counts, suspects


def main():
    ap = argparse.ArgumentParser(description="陈述来源标签扫描（A原意/B事实/C推断/D未验证）")
    ap.add_argument("--work", help="work 目录（扫其下 notes/）")
    ap.add_argument("--notes", help="直接指定 notes 目录（优先于 --work）")
    ap.add_argument("--report", help="报告落点（默认 <work>/source_tag_report.md）")
    ap.add_argument("--strict", action="store_true", help="存在疑似未标推断时退出 1（交付前自检用）")
    args = ap.parse_args()

    if args.notes:
        notes_dir = args.notes
    elif args.work:
        notes_dir = os.path.join(args.work, "notes")
    else:
        sys.stderr.write("[scan_source_tags] 需指定 --work 或 --notes\n")
        sys.exit(2)

    files = iter_note_files(notes_dir)
    if not files:
        sys.stderr.write(f"[scan_source_tags] 目录下无 .md 文件：{notes_dir}\n")
        sys.exit(2)

    total = {k: 0 for k in "ABCD"}
    per_file = []
    all_suspects = []
    for p in files:
        counts, suspects = scan_file(p)
        for k in "ABCD":
            total[k] += counts[k]
        per_file.append((os.path.basename(p), counts, suspects))
        for ln, txt in suspects:
            all_suspects.append((os.path.basename(p), ln, txt))

    labeled = sum(total.values())
    cd = total["C"] + total["D"]

    print(f"扫描笔记 {len(files)} 个文件：{notes_dir}")
    for tag_text, key, desc in TAGS:
        print(f"  {tag_text} {desc}：{total[key]} 处")
    print(f"  合计已标注 {labeled} 处（其中非原文层 C/D {cd} 处）")
    print(f"  疑似推断未标：{len(all_suspects)} 处")
    for fn, ln, txt in all_suspects[:20]:
        print(f"    ⚠ {fn}:{ln}  {txt[:70]}")
    if len(all_suspects) > 20:
        print(f"    … 另有 {len(all_suspects) - 20} 处（见报告）")

    report_path = args.report or (os.path.join(args.work, "source_tag_report.md") if args.work else None)
    if report_path:
        lines = ["# 陈述来源标签扫描报告", "",
                 "标签口径：`〔A 原意〕` 原文直引 · `〔B 事实〕` 原文事实/数据 · "
                 "`〔C 推断〕` 精读者推断 · `〔D 未验证〕` 不可验证。",
                 "**负向标注**：A/B 为默认层不强制标；C/D 为必标层（详见 SKILL.md §6.7）。", ""]
        lines.append("## 一、标签统计")
        lines.append("")
        lines.append("| 标签 | 含义 | 处数 |")
        lines.append("|---|---|---|")
        for tag_text, key, desc in TAGS:
            lines.append(f"| `{tag_text}` | {desc} | {total[key]} |")
        lines.append(f"| **合计** | | **{labeled}** |")
        lines.append("")
        lines.append("## 二、分文件明细")
        lines.append("")
        lines.append("| 文件 | A | B | C | D | 疑似未标 |")
        lines.append("|---|---|---|---|---|---|")
        for fn, counts, suspects in per_file:
            lines.append(f"| {fn} | {counts['A']} | {counts['B']} | {counts['C']} | {counts['D']} | {len(suspects)} |")
        lines.append("")
        lines.append("## 三、疑似推断未标（须人工判级）")
        lines.append("")
        if all_suspects:
            for fn, ln, txt in all_suspects:
                lines.append(f"- `{fn}:{ln}` {txt}")
        else:
            lines.append("（无）")
        lines.append("")
        lines.append("> 判级指引：确有原文依据 → 补 `〔A 原意〕`/`〔B 事实〕` 或改为直引；"
                     "属我方推断 → 标 `〔C 推断〕`；查不到出处的 → 标 `〔D 未验证〕`。"
                     "原文作者自己使用「可能/大约」等词的，按 `〔A 原意〕` 处理（本工具已跳过引号整句）。")
        safe_write(report_path, "\n".join(lines) + "\n")
        print(f"\n报告已写入：{report_path}")

    if args.strict and all_suspects:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
