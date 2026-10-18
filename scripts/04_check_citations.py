#!/usr/bin/env python3
# 04_check_citations.py —— 引文回查：把笔记里的关键引句，回底本核对
#
# 两种模式：
#   ① 手工模式（--checks）：人工编写 (页码, 引句) 清单，逐条回该页核对。精确但需人工备料。
#   ② 自动模式（--auto，v1.28.0 新增）：直接从 notes/*.md 抽取引号包裹的引句，
#      回底本全文匹配，**逐条标注命中 / [未能验证]**，产出 citation_report.md。
#      解决「--checks 须人工编写」这一最大痛点——清单自动生成，人只做判级。
#
# 背景（2026-09-05 参数化改造）：
#   旧版把 RAW 路径与 CHECKS 列表**写死在源码里**（硬编码某一本书 + 手填示例数据），
#   用户每次用都得改源码——而改的是共享技能目录（双机同步，会覆盖另一台的改动）。
#   现改为全部走命令行入参，技能内零硬编码。
#
# 用法：
#   python 04_check_citations.py --raw <fulltext_raw.txt> --checks <checks.json|checks.csv>
#   python 04_check_citations.py --auto --notes <notes目录> --raw <fulltext_raw.txt> \
#          [--clean <03_干净本.txt>] [--out <citation_report.md>] [--min-len 8] [--strict]
#
# --raw    ：法医底本（01a_raw.py 产出的 fulltext_raw.txt，含 <<<PAGE phys=N>>> 标记）
# --checks ：待核对项。两种格式均可，按扩展名自动识别：
#     .json → [{"page":"90","frag":"牵一发而动全身"}, ...] 或 [["90","牵一发而动全身"], ...]
#     .csv/.txt → 每行一条，形如  90,牵一发而动全身  （逗号分隔；页码中不含逗号）
#
# 退出码：0=全部通过；1=存在未命中（有引句在该页查不到，须人工核对）；2=入参/文件缺失。
#
# 说明：本工具仅用于 PDF 底本，锚解析兼容两种形态——
#       · 法医底本 fulltext_raw.txt：<<<PAGE phys=N>>>（仅 phys，无 book）；
#       · 01b 加锚后的 fulltext_struct.txt：<<<PAGE phys=N book=M>>>（含 book）。
#       pages 字典同时以 phys 与 book 为键，故 --checks 的 page 传 phys 或 book 值均可命中。
#       注：EPUB 底本锚为 <<<SEC idx=N ...>>>，本工具正则匹配不到，须改用 assemble 的引用审计。
#       自动模式（--auto）不依赖锚——它对底本做**归一化全文子串匹配**，故两路径（PDF/EPUB）通用。

import re, os, sys, json, argparse
from _common import (  # P0-2（2026-10-04 审查）：收敛锚块正则（含正文第三组）到 _common 单一真相源
    ANCHOR_BLOCK_RE,
    safe_write,   # v1.28.0 自动模式报告落盘（LF 原子写，守 §6.0 换行符铁律）
)


def load_checks(path):
    """从 json / csv 载入待核对项，统一返回 [(page:str, frag:str)]。"""
    if not os.path.isfile(path):
        sys.stderr.write(f"[check_citations] 找不到核对清单: {path}\n")
        sys.exit(2)
    ext = os.path.splitext(path)[1].lower()
    out = []
    if ext == ".json":
        data = json.load(open(path, encoding="utf-8"))
        # 🟡21（2026-09-08 剩余批）：dict 但缺 "checks" 键时，旧实现 rows=data（仍是 dict），
        # 遍历得到键字符串、r[0] 取出单字符——静默错乱。现明确报错退出 2。
        if isinstance(data, dict):
            if "checks" not in data:
                sys.stderr.write('[check_citations] --checks JSON 为 dict 但缺 "checks" 键'
                                 '（应为 {"checks": [{"page": "...", "frag": "..."}]}）。\n')
                sys.exit(2)
            rows = data["checks"]
        else:
            rows = data
        for r in rows:
            if isinstance(r, dict):
                p, f = str(r.get("page", "")).strip(), str(r.get("frag", "")).strip()
            else:
                p, f = str(r[0]).strip(), str(r[1]).strip()
            if p and f:
                out.append((p, f))
    else:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p, _, f = line.partition(",")
            if p.strip() and f.strip():
                out.append((p.strip(), f.strip()))
    return out


