#!/usr/bin/env python3
# 04_check_citations.py —— 手工引文回查：把笔记里的关键引句/数字，回 fulltext_raw.txt 对应原书页核对
#
# 背景（2026-09-05 参数化改造）：
#   旧版把 RAW 路径与 CHECKS 列表**写死在源码里**（硬编码某一本书 + 手填示例数据），
#   用户每次用都得改源码——而改的是共享技能目录（双机同步，会覆盖另一台的改动）。
#   现改为全部走命令行入参，技能内零硬编码。
#
# 用法：
#   python 04_check_citations.py --raw <fulltext_raw.txt> --checks <checks.json|checks.csv>
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

import re, os, sys, json, argparse
from _common import ANCHOR_BLOCK_RE  # P0-2（2026-10-04 审查）：收敛锚块正则（含正文第三组）到 _common 单一真相源


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


def main():
    ap = argparse.ArgumentParser(description="手工引文回查（笔记引句 → fulltext_raw 原书页核对）")
    ap.add_argument("--raw", required=True, help="fulltext_raw.txt 法医底本路径")
    ap.add_argument("--checks", required=True, help="待核对清单（.json 或 .csv/.txt），**须人工编写提供**（本工具不自动生成清单）")
    args = ap.parse_args()

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
