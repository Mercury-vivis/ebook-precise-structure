#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_reading.py —— 全量精读覆盖率（纪律三的唯一机械判据）

它只回答一个问题：**这本书有没有被逐行读完 100%**。
这是整个技能里唯一直接测「有没有偷懒」的检查，所以单独成一个脚本——混进别的检查会被稀释掉。

为什么必须单列
  闸A 抽检是抽样（抽到 10 页干净不代表 947 页都读过）；闸B 看的是书和账对不对，
  两者都测不出「漏读一整章」。漏读的后果是那一章的错误根本没进台账，任何下游检查都看不见。

两种口径（自动判定）
  · 全局行口径：reading_progress 里的行范围是 fulltext_struct 的全局行号 —— 校验所有范围
    拼接后恰好覆盖 1..总行数，无遗漏、无重叠。
  · 章内行口径：每个章文件从 1 计数（"行1-389"）—— 逐章核对章文件实际行数。

数据源（按优先级）
  1. reading_progress.json（结构化：records[].total_lines / read_lines）
  2. reading_progress.md（汇总表：行范围 / 已读行数）
  两者都没有 → 判未通过（无法证明全量覆盖），不猜、不放过。

用法：python check_reading.py --work <work> [--meta book_meta.json]
退出码：0 = 覆盖率 100%；1 = 未覆盖或有缺口；2 = 工作目录/底本缺失。
"""
import os
import re
import sys
import glob
import argparse

from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

from _common import read_text_cn, read_json_cn, _page_blocks  # noqa: E402

# 行范围（数字-数字）；用前 12 字符上下文区分「行范围」与「页码范围」
_RANGE_RE = re.compile(r"(\d+)\s*[-–—~至]\s*(\d+)")
_PAGE_CTX_RE = re.compile(r"(phys|book|页码|印页|页)\s*[：:]?\s*$", re.I)
_READ_LINES_RE = re.compile(r"已读[行]*[数]*[：:]\s*(\d+)")


def count_lines(path):
    if not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8-sig") as f:
        return len(f.read().splitlines())


def extract_line_ranges(text):
    """从 reading_progress 文本提取行范围，排除 phys/book/页码范围。"""
    out = []
    for m in _RANGE_RE.finditer(text):
        s, e = int(m.group(1)), int(m.group(2))
        if e < s or s < 1:
            continue
        ctx = text[max(0, m.start() - 12):m.start()]
        if _PAGE_CTX_RE.search(ctx):
            continue
        explicit = bool(re.search(r"行", ctx))
        # 无「行」前缀且数字像年份（1920-1931）→ 排除
        if not explicit and s > 1900 and e - s < 100:
            continue
        out.append((s, e, explicit))
    return out


def is_chapter_local(ranges, total):
    """≥2 个范围都从 1 起 → 章内行口径（排除"行1-全总"这类全书汇总声明）。"""
    return sum(1 for s, e, _ in ranges if s == 1 and (total is None or e < total)) >= 2


def verify_global(ranges, total):
    """全局口径：范围拼接须恰好覆盖 1..total。返回 (problems, covered)。"""
    problems = []
    usable = [(s, e) for s, e, expl in ranges if expl] or [(s, e) for s, e, _ in ranges]
    if not usable:
        return ["未提取到任何行范围"], 0
    usable.sort()
    merged = []
    for s, e in usable:
        if merged and s <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    for i in range(1, len(usable)):
        if usable[i][0] <= usable[i - 1][1]:
            problems.append(f"行范围重叠：{usable[i-1]} 与 {usable[i]}（同一行被重复声明）")
    if merged[0][0] != 1:
        problems.append(f"开头未覆盖：第一个范围起于 {merged[0][0]}，应从 1 开始")
    for i in range(1, len(merged)):
        expected = merged[i - 1][1] + 1
        if merged[i][0] != expected:
            problems.append(f"覆盖缺口：行 {expected}..{merged[i][0]-1} 无任何组声明")
    end = merged[-1][1]
    if end < total:
        problems.append(f"结尾未覆盖：已覆盖到 {end}，全书 {total} 行，缺 {total - end} 行")
    elif end > total:
        problems.append(f"范围超出：声明覆盖到 {end}，全书仅 {total} 行")
    return problems, min(end, total)


def verify_chapter_local(work, meta):
    """章内口径：逐章核对章文件行数，无章文件则看 struct 该章 phys 段有无内容。"""
    problems, warnings = [], []
    struct = read_text_cn(os.path.join(work, "fulltext_struct.txt"))
    blocks = _page_blocks(struct)
    ch_dir = os.path.join(work, "chapters")
    for c in sorted(meta.get("chapters") or [], key=lambda c: c.get("no", 0)):
        no, name = c.get("no"), c.get("name", "")
        hits = glob.glob(os.path.join(ch_dir, f"{int(no):02d}_*.txt")) if no else []
        if hits:
            if count_lines(hits[0]) == 0:
                problems.append(f"第{no}章「{name}」章文件存在但为空")
            continue
        a, b = c.get("phys_start"), c.get("phys_end")
        if not isinstance(a, int) or not isinstance(b, int):
            problems.append(f"第{no}章「{name}」缺 phys 范围，无法核对覆盖")
            continue
        present = [p for p in range(a, b + 1) if p in blocks and blocks[p].strip()]
        if present:
            warnings.append(f"第{no}章「{name}」无章文件，但 phys{a}-{b} 有 {len(present)} 页内容"
                            f"（建议补切章文件便于溯源）")
        else:
            problems.append(f"第{no}章「{name}」既无章文件，phys{a}-{b} 也无内容（未覆盖）")
    return problems, warnings


def main():
    ap = argparse.ArgumentParser(description="全量精读覆盖率（100% 是硬要求）")
    ap.add_argument("--work", required=True)
    ap.add_argument("--meta", default="", help="book_meta.json（章内口径需要 chapters）")
    args = ap.parse_args()
    B = args.work

    struct_path = os.path.join(B, "fulltext_struct.txt")
    if not os.path.isfile(struct_path):
        print(f"❌ 未找到 fulltext_struct.txt：{struct_path}")
        return 2
    total = count_lines(struct_path)
    print("=" * 64)
    print(f"check_reading：{B}")
    print(f"底本 fulltext_struct.txt 共 {total} 行")
    print("=" * 64)

    meta_path = args.meta or os.path.join(B, "book_meta.json")
    meta = {}
    if os.path.isfile(meta_path):
        try:
            meta = read_json_cn(meta_path)
        except Exception as e:
            print(f"⚠️ book_meta 读取失败（章内口径降级）：{e}")

    errors, warnings = [], []

    # ---- 优先结构化 JSON ----
    jp = os.path.join(B, "reading_progress.json")
    if os.path.isfile(jp):
        try:
            jd = read_json_cn(jp)
            recs = jd.get("records") if isinstance(jd, dict) else jd
            if isinstance(recs, list):
                for r in recs:
                    t, rr = r.get("total_lines"), r.get("read_lines")
                    if t is not None and rr is not None and rr < t:
                        errors.append(f"reading_progress.json：{r.get('name','?')} 已读 {rr}/{t} 行，未全覆盖")
                if not errors:
                    print("✅ reading_progress.json：各组均 100%")
        except Exception as e:
            warnings.append(f"reading_progress.json 解析失败：{e}")

    # ---- reading_progress.md ----
    md_path = os.path.join(B, "reading_progress.md")
    md = read_text_cn(md_path) if os.path.isfile(md_path) else ""
    if not md and not os.path.isfile(jp):
        print("❌ 既无 reading_progress.json 也无 reading_progress.md —— 无法证明全量覆盖")
        print("   漏读的章节，其错误根本没进台账，下游任何检查都看不见。")
        return 1

    ranges_raw = extract_line_ranges(md) if md else []
    seen, ranges = set(), []
    for s, e, expl in ranges_raw:
        if (s, e) not in seen:
            seen.add((s, e))
            ranges.append((s, e, expl))
    # 丢弃冗余的「全书汇总范围」，其子范围信息更详细
    sub = [r for r in ranges if not (r[0] == 1 and r[1] == total)]
    if sub and len(sub) < len(ranges):
        ranges = sub

    if ranges:
        if is_chapter_local(ranges, total):
            print(f"口径：章内行（{len(ranges)} 个范围）")
            probs, warns = verify_chapter_local(B, meta)
            errors.extend(probs)
            warnings.extend(warns)
        else:
            print(f"口径：全局行（{len(ranges)} 个范围）")
            probs, covered = verify_global(ranges, total)
            errors.extend(probs)
            pct = covered / total * 100 if total else 0
            print(f"覆盖：{covered}/{total} 行（{pct:.1f}%）")
    else:
        decl = [int(m.group(1)) for m in _READ_LINES_RE.finditer(md)] if md else []
        if decl:
            s = sum(decl)
            print(f"口径：已读行数合计 {s}/{total}（{s/total*100 if total else 0:.1f}%）")
            if s < total:
                errors.append(f"已读行数合计 {s} < 全书 {total} 行")
        else:
            errors.append("reading_progress 中没有任何可校验的行范围/已读行数记录")

    for w in warnings:
        print(f"⚠️ {w}")
    if errors:
        print(f"\n❌ 覆盖率未达 100%（{len(errors)} 处问题）：")
        for e in errors[:40]:
            print(f"   · {e}")
        if len(errors) > 40:
            print(f"   …（共 {len(errors)} 处）")
        print("\n处置：补读缺的段，把行范围写进 reading_progress.md。不要用调低总行数或改记录的方式让它通过。")
        return 1

    print("\n✅ 覆盖率 100%：全书每一行都有人读过并记了行范围。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