# ---------------------------------------------------------------------------
# 自动模式（v1.28.0）：从 notes 抽取引句 → 底本全文匹配 → 逐条标 [未能验证]
# ---------------------------------------------------------------------------

# 引句抽取：引号包裹且长度 ≥ min_len。三种引号族覆盖中英混排。
_QUOTE_PATS_CACHE = {}


def _quote_patterns(min_len):
    if min_len not in _QUOTE_PATS_CACHE:
        n = max(1, int(min_len))
        _QUOTE_PATS_CACHE[min_len] = [
            re.compile(r"「([^」\n]{%d,})」" % n),
            re.compile(r"『([^』\n]{%d,})』" % n),
            # v1.32.1 补：简体出版物惯用中文弯引号“”，原三族只认「」『』与半角 "，
            # 笔记按国标写“…”时抽不到任何引句（实测本书 93 行引句全被跳过，
            # 工具报「未抽到引句」），闸4 形同虚设。
            re.compile(r"“([^”\n]{%d,})”" % n),
            re.compile(r'"([^"\n]{%d,})"' % n),
        ]
    return _QUOTE_PATS_CACHE[min_len]


def extract_citations(notes_dir, min_len=8):
    """从 notes 目录的 .md 中抽取引句，返回 [(文件名, 行号, 引句)]（已去重，保序）。"""
    if not os.path.isdir(notes_dir):
        sys.stderr.write(f"[check_citations] 找不到笔记目录: {notes_dir}\n")
        sys.exit(2)
    out, seen = [], set()
    for name in sorted(os.listdir(notes_dir)):
        if not name.lower().endswith(".md"):
            continue
        path = os.path.join(notes_dir, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh.read().splitlines(), 1):
                for pat in _quote_patterns(min_len):
                    for m in pat.finditer(line):
                        frag = m.group(1).strip()
                        if frag and frag not in seen:
                            seen.add(frag)
                            out.append((name, i, frag))
    return out


