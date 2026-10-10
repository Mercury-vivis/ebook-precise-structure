#!/usr/bin/env python3
# 00_ingest_bookocr.py —— ebook 技能「三件套接入」开关（纯文本转换，零 Paddle 依赖）
#
# 作用：把上游 book_ocr.py 产出的「三件套」转换为 ebook-precise-structure
#       既有的中间文件，使其可直接跳到 Stage 3（语义清洗 / 分章精读），省去 Stage 1+2。
#
# 三件套（book_ocr 产物，位于 --bookocr-dir）：
#   <stem>.md            干净正文 + 逐页 ## PAGE phys=N 锚（已剥书眉/页脚/页码）
#   <stem>_raw.md        法医底本（未剥离，逐页 ## PAGE phys=N）  ← 可选，缺则跳过 raw
#   <stem>_版式区块.md   被剥区块附档（## PAGE phys=N + - **[label]** text）  ← 可选
#
# 转换产出（写入 --work，即 .work/books/<书名>）：
#   fulltext_struct.txt  ← <stem>.md，锚改写为 <<<PAGE phys=N book=M>>>
#   fulltext_raw.txt     ← <stem>_raw.md（若存在），锚改写为 <<<PAGE phys=N>>>
#   header_strip_log.md / footer_strip_log.md / other_strip_log.md  ← 附档按 label 拆分
#   page_index.json      物理页↔印页映射（含 phys_count）
#   book_map_log.md      印页推导过程与异常标注（供人工抽检）
#   imgs/                ← 源目录 imgs/ 同步过来（book_ocr 裁图，供 Tier1 复用、跳过 07_）
#
# ★ book=M 全自动推导（无需用户给页码表）：
#   从 _版式区块.md 的 [number] 块解析「每页印页号码」作锚点，优先取「众数偏移」
#   （book = phys - 众数偏移）覆盖全书；若锚点散布（如大量删页/双套页码）则退化为
#   「锚点插值」。前页（book<1）自动标记为 front_matter。OCR 噪声（插图/脚注号）
#   与断档写入 book_map_log.md 供抽检。仅当自动结果明显异常时，才用 --offset/
#   --pagemap 人工覆盖。
#
# 注意：本脚本只做格式转写（中间文件），并把源 imgs/ 物理同步到 work/imgs/；
#       不改变任何文字内容；Stage 3+ 脚本完全复用，零漂移。

import os, re, sys, json, argparse, shutil
from collections import Counter

PAGE_RE = re.compile(r"^##\s*PAGE\s+phys=(\d+)\s*$", re.M)
BLOCK_RE = re.compile(r"^-\s*\*\*\[([a-zA-Z_]+)\]\*\*\s*(.*)$")


def sync_imgs(src_bookocr_dir, work_dir):
    """把 book_ocr 三件套目录里的 imgs/ 同步到 work/imgs/。

    Tier1 复用 book_ocr 裁图、跳过 §6.4 的 07_ 探测的前提：03 里的
    `![](imgs/p{phys}_{label}_{n}.jpg)` 引用必须能在 work/imgs/ 命中实体文件。
    book_ocr 图命名 p{phys}_{label}_{n}.jpg 与旧 Stage1 探针的 fig_* 不同，不冲突，可并存。
    已存在的目标文件不覆盖（避免冲掉旧流程已渲染的图）。
    """
    src = os.path.join(src_bookocr_dir, "imgs")
    if not os.path.isdir(src):
        print("[!] 源目录无 imgs/，跳过图同步（若需复用裁图请手动拷入 work/imgs/）")
        return 0
    dst = os.path.join(work_dir, "imgs")
    os.makedirs(dst, exist_ok=True)
    n = 0
    for name in sorted(os.listdir(src)):
        s = os.path.join(src, name)
        if not os.path.isfile(s):
            continue
        t = os.path.join(dst, name)
        if os.path.exists(t):
            continue
        shutil.copy2(s, t)
        n += 1
    print(f"[✔] imgs/ 已同步到 work/imgs/  (+{n} 张；已存在者跳过)")
    return n


