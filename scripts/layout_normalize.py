#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""layout_normalize.py — 纯 TXT 精读交付的版面规整（预处理层·有台账可恢复）。

读者提出的三项规整：
  1. 删除「微视频」栏目及其紧跟的一行视频标题（34 处）；
  2. 合并段内断裂：句读未断的软换行、标点另起行、书名跨行、引号跨行；
  3. 空行规整：仅章/节标题上下各留一个空行，其余空行全删。

落点：只改 fulltext_struct.txt（结构清洗版），fulltext_raw.txt（法医底本）逐字不动。
每处删除/合并写入 layout_normalize_log.md（含 phys、原行、新行），并生成
04_dispositions.md 供 assemble 并入 04 §四 4.4，做到零静默改动。

幂等：重复跑不叠加（微视频已删、断行已并、空行已规整，第二次改动数为 0）。

用法：python layout_normalize.py --work <work>
"""
import argparse
import os
import re
import sys
from datetime import datetime

from _interp_guard import ensure_interpreter  # noqa: E402
from _common import read_text_cn, safe_write  # noqa: E402

ensure_interpreter()

ANCHOR_RE = re.compile(r"<<<PAGE phys=(\d+)")
# 章/节标题（01c 已加 # 前缀）；四级「（一）」标题不在此列
TITLE_RE = re.compile(r"^#{2,3}\s+(?=第[一二三四五六七八九十百]+[章节]|参考文献)")
HEAD_RE = re.compile(r"^#{1,6}\s")
# 段内断裂判据
BROKEN_START = re.compile(r"^[，。、；：？！）》」』】〉”’,.!?;:)\]]")
UNFINISHED_END = set("，、；：,;:“《（")


def phys_of(line, cur):
    m = ANCHOR_RE.search(line)
    return int(m.group(1)) if m else cur


def is_title(line):
    return bool(TITLE_RE.match(line)) or bool(HEAD_RE.match(line))


def should_merge(prev, cur):
    if not prev.strip() or not cur.strip():
        return False
    if ANCHOR_RE.search(prev) or ANCHOR_RE.search(cur):
        return False
    if is_title(prev) or is_title(cur):
        return False
    if BROKEN_START.match(cur):
        return True
    p = prev.rstrip()
    if not p:
        return False
    if p[-1] in UNFINISHED_END:
        return True
    # 书名跨行：上行《未闭合、下行含》
    if p.count("《") > p.count("》") and "》" in cur:
        return True
    # 引号跨行：上行“未闭合
    if p.count("“") > p.count("”"):
        return True
    return False


def normalize(lines):
    log = []
    cur_phys = 0

    # ---- 1. 删微视频块 ----
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        cur_phys = phys_of(line, cur_phys)
        if line.strip() == "微视频" and i + 1 < len(lines):
            follow = lines[i + 1]
            log.append({"type": "微视频块删除", "phys": cur_phys,
                        "removed": [line, follow]})
            i += 2
            continue
        out.append(line)
        i += 1
    lines = out

    # ---- 2. 空行规整：先全删，再在章/节标题上下各插一个 ----
    compact = [x for x in lines if x.strip()]
    spaced = []
    for line in compact:
        if TITLE_RE.match(line):
            if spaced and spaced[-1].strip():
                spaced.append("")
            spaced.append(line)
            spaced.append("")
        else:
            spaced.append(line)
    lines = spaced

    # ---- 3. 合并段内断裂 ----
    out, cur_phys = [], 0
    for line in lines:
        cur_phys = phys_of(line, cur_phys)
        if out and should_merge(out[-1], line):
            prev = out.pop()
            merged = prev.rstrip() + line.lstrip()
            log.append({"type": "段内断裂合并", "phys": cur_phys,
                        "removed": [prev, line], "new": merged})
            out.append(merged)
        else:
            out.append(line)

    # 收尾：去掉文件首尾多余空行
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out, log


def write_log(work, log, stats):
    p = os.path.join(work, "layout_normalize_log.md")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("# 版面规整逐处日志\n\n")
        f.write("来源：fulltext_struct.txt（结构清洗版）；法医底本 fulltext_raw.txt 逐字未动。\n\n")
        f.write(f"删除微视频块 {stats['mv']} 处；段内断裂合并 {stats['merge']} 处；"
                f"空行 {stats['blank_before']} → {stats['blank_after']}。\n\n")
        f.write("## 一、微视频块删除\n\n")
        for e in [x for x in log if x["type"] == "微视频块删除"]:
            f.write(f"- phys={e['phys']}｜删：`{e['removed'][0]}` + `{e['removed'][1]}`\n")
        f.write("\n## 二、段内断裂合并\n\n")
        for e in [x for x in log if x["type"] == "段内断裂合并"]:
            f.write(f"- phys={e['phys']}｜`{e['removed'][0]}` + `{e['removed'][1]}` "
                    f"→ `{e['new'][:160]}`\n")
    disp = os.path.join(work, "04_dispositions.md")
    text = (
        "#### （一）纯 TXT 版面规整（预处理层）\n\n"
        f"按读者裁定对 `fulltext_struct.txt` 做三项确定性规整，法医底本 `fulltext_raw.txt` 逐字不动：\n\n"
        f"1. 删除「微视频」栏目及其紧跟的一行视频标题，共 {stats['mv']} 处；\n"
        f"2. 合并段内断裂共 {stats['merge']} 处（句读未断的软换行、标点另起行、书名/引号跨行）；\n"
        f"3. 空行规整：空行由 {stats['blank_before']} 个减至 {stats['blank_after']} 个，"
        "仅章/节标题上下各留一个空行。\n\n"
        "逐处原文、phys 与合并结果见 `layout_normalize_log.md`；此处登记不进 corrections 台账，"
        "属预处理处置，重跑 assemble 不会抹除。\n"
    )
    with open(disp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return p, disp


def main():
    ap = argparse.ArgumentParser(description="纯 TXT 精读交付的版面规整")
    ap.add_argument("--work", required=True)
    args = ap.parse_args()
    work = os.path.abspath(args.work)
    sp = os.path.join(work, "fulltext_struct.txt")
    if not os.path.isfile(sp):
        sys.exit(f"❌ 找不到 {sp}")
    raw = read_text_cn(sp)
    lines = raw.splitlines()
    stats_before = {"blank_before": sum(1 for x in lines if not x.strip())}
    new_lines, log = normalize(lines)
    stats = {
        "mv": sum(1 for x in log if x["type"] == "微视频块删除"),
        "merge": sum(1 for x in log if x["type"] == "段内断裂合并"),
        "blank_before": stats_before["blank_before"],
        "blank_after": sum(1 for x in new_lines if not x.strip()),
    }
    if not log:
        print("ℹ 无待规整项（幂等重跑）；未写盘。")
        return
    bak_dir = os.path.join(work, "_bak")
    os.makedirs(bak_dir, exist_ok=True)
    bak = os.path.join(bak_dir, f"fulltext_struct.before_layout.{datetime.now():%Y%m%d-%H%M%S}.txt")
    with open(bak, "w", encoding="utf-8", newline="\n") as f:
        f.write(raw)
    safe_write(sp, "\n".join(new_lines) + "\n")
    lp, dp = write_log(work, log, stats)
    print(f"✅ 规整完成：微视频 {stats['mv']} 处、断行合并 {stats['merge']} 处、"
          f"空行 {stats['blank_before']}→{stats['blank_after']}")
    print(f"   备份：{bak}")
    print(f"   日志：{lp}")
    print(f"   处置登记：{dp}")


if __name__ == "__main__":
    main()