def run_auto_mode(args):
    """自动模式：笔记引句 → 底本（raw ∪ clean）归一化全文匹配，产出逐条报告。"""
    notes_dir = args.notes or (os.path.join(args.work, "notes") if args.work else None)
    if not notes_dir:
        sys.stderr.write("[check_citations] --auto 需指定 --notes 或 --work\n")
        sys.exit(2)

    bases = []
    for p in (args.raw, args.struct, args.clean):
        if p:
            if not os.path.isfile(p):
                sys.stderr.write(f"[check_citations] 找不到底本: {p}\n")
                sys.exit(2)
            bases.append(p)
    if not bases:
        sys.stderr.write("[check_citations] --auto 至少需提供 --raw / --struct / --clean 之一作为比对源\n")
        sys.exit(2)

    # 比对源 = 各底本归一化后拼接。取并集的意义：笔记引句可能引自纠正后正字
    # （raw 里没有，struct/clean 里才有），只比对 raw 会造成假未命中（见 §6.1.1）。
    # 两份干草垛：norm=去空白（精确档）；punct=再剥标点（OCR 标点差异容忍档）
    hay_norm, hay_punct = [], []
    for p in bases:
        with open(p, encoding="utf-8") as fh:
            raw = fh.read()
        n = re.sub(r"\s+", "", raw)
        hay_norm.append(n)
        hay_punct.append(re.sub(r"[^\w]", "", n))
    haystack = "\n".join(hay_norm)
    haystack_punct = "\n".join(hay_punct)

    cites = extract_citations(notes_dir, args.min_len)
    if not cites:
        sys.stderr.write(f"[check_citations] 未在 {notes_dir} 抽到引句（引号包裹且 ≥{args.min_len} 字）。\n")
        sys.exit(2)

    # 三档判定：精确 → 剥分节号 → 剥标点。后两档是「结构性容错」不是放松标准：
    # 学术译本插 [4]/［4］、OCR 把逗号读成句号，都会把合法引文切断成假未命中
    # （与 §6.1.1 留痕/分节号容错同源）。标点档命中单独标注，便于人工复核。
    ok_list, punct_list, bad_list = [], [], []
    for fn, ln, frag in cites:
        needle = re.sub(r"\s+", "", frag)
        needle_alt = re.sub(r"[［\[]\d+[］\]]", "", needle)
        needle_punct = re.sub(r"[^\w]", "", needle)
        if needle in haystack or (needle_alt and needle_alt in haystack):
            ok_list.append((fn, ln, frag))
        elif needle_punct and needle_punct in haystack_punct:
            punct_list.append((fn, ln, frag))
        else:
            bad_list.append((fn, ln, frag))

    total = len(cites)
    hit_all = len(ok_list) + len(punct_list)
    rate = (hit_all / total * 100) if total else 0.0
    print(f"自动引文回查：{notes_dir}")
    print(f"  比对源：{', '.join(os.path.basename(p) for p in bases)}")
    print(f"  抽到引句 {total} 条 → 精确命中 {len(ok_list)}，标点差异命中 {len(punct_list)}，"
          f"未能验证 {len(bad_list)}（逐字率 {rate:.1f}%）")
    for fn, ln, frag in bad_list[:15]:
        print(f"    [未能验证] {fn}:{ln}  「{frag[:60]}」")
    if len(bad_list) > 15:
        print(f"    … 另有 {len(bad_list) - 15} 条（见报告）")

    out_path = args.out or (os.path.join(args.work, "citation_report.md") if args.work else "citation_report.md")
    lines = ["# 引文回查报告（自动模式）", "",
             f"- 笔记目录：`{notes_dir}`",
             f"- 比对源：{', '.join(os.path.basename(p) for p in bases)}（归一化后取并集，兼容纠正后正字）",
             f"- 抽到引句 **{total}** 条：精确命中 **{len(ok_list)}**，标点差异命中 **{len(punct_list)}**，"
             f"**[未能验证] {len(bad_list)}**（逐字率 **{rate:.1f}%**）", "",
             "## 一、未能验证（须逐条人工判级）", ""]
    if bad_list:
        lines.append("| # | 出处 | 引句 | 状态 |")
        lines.append("|---|---|---|---|")
        for i, (fn, ln, frag) in enumerate(bad_list, 1):
            lines.append(f"| {i} | `{fn}:{ln}` | 「{frag}」 | **[未能验证]** |")
    else:
        lines.append("（无）")
    lines += ["", "## 二、已验证命中", ""]
    if ok_list:
        lines.append("| # | 出处 | 引句 | 状态 |")
        lines.append("|---|---|---|---|")
        for i, (fn, ln, frag) in enumerate(ok_list, 1):
            lines.append(f"| {i} | `{fn}:{ln}` | 「{frag}」 | ✅ 命中 |")
    else:
        lines.append("（无）")
    lines += ["", "## 三、标点差异命中（已计入通过，建议复核）", ""]
    if punct_list:
        lines.append("| # | 出处 | 引句 | 状态 |")
        lines.append("|---|---|---|---|")
        for i, (fn, ln, frag) in enumerate(punct_list, 1):
            lines.append(f"| {i} | `{fn}:{ln}` | 「{frag}」 | ⚠ 命中（标点差异） |")
    else:
        lines.append("（无）")
    lines += ["", "> 判级指引：`[未能验证]` ≠ 一定是错的——可能是①引句被 OCR 断行/错字切断；"
              "②笔记改写过而非直引；③真正编造。前两类回底本核对后订正，第三类必须删除。"
              "逐字率不达标（<95%）须订正笔记引文或改判后重填，不得跳过（闸4）。"]
    safe_write(out_path, "\n".join(lines) + "\n")
    print(f"\n报告已写入：{out_path}")

    if args.strict and bad_list:
        sys.exit(1)
    sys.exit(0)


