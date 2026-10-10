#!/usr/bin/env python3
# 00e_scan_simplify_leak.py —— 简繁码位抖动全量扫描（只读 · 不改正文）
#
# 用途：OCR 引擎（尤其 PaddleOCR）对繁体底本会**整类**输出简体码位（如全书「吳」0 见、
#   「吴」439 见），也会把新字形对 / 古典两合法字误判成错字。本脚本在派工前（00d 后 ·
#   Stage 3 前）对 fulltext_struct.txt 做一次全量逐字判定，产出比值表，供：
#   ① 派工 brief 写明「哪些字是整类泄漏、走机械补丁/通改，Agent 不必逐处判」；
#   ② 终检时核对 03 残留（目标字形应清零，保留字应原样）。
#
# 判定（依赖 opencc，缺依赖脚本退出码 2 并提示安装）：
#   对每个 CJK 字 ch：s2t(ch) != ch 且 t2s(ch) == ch → 「简体独有形」候选。
#   再按该书内 简形 n1 / 繁形 n2 比值分三档：
#     n2 == 0                  → 简形独占。两可：专名/地名等「必印繁形」字疑整类泄漏（须通改）；
#                                古典两合法字（云=曰、咸=皆、征=征伐、后=皇后）可能系底本
#                                惯例（勿动）。逐字人工判，禁止全局通改。
#     0 < n1 <= n2 * --ratio   → 混用·繁形主导 → OCR 码位抖动，建议通改/机械补丁。
#     其余                      → 混用·简形占比高 → 人工判。
#   新字形对（后/内/宫/争/温/恒…）、异体两合法对（于/於、万俟）一律「人工判·勿机械通改」。
#
# 用法：
#   python 00e_scan_simplify_leak.py --work <work目录> [--ratio 0.15] [--min-count 1]
#                                     [--struct fulltext_struct.txt] [--out <报告目录>]
#   python 00e_scan_simplify_leak.py --root <books根目录>   # 批量
#
# 产出（每书一份，LF）：
#   simplify_leak_report.md   三档比值表 + 每字样例（含 phys 定位）
#
# 只读扫描，不写回 fulltext_struct.txt。

import argparse
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- 共享口径（单一真相源：同目录 _fig_common.py）----
from _fig_common import PAGE_RE  # noqa: E402

try:
    from opencc import OpenCC
except ImportError:
    print("[00e] 缺依赖 opencc：先在技能规定的 venv 中安装再重跑（见 SKILL.md §1 环境准备）：")
    print("      <PY> -m pip install opencc-python-reimplemented   # PY 解析见 skill-dependencies.json")
    sys.exit(2)

# v1.16.2 修复：新字形对 / 异体两合法对两形皆合法（底本用字习惯），禁止机械通改。
# 旧版 docstring 声明排除，代码无排除表 → 后/内/宫/争 等被误判"建议通改"（审计 §三 高）。
# 这里仅列简形（脚本只扫 s2t!=ch 且 t2s==ch 的简形独有形），命中即强制"人工判"。
_SIMPLIFY_EXCLUDE = set("后内宫争温恒于云咸征采才群峰并凡帆万")


def phys_index(text):
    """返回 [(start, phys), ...] 升序，供 pos→phys 查询。"""
    return [(m.start(), int(m.group(1))) for m in PAGE_RE.finditer(text)]


def pos_to_phys(idx, pos):
    cur = 0
    for start, phys in idx:
        if start <= pos:
            cur = phys
        else:
            break
    return cur


def contexts(text, ch, idx, limit=3, width=14):
    out = []
    for m in list(re.finditer(re.escape(ch), text)):
        p = pos_to_phys(idx, m.start())
        ctx = text[max(0, m.start() - width):m.end() + width].replace("\n", "⏎")
        out.append(f"phys{p}: …{ctx}…")
        if len(out) >= limit:
            break
    return out


