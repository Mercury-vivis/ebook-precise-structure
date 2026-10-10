#!/usr/bin/env python3
# 00d_scan_figure_gaps.py —— 图片缺口全库扫描（A/C 两类统一 · 纯标准库零依赖）
#
# 用途：找出「图片相关缺口」页，只读扫描、不修改正文，供 08_backfill_figures.py 回溯补图。
#   A 类（img 断链）：页内有 <img> marker，但引用的图文件在 work/imgs 与 deliv/imgs 都不存在——
#                     引用悬空，00c 已据此把该页整页保护为「待补图」，这里统一列出。
#   C 类（疑似图片文字层）：页内无 table/img marker，文字层只剩图内标注散字——原本应是一整幅图，
#                     却只有碎字（无图可替代，剥离即丢信息）。
#
# v1.9.1 口径统一：不再只扫「无 table/img」的页；改为扫全部页，A/C 两类并入同一份「图片缺口清单」。
#   C 类与 00c 同口径：markdown 标题页 / 目录页 / 版权页整页性质特殊，不是图片缺口，跳过（不误送 08）。
#
# 判据：
#   A 类：页内含 <img ... src="...">，且任一引用图 basename 不在 work/imgs 与 deliv/imgs。
#   C 类：页内无 <table>/<img> marker；非空行 ≥5；短行(去空白后<15字)占比 ≥80%；
#         句末标点行(。！？…) ≤ max(1, 非空行数×2%)；且非标题/目录/版权排除页。
#
# 用法：
#   python 00d_scan_figure_gaps.py --work <单本书work目录> [--deliv <交付目录>] [--out <报告目录>]
#   python 00d_scan_figure_gaps.py --root <books根目录>   [--deliv <交付目录>] [--out <报告目录>]
#     --work   含 fulltext_struct.txt 的单本书工作目录
#     --root   批量模式：遍历 root 下所有含 fulltext_struct.txt 的子目录
#     --deliv  交付目录（<书名>）；缺省按 work 路径推断（../../../<basename>）
#     --out    报告输出目录（单本缺省=work；批量缺省=root）
#
# 产出（每书一份）：
#   figure_gaps_report.md  统一清单：类别 / phys / 非空行数 / 短行比 / 缺失img / 已有候选图 / 样例 / 建议处置
#   figure_gaps.json      同构结构化数据（供 08 补图脚本消费）
#
# 不写回 fulltext_struct.txt；只读扫描。全 LF。

import os
import re
import sys
import json
import glob
import argparse

# ---- 共享口径（单一真相源：同目录 _fig_common.py）----
# 页锚 / 表图 marker / img src / 句末标点正则、整页排除表常量与判据、
# 交付目录解析与 img 可用性检查，一律取自 _fig_common（与 00c 同源，口径不会分叉）。
from _fig_common import (
    PAGE_RE, MARKER_RE, SENTENCE_END_RE, IMG_SRC_RE,
    SHORT_LINE_THRESHOLD, MIN_NONEMPTY, C_SHORT_RATIO, C_PUNCT_RATIO,
    is_excluded_nonfigure_page, scan_figure_gap_body,
    resolve_deliv, build_available_imgs, find_missing_imgs,
)

# MIN_NONEMPTY / C_SHORT_RATIO 等阈值已上移 _fig_common，本脚本不再复制定义。


def looks_like_figure_gap(body):
    """对单页 body 判 C 类图片缺口（无 marker 的短行散字）。返回 stats dict 或 None。

    判据与 00c 的「疑似图片文字层」同源（_fig_common.scan_figure_gap_body）：
    无 marker、非空行≥MIN_NONEMPTY、短行≥C_SHORT_RATIO、句末标点行≤max(1, n×C_PUNCT_RATIO)、
    且非标题/目录/版权排除页。
    """
    return scan_figure_gap_body(body)


def has_candidate_image(work_dir, deliv, phys):
    """检查 work/imgs/ 与 deliv/imgs/ 是否已有 p{phys}_* 候选补图。"""
    patterns = [
        os.path.join(work_dir, "imgs", f"p{phys}_*"),
        os.path.join(deliv, "imgs", f"p{phys}_*"),
    ]
    hits = []
    for pat in patterns:
        hits.extend(glob.glob(pat))
    return sorted(set(os.path.basename(h) for h in hits))


