#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_group_plan.py —— 分组方案自动推荐（P0-A2）。

在 pipeline_split.py 切章之后、派工之前运行：读取 chapters/_chapters.json 与 book_meta.json，
按"每组目标字符数"把连续章节顺序均衡地分成若干组，输出 work/group_plan.json 供
gen_dispatch_brief.py（A1，P1）消费，也可直接据此手工派工。

设计约束（来自八卷实战 / SKILL.md）：
  1. 保持章节连续性——同组必须是连续章节，不把不连续的章塞进同一组（子 agent 读连续段落）。
  2. 不拆章——单个超长章（如大事件章/附录）独占一组，加"建议章内拆分"提示。
  3. 前辅文独立成组——前辅文（no=0 / 编例 / 导言 / 目录等）不与正文混组
     （三国志曾因分组漏前辅文导致目录页"荀或"残留，SKILL.md §5.1）。
  4. 不跨卷——若 book_meta 含 juan_map / vol_map，分组不跨越卷边界。

用法：
    python scripts/gen_group_plan.py --work <WORK目录>
    python scripts/gen_group_plan.py --work <WORK目录> --meta <book_meta.json> \
        --target-chars 25000 --out group_plan.json

数据源优先级：
  - chapters/_chapters.json（章列表）+ chapters/NN_*.txt（统计实际字符数，首选）
  - 若 chapters 目录缺失，退化为从 fulltext_struct.txt 按 book_meta 的 phys 范围统计