def scan_book(work, ratio, min_count, struct_name, out_dir):
    struct = os.path.join(work, struct_name)
    if not os.path.isfile(struct):
        print(f"[00e] 跳过（无 {struct_name}）: {work}")
        return None
    text = open(struct, encoding="utf-8").read()
    idx = phys_index(text)
    t2s, s2t = OpenCC("t2s"), OpenCC("s2t")

    rows = []
    for ch in sorted(set(text)):
        if not ("\u4e00" <= ch <= "\u9fff"):
            continue
        if not (s2t.convert(ch) != ch and t2s.convert(ch) == ch):
            continue
        # v1.16.2 修复：新字形对/异体两合法对强制人工判（见 _SIMPLIFY_EXCLUDE）
        if ch in _SIMPLIFY_EXCLUDE:
            T = s2t.convert(ch)
            n1 = text.count(ch)
            n2 = text.count(T)
            verdict = "新字形对/异体两合法对→人工判（两形皆合法，禁止机械通改）"
            rows.append((ch, n1, T, n2, verdict, contexts(text, ch, idx)))
            continue
        n1 = text.count(ch)
        if n1 < min_count:
            continue
        T = s2t.convert(ch)
        n2 = text.count(T)
        if n2 == 0:
            verdict = "简形独占→人工判（专名/必印繁形字疑整类泄漏，须通改；古典两合法字系底本惯例，勿动）"
        elif n1 <= n2 * ratio:
            verdict = f"混用·繁形主导（n1<=n2×{ratio}）→ OCR 码位抖动，建议通改"
        else:
            verdict = "混用·简形占比高→人工判"
        rows.append((ch, n1, T, n2, verdict, contexts(text, ch, idx)))

    rows.sort(key=lambda r: -r[1])
    n_auto = sum(1 for r in rows if r[4].startswith("混用·繁形主导"))
    n_solo = sum(1 for r in rows if r[4].startswith("简形独占"))
    n_mix = len(rows) - n_auto - n_solo

    lines = [
        "# 简繁码位抖动扫描报告（00e · 只读）",
        "",
        f"- 底本：`{struct_name}`（{len(idx)} 页锚）",
        f"- 判定档：通改候选 {n_auto} 字 · 简形独占（人工判）{n_solo} 字 · 混用占比高（人工判）{n_mix} 字",
        f"- 阈值：ratio={ratio}，min_count={min_count}",
        "",
        "## 三档要点",
        "",
        "- **通改候选**：简形零星混入、繁形占主导 → 可入 systematic 通改或机械补丁（先核 §3.8 收窄口径）。",
        "- **简形独占**：繁形 0 见 ≠ 底本惯例。先判该书底本「必印繁形吗」——专名/地名类是整类泄漏，",
        "  古典两合法字（云/咸/征/后…）是底本用字。逐字看样例定夺，禁止按「同占位=同字」全局通改。",
        "- **混用占比高**：简形成规模出现而繁形也不少 → 多半两形皆合法（新字形对/异体对），保留。",
        "",
        "## 明细",
        "",
        "| 简形 | n1 | 繁形 | n2 | 判定 | 样例 |",
        "|---|---|---|---|---|---|",
    ]
    for ch, n1, T, n2, verdict, ctxs in rows:
        sample = "<br>".join(ctxs) if ctxs else "（—）"
        sample = sample.replace("|", "\\|")
        lines.append(f"| {ch} | {n1} | {T} | {n2} | {verdict} | {sample} |")
    lines.append("")

    out_path = os.path.join(out_dir, "simplify_leak_report.md")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(f"[00e] {work} → {out_path}（通改候选 {n_auto} / 独占 {n_solo} / 高占比 {n_mix}）")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="简繁码位抖动全量扫描（只读）")
    ap.add_argument("--work", default="", help="单本书 work 目录（含 fulltext_struct.txt）")
    ap.add_argument("--root", default="", help="批量：books 根目录")
    ap.add_argument("--ratio", type=float, default=0.15, help="繁形主导阈值（默认 0.15）")
    ap.add_argument("--min-count", type=int, default=1, help="低于该出现次数的简形不列（默认 1）")
    ap.add_argument("--struct", default="fulltext_struct.txt", help="底本文件名")
    ap.add_argument("--out", default="", help="报告输出目录（缺省=work 或 root）")
    a = ap.parse_args()
    if not a.work and not a.root:
        ap.error("须给 --work 或 --root")
    out_dir = a.out
    if a.work:
        scan_book(a.work, a.ratio, a.min_count, a.struct, out_dir or a.work)
    else:
        books = sorted(d for d in glob.glob(os.path.join(a.root, "*"))
                       if os.path.isfile(os.path.join(d, a.struct)))
        if not books:
            print(f"[00e] {a.root} 下无含 {a.struct} 的书目录")
            return
        for d in books:
            # v1.16.2 修复：批量模式 --out 共用会逐本覆盖、只剩最后一本报告。每本落到 --out 下的子目录。
            _out = os.path.join(out_dir, os.path.basename(d)) if out_dir else d
            os.makedirs(_out, exist_ok=True)
            scan_book(d, a.ratio, a.min_count, a.struct, _out)


if __name__ == "__main__":
    main()