def compute_book(phys, offset, pagemap):
    """人工覆盖模式：--offset 基准 + --pagemap 分段（key=段起始物理页，value=该段 OFFSET）。"""
    off = offset
    if pagemap:
        for s in sorted(int(k) for k in pagemap.keys()):
            if phys >= s:
                off = int(pagemap[str(s)])
            else:
                break
    return phys - off


def split_pages(text):
    """返回 [(phys, body)]，body 不含原锚行。"""
    out = []
    for m in PAGE_RE.finditer(text):
        phys = int(m.group(1))
        start = m.end()
        nxt = PAGE_RE.search(text, start)
        end = nxt.start() if nxt else len(text)
        body = text[start:end].strip("\n")
        out.append((phys, body))
    return out


def parse_number_anchors(sidecar_text):
    """从版式区块附档解析 (phys, book_int) 干净锚点，及无法解析的 (phys, raw)。"""
    anchors, garbled = [], []
    cur = None
    if not sidecar_text:
        return anchors, garbled
    for line in sidecar_text.splitlines():
        pm = re.match(r"^##\s*PAGE\s+phys=(\d+)\s*$", line.strip())
        if pm:
            cur = int(pm.group(1))
            continue
        bm = BLOCK_RE.match(line.strip())
        if bm and cur is not None and bm.group(1) == "number":
            val = bm.group(2).strip().replace(" ", "")
            m = re.fullmatch(r"(\d+)(?:\.0+)?", val)
            if m:
                anchors.append((cur, int(m.group(1))))
            else:
                garbled.append((cur, bm.group(2).strip()))
    return anchors, garbled


def derive_book_map(phys_list, anchors, garbled):
    """自动推导 phys→book。返回 (book_map:dict, log:dict)。

    策略：
      1) 无干净 number 锚点 → book=phys（未校准，告警）。
      2) 众数偏移覆盖 ≥40% → 全局偏移法 book = phys - 众数偏移；
         其余锚点若与赋值偏差 >2 记为噪声（插图/脚注号）。
      3) 否则（锚点散布，如大量删页/双套页码）→ 锚点插值法，相邻锚点间线性补值，
         并标注断档（phys步长 ≠ book步长）。
      4) book<1 的 phys 自动标记为 front_matter（封面/版权/前言等前页）。
    """
    log = {"method": None, "mode_offset": None, "coverage": None,
           "front_matter": None, "noise_anchors": [], "gaps": [],
           "garbled": garbled, "warning": None}
    book_map = {}
    phys_list = sorted(phys_list)

    if not anchors:
        log["method"] = "none"
        log["warning"] = "版式区块无干净 number 块，book=phys（未校准）；如需印页映射请另给 --offset/--pagemap"
        for p in phys_list:
            book_map[p] = p
        return book_map, log

    offsets = Counter(p - b for p, b in anchors)
    mode_off, mode_cnt = offsets.most_common(1)[0]
    coverage = mode_cnt / len(anchors)
    log["mode_offset"] = mode_off
    log["coverage"] = round(coverage, 3)

    if coverage >= 0.4:
        log["method"] = "global_offset"
        for p in phys_list:
            book_map[p] = p - mode_off
        for p, b in anchors:
            if abs((p - mode_off) - b) > 2:
                log["noise_anchors"].append({"phys": p, "recorded": b, "assigned": p - mode_off})
    else:
        log["method"] = "anchor_interp"
        seen = {}
        for p, b in sorted(anchors):
            seen.setdefault(p, b)
        a = sorted(seen.items())
        for p in phys_list:
            if p < a[0][0]:
                book_map[p] = p - mode_off          # 前页：沿用众数偏移（通常 book<1）
            elif p > a[-1][0]:
                book_map[p] = a[-1][1] + (p - a[-1][0])
            else:
                for i in range(1, len(a)):
                    if a[i][0] >= p:
                        p0, b0 = a[i - 1]
                        p1, b1 = a[i]
                        book_map[p] = b0 if p1 == p0 else b0 + round((b1 - b0) * (p - p0) / (p1 - p0))
                        if (b1 - b0) != (p1 - p0):
                            log["gaps"].append({"phys": f"{p0}->{p1}", "book": f"{b0}->{b1}"})
                        break

    fm = [p for p in phys_list if book_map[p] < 1]
    if fm:
        log["front_matter"] = [fm[0], fm[-1]]
    return book_map, log