"""
import os
import re
import sys
import json
import argparse

from _common import read_text_cn, read_json_cn, safe_write, _page_blocks

# 前辅文识别关键词（仅对全书第一章生效，避免正文误匹配）
PREFACE_KEYWORDS = ["前辅文", "编例", "凡例", "导言", "前言", "序言", "修订", "版权", "目录"]


def count_body_chars(text):
    """统计"实际要读的字数"：剥掉页锚标记行与全部空白后的字符数。"""
    body = re.sub(r"<<<PAGE[^>]*>>>", "", text)
    body = re.sub(r"\s+", "", body)
    return len(body)


def is_preface_chapter(ch, idx):
    """判断是否为前辅文章：no==0，或全书第一章且名字含前辅文关键词。"""
    if ch.get("no") == 0:
        return True
    if idx == 0:
        name = ch.get("name", "")
        return any(kw in name for kw in PREFACE_KEYWORDS)
    return False


def load_chapters_with_chars(work, meta):
    """返回 [(chapter_meta, nchars)]，按 no 顺序。

    优先从 chapters/ 目录读实际切分文件统计；缺失时退化为从 fulltext_struct.txt 统计。
    """
    chapters = sorted(meta["chapters"], key=lambda c: c["no"])
    ch_dir = os.path.join(work, "chapters")
    chapters_json = os.path.join(ch_dir, "_chapters.json")

    # 读取 chapters/ 下实际文件名（no -> 文件内容）
    file_by_no = {}
    if os.path.isdir(ch_dir):
        for fn in os.listdir(ch_dir):
            m = re.match(r"^(\d+)_.*\.txt$", fn)
            if m:
                file_by_no[int(m.group(1))] = os.path.join(ch_dir, fn)

    # _chapters.json 可能携带 chars（若 pipeline_split 已升级）
    declared_chars = {}
    if os.path.isfile(chapters_json):
        try:
            for c in read_json_cn(chapters_json):
                if "chars" in c:
                    declared_chars[c["no"]] = c["chars"]
        except Exception:
            pass

    # struct 页块（兜底统计用）
    struct_path = os.path.join(work, "fulltext_struct.txt")
    struct_blocks = None
    if os.path.isfile(struct_path):
        struct_blocks = _page_blocks(read_text_cn(struct_path))

    result = []
    for idx, ch in enumerate(chapters):
        no = ch["no"]
        nchars = None
        if no in file_by_no:
            nchars = count_body_chars(read_text_cn(file_by_no[no]))
        elif no in declared_chars:
            nchars = declared_chars[no]
        elif struct_blocks is not None:
            parts = []
            for p in range(ch["phys_start"], ch["phys_end"] + 1):
                if p in struct_blocks:
                    parts.append(struct_blocks[p])
            nchars = count_body_chars("\n".join(parts))
        else:
            nchars = 0
        result.append((ch, nchars))
    return result


def _phys_range_of(group_items):
    """根据组内章列表求 phys_range = [min(phys_start), max(phys_end)]。"""
    starts = [c["phys_start"] for c in group_items]
    ends = [c["phys_end"] for c in group_items]
    return [min(starts), max(ends)]


def _chapter_nos(group_items):
    return [c["no"] for c in group_items]


def recommend_groups(chapters_with_chars, target_chars, vol_boundaries=None):
    """顺序均衡分组核心。

    chapters_with_chars: [(chapter_meta, nchars)]，已按 no 排序
    target_chars: 每组目标字符数（超过 target*OVERSHOOT 才结组，容忍小幅波动）
    vol_boundaries: set，含卷边界 phys（跨卷不合并）；无则 None
    返回 [group_dict]，每组 {group, label, chapters, phys_range, chars, is_preface, notes}
    """
    OVERSHOOT = 1.3  # 加入新章后超过 target 的 30% 才结组
    groups = []

    cur_items = []
    cur_chars = 0
    cur_is_pref = None

    def flush():
        nonlocal cur_items, cur_chars, cur_is_pref
        if cur_items:
            gno = len(groups) + 1
            if cur_is_pref:
                label = "前辅文"
            else:
                nos = _chapter_nos(cur_items)
                label = f"第{nos[0]}章" if len(nos) == 1 else f"第{nos[0]}-{nos[-1]}章"
            notes = []
            # 超长单章提示章内拆分
            if not cur_is_pref and cur_chars > target_chars * OVERSHOOT and len(cur_items) == 1:
                notes.append(
                    f"本章约{cur_chars}字，远超每组目标{target_chars}字；"
                    "不可拆章，建议在章内拆分（按节/年份/文件二次切分，手工决定切分点）"
                )
            groups.append({
                "group": gno,
                "label": label,
                "chapters": _chapter_nos(cur_items),
                "phys_range": _phys_range_of(cur_items),
                "chars": cur_chars,
                "is_preface": bool(cur_is_pref),
                "notes": "；".join(notes),
            })
        cur_items = []
        cur_chars = 0
        cur_is_pref = None

    for idx, (ch, nchars) in enumerate(chapters_with_chars):
        is_pref = is_preface_chapter(ch, idx)

        # 性质切换（前辅文↔正文）：先结组
        if cur_items and cur_is_pref != is_pref:
            flush()

        # 跨卷边界：先结组
        if cur_items and vol_boundaries and ch["phys_start"] in vol_boundaries:
            flush()

        # 正文累加超限：先结组（前辅文不按 target 控制）
        if cur_items and not is_pref and cur_chars + nchars > target_chars * OVERSHOOT:
            flush()

        cur_items.append(ch)
        cur_chars += nchars
        cur_is_pref = is_pref

    flush()
    return groups


def detect_vol_boundaries(meta):
    """从 book_meta 的 juan_map / vol_map 推导出卷边界 phys 集合（分组不跨卷）。

    支持的可选字段（若结构不同则忽略，返回 None）：
      - vol_map / juan_map: [{"phys_start": N, ...}] 每卷第一项的 phys_start 即边界
    """
    boundaries = set()
    for key in ("vol_map", "juan_map", "volumes", "juans"):
        v = meta.get(key)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and "phys_start" in item:
                    boundaries.add(item["phys_start"])
    return boundaries if boundaries else None


def main():
    ap = argparse.ArgumentParser(description="分组方案自动推荐")
    ap.add_argument("--work", required=True, help="WORK 目录")
    ap.add_argument("--meta", default="", help="book_meta.json（默认 work/book_meta.json）")
    ap.add_argument("--target-chars", type=int, default=40000,
                    help="每组目标字符数（默认 40000，对应并行精读子 agent 约 4-5 万字/组的实际工作量；"
                         "小书可单 agent 读全本、忽略分组）")
    ap.add_argument("--out", default="group_plan.json", help="输出文件名（默认 work/group_plan.json）")
    args = ap.parse_args()

    work = args.work
    if not os.path.isdir(work):
        print(f"❌ WORK 目录不存在：{work}", file=sys.stderr)
        sys.exit(2)

    meta_path = args.meta or os.path.join(work, "book_meta.json")
    if not os.path.isfile(meta_path):
        print(f"❌ book_meta.json 不存在：{meta_path}", file=sys.stderr)
        sys.exit(2)
    meta = read_json_cn(meta_path)

    chapters_with_chars = load_chapters_with_chars(work, meta)
    if not chapters_with_chars:
        print("❌ book_meta.chapters 为空，无法分组", file=sys.stderr)
        sys.exit(2)

    vol_boundaries = detect_vol_boundaries(meta)
    groups = recommend_groups(chapters_with_chars, args.target_chars, vol_boundaries)

    total_chars = sum(g["chars"] for g in groups)
    plan = {
        "book": meta.get("book", ""),
        "type": meta.get("type", ""),
        "target_chars": args.target_chars,
        "total_chapters": len(chapters_with_chars),
        "total_groups": len(groups),
        "total_chars": total_chars,
        "vol_boundaries": sorted(vol_boundaries) if vol_boundaries else [],
        "groups": groups,
    }

    out_path = args.out
    if not os.path.isabs(out_path):
        out_path = os.path.join(work, out_path)
    safe_write(out_path, json.dumps(plan, ensure_ascii=False, indent=2))

    # 打印摘要
    print(f"《{meta.get('book','')}》分组方案（每组目标 {args.target_chars} 字）")
    print(f"全书 {len(chapters_with_chars)} 章 / 约 {total_chars} 字 → 分 {len(groups)} 组")
    print("-" * 72)
    for g in groups:
        pr = g["phys_range"]
        flag = " [前辅文]" if g["is_preface"] else ""
        print(f"  组{g['group']} {g['label']:<12} phys{pr[0]}-{pr[1]} "
              f"约{g['chars']}字{flag}")
        if g["notes"]:
            print(f"        ⚠ {g['notes']}")
    print("-" * 72)
    print(f"已写出：{out_path}")


if __name__ == "__main__":
    main()