def scan_book(work_dir, out_dir, deliv):
    struct = os.path.join(work_dir, "fulltext_struct.txt")
    if not os.path.exists(struct):
        return None
    text = open(struct, encoding="utf-8").read()
    ms = list(PAGE_RE.finditer(text))
    # v1.16.2 修复：解析到 0 页锚 → 扫描失败（struct 损坏/锚格式不符），不应包装成"无缺口"。
    if not ms:
        print(f"⚠️ [00d] {work_dir}：fulltext_struct.txt 解析到 0 个 <<<PAGE 锚，扫描失败，报告不可信（请检查 struct 锚格式）。", file=sys.stderr)
    available = build_available_imgs(work_dir, deliv)

    gaps = []
    for i, m in enumerate(ms):
        phys = int(m.group(1))
        s = m.end()
        e = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        body = text[s:e]

        # A 类：有 <img> 但文件缺失（断链）
        missing = find_missing_imgs(body, available)
        if missing:
            gaps.append({
                "class": "A", "phys": phys,
                "missing_imgs": missing,
                "candidate_imgs": has_candidate_image(work_dir, deliv, phys),
                "suggest": "补图：从原 PDF/EPUB 导出该图，放入 work/imgs 后重跑 00c（00c 已将该页整页保护为待补图）",
            })
            continue

        # C 类：无 marker 的短行散字（疑似整图页文字层；排除标题/目录/版权页）
        c = looks_like_figure_gap(body)
        if c:
            c["class"] = "C"
            c["phys"] = phys
            c["missing_imgs"] = []
            c["candidate_imgs"] = has_candidate_image(work_dir, deliv, phys)
            c["suggest"] = "补图：08_backfill_figures.py 从原 PDF 回渲整页为 imgs/p{phys}_backfill.jpg，再以 silent 补丁替换短行文字层"
            gaps.append(c)

    n_a = sum(1 for g in gaps if g["class"] == "A")
    n_c = sum(1 for g in gaps if g["class"] == "C")

    report = [
        f"# 图片缺口扫描报告 — {os.path.basename(work_dir.rstrip(chr(92)))}",
        "",
        f"> 来源：`{struct}`",
        f"> 交付目录：`{deliv}`（work/deliv imgs 可用文件 {len(available)} 个）",
        f"> A 类判据：有 <img> 但引用图缺失（断链）；C 类判据：无 table/img、非空行≥{MIN_NONEMPTY}、"
        f"短行(<{SHORT_LINE_THRESHOLD}字)≥{C_SHORT_RATIO*100:.0f}%、"
        f"句末标点行≤max(1, n×{C_PUNCT_RATIO*100:.0f}%)、排除标题/目录/版权页",
        f"> 总页数：{len(ms)}；图片缺口页：**{len(gaps)}**（A 类断链 {n_a} / C 类疑似文字层 {n_c}）",
        "",
        "| 类别 | phys | 非空行数 | 短行比 | 句末标点数 | 缺失 img | 已有候选图 | 样例/说明 |",
        "|------|-----|----------|--------|------------|----------|------------|-----------|",
    ]
    for g in gaps:
        if g["class"] == "A":
            report.append(
                f"| A（断链） | {g['phys']} | — | — | — | "
                f"{', '.join(g['missing_imgs'])} | "
                f"{', '.join(g['candidate_imgs']) if g['candidate_imgs'] else '—'} | {g['suggest']} |"
            )
        else:
            cand = ", ".join(g["candidate_imgs"]) if g["candidate_imgs"] else "—"
            report.append(
                f"| C（文字层） | {g['phys']} | {g['n']} | {g['short_ratio']*100:.0f}% | "
                f"{g['n_punct']} | — | {cand} | {g['sample']} |"
            )
    report += [
        "",
        "> 说明：本报告只读扫描，不修改正文。A 类断链页 00c 已整页保护为「待补图」；"
        "C 类无候选图的页建议走 08_backfill_figures.py 回溯补图。",
    ]

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "figure_gaps_report.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(report) + "\n")
    with open(os.path.join(out_dir, "figure_gaps.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"work_dir": work_dir, "deliv": deliv, "total_pages": len(ms),
                   "counts": {"A": n_a, "C": n_c}, "gaps": gaps},
                  f, ensure_ascii=False, indent=2)
    return len(gaps)


def main():
    ap = argparse.ArgumentParser(description="图片缺口全库扫描 A/C 两类统一（只读）")
    ap.add_argument("--work", default="", help="单本书 work 目录")
    ap.add_argument("--root", default="", help="批量：books 根目录，遍历含 fulltext_struct.txt 的子目录")
    ap.add_argument("--deliv", default="", help="交付目录（<书名>）；缺省按 work 路径推断")
    ap.add_argument("--out", default="", help="报告输出目录（缺省=work 或 root）")
    args = ap.parse_args()

    if not args.work and not args.root:
        sys.exit("ERROR: 须指定 --work 或 --root")

    if args.work:
        out = args.out or args.work
        deliv = resolve_deliv(args.work, args.deliv)
        n = scan_book(args.work, out, deliv)
        print(f"单本扫描完成：{args.work} → 缺口页 {n}（deliv={deliv}）")
        print(f"报告：{os.path.join(out, 'figure_gaps_report.md')}")
        return

    root = args.root
    books = []
    for sub in sorted(os.listdir(root)):
        d = os.path.join(root, sub)
        if os.path.isfile(os.path.join(d, "fulltext_struct.txt")):
            books.append(d)
    print(f"批量扫描：{root} 下发现 {len(books)} 本")
    total = 0
    for d in books:
        # v1.16.2 修复：批量模式 --out 共用会逐本覆盖、只剩最后一本报告。每本落到 --out 下的
        # 以书名命名的子目录，单本模式（无 --out）仍用本书目录。
        out = os.path.join(args.out, os.path.basename(d)) if args.out else d
        os.makedirs(out, exist_ok=True)
        deliv = resolve_deliv(d, args.deliv)
        n = scan_book(d, out, deliv)
        total += n or 0
        print(f"  {os.path.basename(d)}: 缺口页 {n}")
    print(f"合计缺口页：{total}")


if __name__ == "__main__":
    main()