def convert_md_to_struct(text, book_resolver):
    pages = split_pages(text)
    blocks = []
    for phys, body in pages:
        book = book_resolver(phys)
        blocks.append(f"<<<PAGE phys={phys} book={book}>>>\n\n{body}\n")
    return "\n".join(blocks), len(pages)


def convert_md_to_raw(text):
    pages = split_pages(text)
    blocks = [f"<<<PAGE phys={phys}>>>\n\n{body}\n" for phys, body in pages]
    return "\n".join(blocks)


def split_sidecar(text):
    """返回 {label: [(phys, line)]}。"""
    by_label = {}
    cur_phys = None
    for line in text.splitlines():
        pm = re.match(r"^##\s*PAGE\s+phys=(\d+)\s*$", line.strip())
        if pm:
            cur_phys = int(pm.group(1))
            continue
        bm = BLOCK_RE.match(line.strip())
        if bm and cur_phys is not None:
            label, content = bm.group(1), bm.group(2).strip()
            by_label.setdefault(label, []).append((cur_phys, content))
    return by_label


LABEL_FILE = {
    "header": "header_strip_log.md",
    "footer": "footer_strip_log.md",
}


def format_book_log(log):
    lines = ["# 印页推导日志（book=M 自动推导）", ""]
    lines.append(f"- 方法：`{log['method']}`")
    if log["mode_offset"] is not None:
        lines.append(f"- 众数偏移：{log['mode_offset']}（覆盖 {log['coverage']}）")
    if log["front_matter"]:
        lines.append(f"- 前页（book<1，封面/版权/前言等）：phys {log['front_matter'][0]}–{log['front_matter'][1]}，印页未校准，建议人工核对（或默认罗马数字）")
    if log["noise_anchors"]:
        lines.append(f"- 疑似噪声锚点（插图/脚注号，已忽略，按众数偏移赋值）：{len(log['noise_anchors'])} 处")
        for r in log["noise_anchors"][:20]:
            lines.append(f"    - phys {r['phys']}：记录值 {r['recorded']} → 赋 book={r['assigned']}")
    if log["gaps"]:
        lines.append(f"- 断档（phys步长≠book步长，疑似删页/双套页码）：{len(log['gaps'])} 处")
        for g in log["gaps"][:20]:
            lines.append(f"    - phys {g['phys']}  book {g['book']}")
    if log["garbled"]:
        lines.append(f"- OCR 无法解析的 number 值：{len(log['garbled'])} 处（如 {log['garbled'][:5]}）")
    if log["warning"]:
        lines.append(f"- ⚠️ {log['warning']}")
    if log.get("table_count"):
        lines.append(f"- 表格原图校验：{log['table_count']} 个 HTML 表")
        if log.get("table_image_missing"):
            lines.append(f"  - ⚠️ 以下 phys 有HTML表格但无 p{{phys}}_table_* 对照原图（建议从源书补渲）：{log['table_image_missing']}")
        else:
            lines.append("  - 全部表格均有对照原图 ✔")
    lines.append("")
    lines.append("> 本日志仅作抽检参照；如自动结果明显异常，可用 `--offset N` 或 `--pagemap` 覆盖后重跑。")
    return "\n".join(lines)

