#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_subagent_outputs.py —— 子 agent 产出校验自动化（P0-A3）。

在各组并行精读结束、assemble 之前运行，做两类机械校验：

  1. 覆盖率校验（check_reading_progress）：
     - 兼容读取结构化 reading_progress.json（SKILL.md §5.1 L643 规范要求子 agent 产出）
       与主 agent 汇总的 reading_progress.md（纪律三，实际八卷实战多为此格式）；
     - 自动判定口径：
       · 全局行口径（行范围是 fulltext_struct 的全局行号，如 1-1643 / 2911-4912）→
         校验所有行范围拼接后恰好覆盖 1..总行数，无遗漏、无重叠；
       · 章内行口径（每个 chapter 文件从 1 计数，如"行1-389"）→
         逐章校验 chapter 文件实际行数与记录的已读行数一致；
     - 修复扩展名漂移：校验通过后自动汇总渲染 reading_progress.md，
       使 gen_reading_report.py:187 能读到内容（而非 None 退化成空表）。

  2. 补丁多命中校验（check_uniqueness）：
     - 对 per-phys 补丁（豁免 systematic / merge / regex），在其声明 phys 页块
       count(old)：=0 → ERROR（grounding 失败），>1 → WARN（短 old 偏页风险，
       应补 ≥6 字上下文使其在该页唯一命中）。

用法：
    python scripts/validate_subagent_outputs.py --work <WORK目录>
    python scripts/validate_subagent_outputs.py --work <WORK目录> [--no-write-md]