def main():
    ap = argparse.ArgumentParser(description="引文回查（手工 --checks / 自动 --auto 两种模式）")
    ap.add_argument("--raw", help="fulltext_raw.txt 法医底本路径")
    ap.add_argument("--struct", help="fulltext_struct.txt 加锚底本（自动模式比对源之一）")
    ap.add_argument("--clean", help="03 干净本（自动模式比对源之一，容纳纠正后正字）")
    ap.add_argument("--checks", help="待核对清单（.json 或 .csv/.txt），**须人工编写提供**（本工具不自动生成清单）")
    ap.add_argument("--auto", action="store_true", help="自动模式：从 notes 抽引句，无需人工备清单")
    ap.add_argument("--notes", help="自动模式：笔记目录")
    ap.add_argument("--work", help="work 目录（自动模式未给 --notes 时取 <work>/notes）")
    ap.add_argument("--out", help="自动模式：报告落点（默认 <work>/citation_report.md）")
    ap.add_argument("--min-len", type=int, default=8, help="自动模式：引句最短字数（默认 8）")
    ap.add_argument("--strict", action="store_true", help="自动模式：存在 [未能验证] 即退出 1")
    args = ap.parse_args()

    if args.auto:
        run_auto_mode(args)
        return

    if not args.checks:
        sys.stderr.write("[check_citations] 手工模式需 --checks 清单；或改用 --auto 自动抽取引句。\n")
        sys.exit(2)
    if not args.raw:
        sys.stderr.write("[check_citations] 手工模式需 --raw 底本。\n")
        sys.exit(2)

    if not os.path.isfile(args.raw):
        sys.stderr.write(f"[check_citations] 找不到底本: {args.raw}\n")
        sys.exit(2)

    raw = open(args.raw, encoding="utf-8").read()

    # 原书页 -> 该页纯文本（去所有空白，便于片段匹配）
    # 2026-09-08 R2 修复：phys 与 book 拆成两个字典——此前混用同一 dict 的字符串键，
    # 当某页的 book 值撞上另一页的 phys 值时（OFFSET≠0 的 PDF 必然大量发生），
    # 后读的块会覆盖先读的键，导致按「原书页 N」回查时命中错误页的内容。
    pages_by_phys = {}
    pages_by_book = {}
    for m in ANCHOR_BLOCK_RE.finditer(raw):
        phys, book, body = m.group(1), m.group(2), m.group(3)
        text = re.sub(r"\s+", "", body)
        pages_by_phys[phys] = text
        if book and book != "None":
            pages_by_book[book] = text

    def lookup(page):
        # v1.16.2 修复：book 与 phys 是两套坐标系，跨坐标回退会取到另一页内容导致假通过
        # （印页值恰等于某 phys 时命中错误页）。判定规则：
        #  - struct 底本含真实 book 字段（pages_by_book 非空）→ 核对 page 即原书页，只查 book，不回退 phys；
        #  - 01a_raw 法医底本无 book（全 None）→ 核对 page 即 phys，只查 phys。
        if pages_by_book:
            return pages_by_book.get(page) or ""
        return pages_by_phys.get(page) or ""

    checks = load_checks(args.checks)
    if not checks:
        sys.stderr.write("[check_citations] 核对清单为空，无可校验项。\n")
        sys.exit(2)

    passed, failed = 0, []
    for page, frag in checks:
        page_text = lookup(page)
        # 归一化与页文本一致：去所有空白（含全角空格/制表符），而非仅半角空格
        ok = re.sub(r"\s+", "", frag) in page_text
        if ok:
            passed += 1
        else:
            failed.append((page, frag))
        print(f"[{'OK' if ok else 'XX'}] 原书页{page} 含「{frag}」")

    print(f"\n通过 {passed}/{len(checks)}")
    if failed:
        print(f"未命中 {len(failed)} 条（须人工核对：页码是否填错 / 引句是否出自该页 / 是否 OCR 误读）：")
        for page, frag in failed:
            print(f"  - 页{page} 「{frag}」")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