def audit_table_images(struct_text, imgs_dir):
    """扫描 struct 中所有 <table> 块，检查 work/imgs 是否有对应 p{phys}_table_* 原图。
    返回 (table_count, missing_phys_list)。缺失则在 book_map_log.md 告警。
    """
    table_blocks = list(re.finditer(r"<table\b[^>]*>.*?</table>", struct_text, re.S | re.I))
    if not table_blocks:
        return 0, []
    missing = []
    checked = set()
    for m in table_blocks:
        preceding = struct_text[:m.start()]
        phys_matches = list(re.finditer(r"<<<PAGE\s+phys=(\d+)", preceding))
        if not phys_matches:
            continue
        phys = int(phys_matches[-1].group(1))
        if phys in checked:
            continue
        checked.add(phys)
        if not os.path.isdir(imgs_dir):
            missing.append(phys)
            continue
        candidates = [fn for fn in os.listdir(imgs_dir)
                      if fn.startswith(f"p{phys}_table_")]
        if not candidates:
            missing.append(phys)
    return len(table_blocks), sorted(missing)


def main():
    ap = argparse.ArgumentParser(description="book_ocr 三件套 → ebook 中间文件（跳过 Stage 1+2）")
    ap.add_argument("--bookocr-dir", required=True, help="book_ocr 输出目录（含 <stem>.md / _raw.md / _版式区块.md）")
    ap.add_argument("--work", required=True, help="ebook 工作目录 .work/books/<书名>")
    ap.add_argument("--name", default="", help="书名 stem；缺省取目录下首个 .md 去 _raw/_版式区块 后缀")
    ap.add_argument("--offset", type=int, default=None, help="【可选覆盖】印页偏移（book = phys - offset）；不填则自动推导")
    ap.add_argument("--pagemap", default="", help='【可选覆盖】分段映射 JSON，如 \'{"6":5,"31":30}\'；不填则自动推导')
    args = ap.parse_args()

    d = args.bookocr_dir
    if not os.path.isdir(d):
        sys.exit(f"[错误] --bookocr-dir 不存在：{d}")

    # 推断 stem
    stem = args.name
    if not stem:
        mds = [f for f in os.listdir(d) if f.endswith(".md") and not f.endswith(("_raw.md", "_版式区块.md"))]
        if not mds:
            sys.exit("[错误] 未找到 <stem>.md，请用 --name 指定")
        stem = mds[0][:-3]

    md_path = os.path.join(d, stem + ".md")
    raw_path = os.path.join(d, stem + "_raw.md")
    side_path = os.path.join(d, stem + "_版式区块.md")
    if not os.path.isfile(md_path):
        sys.exit(f"[错误] 缺少主 md：{md_path}")

    os.makedirs(args.work, exist_ok=True)
    sync_imgs(d, args.work)
    pagemap = json.loads(args.pagemap) if args.pagemap else None

    # 印页推导：优先自动；用户显式给 --offset/--pagemap 则覆盖
    md_text = open(md_path, encoding="utf-8").read()
    phys_list = [p for p, _ in split_pages(md_text)]
    manual = args.offset is not None or pagemap is not None
    if manual:
        off = args.offset if args.offset is not None else 0
        book_resolver = lambda phys: compute_book(phys, off, pagemap)
        print(f"[i] 使用人工覆盖：offset={off} pagemap={pagemap}")
        log = {"method": "manual", "mode_offset": off, "coverage": None,
               "front_matter": None, "noise_anchors": [], "gaps": [], "garbled": [], "warning": None}
    else:
        anchors, garbled = parse_number_anchors(
            open(side_path, encoding="utf-8").read() if os.path.isfile(side_path) else "")
        book_map, log = derive_book_map(phys_list, anchors, garbled)
        book_resolver = lambda phys: book_map.get(phys, phys)
        print(f"[i] 自动推导 book=M：方法={log['method']} 众数偏移={log['mode_offset']} 覆盖={log['coverage']}"
              + (f" 前页 phys {log['front_matter']}" if log['front_matter'] else ""))

    # ① struct（必产）
    struct_txt, n_pages = convert_md_to_struct(md_text, book_resolver)
    open(os.path.join(args.work, "fulltext_struct.txt"), "w", encoding="utf-8", newline="\n").write(struct_txt + "\n")
    print(f"[✔] fulltext_struct.txt  ({n_pages} 页)")

    # ② raw（可选）
    if os.path.isfile(raw_path):
        raw_text = open(raw_path, encoding="utf-8").read()
        open(os.path.join(args.work, "fulltext_raw.txt"), "w", encoding="utf-8", newline="\n").write(convert_md_to_raw(raw_text) + "\n")
        print(f"[✔] fulltext_raw.txt  (法医底本)")
    else:
        print(f"[!] 未找到 {stem}_raw.md，跳过 fulltext_raw.txt（Stage 3 审计将无对照源）")

    # ③ 附档按 label 拆分
    if os.path.isfile(side_path):
        by_label = split_sidecar(open(side_path, encoding="utf-8").read())
        for label, items in by_label.items():
            fname = LABEL_FILE.get(label, "other_strip_log.md")
            fpath = os.path.join(args.work, fname)
            head = "" if os.path.exists(fpath) else f"# 剥离区块日志 · {label}\n\n"
            with open(fpath, "a", encoding="utf-8", newline="\n") as f:
                f.write(head)
                for phys, content in items:
                    f.write(f"## PAGE phys={phys}\n\n- **[{label}]** {content}\n\n")
            print(f"[✔] {fname}  (+{len(items)} 条 {label})")
    else:
        print(f"[!] 未找到 {stem}_版式区块.md，跳过剥离日志")

    # ④ page_index.json
    pages_info = [(phys, book_resolver(phys)) for phys in phys_list]
    idx = {"phys_count": len(pages_info),
           "pages": [{"phys": p, "book": b} for p, b in pages_info]}
    open(os.path.join(args.work, "page_index.json"), "w", encoding="utf-8", newline="\n").write(json.dumps(idx, ensure_ascii=False, indent=2) + "\n")
    print(f"[✔] page_index.json  ({len(pages_info)} 页)")

    # ④.5 表格原图校验（有HTML表但无 p{phys}_table_* 原图时告警）
    imgs_dir = os.path.join(args.work, "imgs")
    n_tables, missing_tables = audit_table_images(struct_txt, imgs_dir)
    log["table_count"] = n_tables
    log["table_image_missing"] = missing_tables
    if n_tables > 0:
        print(f"[i] 表格原图校验：{n_tables} 个 HTML 表，缺失对照原图 {len(missing_tables)} 个 phys")
        if missing_tables:
            print(f"    ⚠️ 缺失 phys: {missing_tables}（建议从源书补渲 table 原图到 imgs/）")
    # 独立告警清单 table_image_missing.md（phys 列表 + 说明；幂等可重跑）
    miss_path = os.path.join(args.work, "table_image_missing.md")
    if missing_tables:
        miss_lines = ["# table 原图缺失告警", "",
            f"> fulltext_struct.txt 中有 **{n_tables}** 个 HTML 表块，"
            f"其中 **{len(missing_tables)}** 个 phys 在 imgs/ 中找不到 `p{{phys}}_table*` 对照原图：", ""]
        for phys in missing_tables:
            miss_lines.append(f"- phys={phys}")
        miss_lines += ["", "> 这些表已转 HTML 但无裁图对照；Stage 3 表格核对时按 §3.7 决策树"
                       "（重建 HTML / 整图嵌入），并按需从源书补渲原图。"]
        open(miss_path, "w", encoding="utf-8", newline="\n").write("\n".join(miss_lines) + "\n")
        print(f"[!] table 原图缺失清单 → {miss_path}")
    else:
        open(miss_path, "w", encoding="utf-8", newline="\n").write(
            "# table 原图缺失告警\n\n"
            f"> 共 {n_tables} 个 HTML 表块；所有含表 phys 均有 imgs/p{{phys}}_table* 对照原图，无缺失。\n")

    # ⑤ 印页推导日志
    open(os.path.join(args.work, "book_map_log.md"), "w", encoding="utf-8", newline="\n").write(format_book_log(log) + "\n")
    print(f"[✔] book_map_log.md  (推导过程与异常标注)")

    print(f"\n完成。下一步：在 {args.work} 上直接跑 Stage 3+（corrections / pipeline_assemble.py），无需 Stage 1+2。")


if __name__ == "__main__":
    main()