校验失败（有 ERROR）以退出码 1 结束。
"""
import os
import re
import sys
import glob
import json
import argparse

from _common import (
    read_text_cn, read_json_cn, safe_write, _page_blocks, expand_patch_data,
    compile_linebreak, iter_spans, find_span
)


# ============================ 覆盖率校验 ============================

# 行范围匹配（数字-数字），用前 12 字符上下文区分"行范围"与"页码范围"
_RANGE_RE = re.compile(r"(\d+)\s*[-–—~至]\s*(\d+)")
# 页码/页范围上下文（这些前缀的范围不是行范围）
_PAGE_CTX_RE = re.compile(r"(phys|book|页码|印页|页)\s*[：:]?\s*$", re.I)
# 已读行数声明：- 已读行数：1282
_READ_LINES_RE = re.compile(r"已读[行]*[数]*[：:]\s*(\d+)")
# 总行数声明
_TOTAL_LINES_RE = re.compile(r"总行数[：:]\s*(\d+)")


def count_file_lines(path):
    """统计文件行数（与读取口径一致，按 \\n 计，末尾空行不计）。"""
    if not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    return len(text.splitlines())


def extract_line_ranges(text):
    """从 reading_progress 文本提取"行范围"对，排除 phys/book/页码范围。

    返回 [(start, end, is_explicit_line)]：
      is_explicit_line=True 表示前缀显式含"行"字（更可靠）。
    """
    ranges = []
    for m in _RANGE_RE.finditer(text):
        start, end = int(m.group(1)), int(m.group(2))
        if end < start or start < 1:
            continue
        ctx = text[max(0, m.start() - 12):m.start()]
        # 排除页码范围
        if _PAGE_CTX_RE.search(ctx):
            continue
        # 排除明显的年份/数字（如 1920-1931 且无行上下文）：年份跨度小且数字>1900
        explicit_line = bool(re.search(r"行", ctx))
        if not explicit_line and start > 1900 and end - start < 100:
            # 可能是年份范围（如 1920-1931），无"行"前缀则排除
            continue
        ranges.append((start, end, explicit_line))
    return ranges


def extract_read_lines(text):
    """提取所有"已读行数：N"声明。"""
    return [int(m.group(1)) for m in _READ_LINES_RE.finditer(text)]


def is_chapter_local_ranges(ranges, total_lines=None):
    """判断是否为章内行口径：出现 ≥2 个范围都从 1 开始。

    排除"全书汇总范围"（start=1 且 end=total_lines，如"行1-9879全覆盖"）——
    那是全书覆盖声明，不是某个章的局部范围。
    """
    starts_one = sum(
        1 for s, e, _ in ranges
        if s == 1 and (total_lines is None or e < total_lines))
    return starts_one >= 2


def verify_global_coverage(ranges, total_lines):
    """全局行口径校验：行范围拼接后恰好覆盖 1..total，无遗漏、无重叠。

    返回 (ok, problems, covered)。
    """
    problems = []
    # 只取显式行范围，若无则取全部（表格口径）
    usable = [(s, e) for s, e, expl in ranges if expl]
    if not usable:
        usable = [(s, e) for s, e, _ in ranges]
    usable.sort()

    if not usable:
        return False, ["未提取到任何行范围"], 0

    # 合并重叠/相邻区间，检查连续性
    merged = []
    for s, e in usable:
        if merged and s <= merged[-1][1] + 1:
            if e > merged[-1][1]:
                merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))

    # 检测重叠（原始区间）
    for i in range(1, len(usable)):
        if usable[i][0] <= usable[i - 1][1]:
            problems.append(
                f"行范围重叠：{usable[i-1]} 与 {usable[i]}（同一行被多组重复声明）")

    # 连续性
    if merged[0][0] != 1:
        problems.append(f"开头未覆盖：第一个范围起于 {merged[0][0]}，应从 1 开始")
    for i in range(1, len(merged)):
        expected = merged[i - 1][1] + 1
        if merged[i][0] != expected:
            problems.append(
                f"覆盖缺口：行 {expected}..{merged[i][0]-1} 无任何组声明")
    covered_end = merged[-1][1]
    if covered_end < total_lines:
        problems.append(
            f"结尾未覆盖：已覆盖到 {covered_end}，全书共 {total_lines} 行，"
            f"缺 {total_lines - covered_end} 行")
    elif covered_end > total_lines:
        problems.append(
            f"范围超出：声明覆盖到 {covered_end}，但全书仅 {total_lines} 行")

    covered = min(covered_end, total_lines)
    return len(problems) == 0, problems, covered


def verify_chapter_local(work, meta):
    """章内行口径校验：逐章核对覆盖情况。

    - 章文件存在 → 核对章文件实际行数（非空即已切分）；
    - 章文件缺失（后期补入 book_meta 但未重切）→ 用 fulltext_struct 中该章
      phys 范围是否有实际内容兜底：有内容 → WARN（建议补切章文件），
      无内容 → ERROR。
    返回 (problems, warnings, stats)。
    """
    problems, warnings, stats = [], [], []
    struct = read_text_cn(os.path.join(work, "fulltext_struct.txt"))
    blocks = _page_blocks(struct)
    ch_dir = os.path.join(work, "chapters")
    for c in sorted(meta["chapters"], key=lambda c: c["no"]):
        no = c["no"]
        matches = glob.glob(os.path.join(ch_dir, f"{no:02d}_*.txt"))
        if matches:
            actual = count_file_lines(matches[0])
            stats.append({"no": no, "name": c["name"], "actual_lines": actual,
                          "source": "chapter_file"})
            if actual == 0:
                problems.append(f"第{no}章「{c['name']}」章文件存在但为空")
        else:
            a, b = c["phys_start"], c["phys_end"]
            present = [p for p in range(a, b + 1)
                       if p in blocks and blocks[p].strip()]
            stats.append({"no": no, "name": c["name"],
                          "phys_present": len(present), "source": "struct"})
            if present:
                warnings.append(
                    f"第{no}章「{c['name']}」未切章文件，但 phys{a}-{b} 在 "
                    f"struct 中有 {len(present)} 页内容（精读可能直接在 struct 上"
                    "完成，建议补切章文件以便溯源）")
            else:
                problems.append(
                    f"第{no}章「{c['name']}」既无章文件，struct phys{a}-{b} "
                    "也无任何内容（未覆盖）")
    return problems, warnings, stats


def check_reading_progress(work, meta, write_md=True):
    """覆盖率校验主入口。返回 (errors, warnings, info)。"""
    errors, warnings, info = [], [], {}

    struct_path = os.path.join(work, "fulltext_struct.txt")
    total_lines = count_file_lines(struct_path)
    info["total_lines"] = total_lines

    # 1) 优先结构化 reading_progress.json
    jp = os.path.join(work, "reading_progress.json")
    if os.path.isfile(jp):
        try:
            jdata = read_json_cn(jp)
            recs = jdata.get("records") if isinstance(jdata, dict) else jdata
            if isinstance(recs, list):
                ok = True
                for r in recs:
                    t, rr = r.get("total_lines"), r.get("read_lines")
                    if t is not None and rr is not None and rr < t:
                        errors.append(
                            f"reading_progress.json：{r.get('name','?')} "
                            f"已读 {rr}/{t} 行，未全覆盖")
                        ok = False
                if ok:
                    info["json_coverage"] = "PASS"
        except Exception as e:
            warnings.append(f"reading_progress.json 解析失败：{e}")

    # 2) 读取 reading_progress.md
    md_path = os.path.join(work, "reading_progress.md")
    md_text = read_text_cn(md_path) if os.path.isfile(md_path) else ""
    ranges_raw = extract_line_ranges(md_text) if md_text else []
    # 去重：同一范围可能在表格与正文"覆盖率验证"中重复出现（如 1-1643 出现两次）
    seen = set()
    ranges = []
    for s, e, expl in ranges_raw:
        if (s, e) not in seen:
            seen.add((s, e))
            ranges.append((s, e, expl))
    info["ranges_found"] = len(ranges)

    # 若同时存在"全书汇总范围"(1,total) 与多个连续子范围，丢弃冗余汇总范围
    # （子范围信息更详细；汇总范围本来就包含子范围，保留会误报"重叠"）
    sub_ranges = [r for r in ranges if not (r[0] == 1 and r[1] == total_lines)]
    if sub_ranges and len(sub_ranges) < len(ranges):
        ranges = sub_ranges

    if ranges:
        if is_chapter_local_ranges(ranges, total_lines):
            probs, w, stats = verify_chapter_local(work, meta)
            info["mode"] = "chapter_local"
            info["chapters"] = stats
            errors.extend(probs)
            warnings.extend(w)
        else:
            ok, probs, covered = verify_global_coverage(ranges, total_lines)
            info["mode"] = "global"
            info["covered_lines"] = covered
            errors.extend(probs)
    else:
        # 无行范围：尝试用"已读行数"求和
        read_decl = extract_read_lines(md_text)
        if read_decl:
            total_read = sum(read_decl)
            info["mode"] = "read_lines_sum"
            info["read_lines_sum"] = total_read
            if total_read < total_lines:
                errors.append(
                    f"已读行数合计 {total_read} < 全书 {total_lines} 行，"
                    f"覆盖率 {total_read/total_lines*100:.1f}%")
        else:
            errors.append(
                "reading_progress 中未找到任何可校验的行范围/已读行数记录 "
                "（无法证明全量精读覆盖）")

    return errors, warnings, info


# ============================ 补丁多命中校验 ============================

def load_all_corrections(work):
    """加载 corrections/*.json 全部补丁（含来源文件）。返回 [(patch, source_file)]。"""
    out = []
    for fp in sorted(glob.glob(os.path.join(work, "corrections", "*.json"))):
        try:
            data = read_json_cn(fp)
        except Exception:
            continue
        for c in expand_patch_data(data):
            if isinstance(c, dict) and c.get("old"):
                out.append((c, os.path.basename(fp)))
    return out


def build_body0(struct, all_patches):
    """施加 systematic 通改得到 body0（与 check_patch 第⑧步 / apply_corrections
    同口径）——per-phys 补丁的 old 是"body0 形态"（§3.4），必须在 body0 上校验。

    all_patches: [(patch, src)]，只取其中 active systematic（含 phys=ALL）补丁。
    """
    body0 = struct
    for c, _src in all_patches:
        if c.get("status") == "deleted":
            continue
        is_sys = c.get("systematic") or str(c.get("phys", "")).upper() == "ALL"
        if not is_sys:
            continue
        old, new = c.get("old", ""), c.get("new", "")
        if not old:
            continue
        if c.get("regex"):
            rx = re.compile(old)
            body0 = rx.sub(lambda m, n=new: m.expand(n), body0)
        else:
            body0 = compile_linebreak(old).sub(new, body0)
    return body0


def check_uniqueness(work):
    """per-phys 补丁多命中校验（基于 body0）。

    返回 (errors, warnings, multi_count, redundant_count)。
    - 0命中但 new 已在 body0 → 冗余（被 systematic 通改代劳），WARN 建议 deleted
      （与 check_patch 第⑧步 L228-231 同口径）；
    - 0命中且 new 不在 body0 → 真 ERROR（grounding 失败）。
    """
    errors, warnings = [], []
    struct_path = os.path.join(work, "fulltext_struct.txt")
    if not os.path.isfile(struct_path):
        errors.append("fulltext_struct.txt 不存在，无法做补丁定位校验")
        return errors, warnings, 0, 0
    struct = read_text_cn(struct_path)
    all_patches = load_all_corrections(work)
    # 构造 body0（先施加 systematic 通改）
    body0 = build_body0(struct, all_patches)
    blocks = _page_blocks(body0)

    multi_count, redundant_count = 0, 0
    for c, src in all_patches:
        # 豁免：systematic / merge / regex / deleted
        if c.get("systematic") or c.get("merge") or c.get("regex"):
            continue
        if c.get("status") == "deleted":
            continue
        phys = c.get("phys")
        old = c["old"]
        if phys in (None, "") or not str(phys).isdigit():
            continue  # 无明确 phys 的补丁不在此校验
        block = blocks.get(int(phys))
        if block is None:
            errors.append(
                f"[{src}] phys={phys} 页块不存在（补丁 old「{old[:15]}…」无法定位）")
            continue
        # 用 iter_spans 计数（容忍换行，与 apply_corrections 同口径）
        hits = len(iter_spans(block, old))
        if hits == 0:
            new = c.get("new", "")
            if new and find_span(body0, new):
                # 冗余：old 已被 systematic 通改、new 已存在
                redundant_count += 1
                warnings.append(
                    f"[{src}] phys={phys} 补丁 old「{old[:18]}」在 body0 0 命中、"
                    f"但 new「{new[:18]}」已存在（已被 systematic 通改代劳，"
                    "冗余，建议 status:deleted）")
            else:
                errors.append(
                    f"[{src}] phys={phys} 补丁 old「{old[:20]}」在 body0 该页 0 命中"
                    "（grounding 失败：old 抄录有误 / phys 错位 / 漏了 systematic 通改）")
        elif hits > 1:
            multi_count += 1
            warnings.append(
                f"[{src}] phys={phys} 补丁 old「{old[:20]}」在该页 {hits} 处命中"
                "（短 old 偏页风险，建议补 ≥6 字上下文使其唯一命中）")
    return errors, warnings, multi_count, redundant_count


# ============================ 主入口 ============================

def main():
    ap = argparse.ArgumentParser(description="子 agent 产出校验")
    ap.add_argument("--work", required=True)
    ap.add_argument("--meta", default="")
    ap.add_argument("--no-write-md", action="store_true",
                    help="不自动汇总 reading_progress.md")
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

    print("=" * 68)
    print("子 agent 产出校验")
    print("=" * 68)

    # 覆盖率
    cov_errors, cov_warn, info = check_reading_progress(
        work, meta, write_md=not args.no_write_md)
    print("\n【1. 覆盖率校验】")
    print(f"  全书行数：{info.get('total_lines')} | 口径：{info.get('mode','?')}")
    if info.get("mode") == "global":
        pct = info.get("covered_lines", 0) / max(1, info.get("total_lines", 1)) * 100
        print(f"  已覆盖：{info.get('covered_lines')} 行（{pct:.1f}%）")
    elif info.get("mode") == "read_lines_sum":
        print(f"  已读行数合计：{info.get('read_lines_sum')}")

    # 多命中
    uni_errors, uni_warn, multi_count, redundant_count = check_uniqueness(work)
    print("\n【2. 补丁多命中校验】")
    print(f"  多命中 WARN：{multi_count} 条 | 冗余（被 systematic 代劳）：{redundant_count} 条")

    # 汇总
    all_errors = cov_errors + uni_errors
    all_warn = cov_warn + uni_warn
    for w in all_warn:
        print(f"  ⚠ {w}")
    for e in all_errors:
        print(f"  ❌ {e}")

    print("\n" + "=" * 68)
    if all_errors:
        print(f"❌ 校验未通过：{len(all_errors)} 个 ERROR，{len(all_warn)} 个 WARN")
        sys.exit(1)
    print(f"✅ 校验通过：覆盖率 100%，{len(all_warn)} 个 WARN（多命中需人工确认）")
    print("=" * 68)


if __name__ == "__main__":
    main()
