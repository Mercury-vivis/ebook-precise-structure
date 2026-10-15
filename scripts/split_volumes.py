#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
split_volumes.py —— 多卷合订本 → 按卷拆成 per-volume 工作子目录（混合模型输入切片）

【触发】
    <WORK>/fulltext_struct.txt 的物理页(phys)总数 > --threshold（默认 3000）即判定为多卷合订本，需拆分。
    已分卷各自成册的 PDF 不进此流程（直接按单本跑），见 SKILL.md §多卷合订本规范。

【边界检测（优先级，高置信优先）】
    A. 显式 spec：--volumes spec.json  或  book_meta.json 的 "volumes" 字段
       spec 条目：{"no":1,"title":"...","phys_start":2,"phys_end":812}
    B. book= 印页重置（自动）：扫描 <<<PAGE phys=N book=M>>>，当 book 由大值跌回低值（≤100，典型为新卷起印页=1）
       即判定为新卷起点。适用于"各卷印页独立、phys 连续"的合订本（不命中连续编码 book==phys 的书）。
    C. 语义识别（连续编码回退）：扫描正文卷/册标题标记。内置默认模式（Volume/Book/Part + 中文 卷/册），
       可用 --title-pattern 追加书专属正则（捕获组1 = 卷序号，可为 \\d+ 或 [IVXLC]+）。
       ⚠ 语义检测易误命中（如 TOC 引用其他卷），故 --apply 时必须再带 --confirm 才落盘。

【产出（非破坏：原合并件始终保留）】
    <WORK>/volumes/V{NN}_{slug}/fulltext_struct.txt        （绝对 phys 保留，不重基）
    <WORK>/volumes/V{NN}_{slug}/fulltext_raw.txt           （若存在）
    <WORK>/volumes/V{NN}_{slug}/fulltext_clean.txt         （若存在）
    <WORK>/volumes/V{NN}_{slug}/book_meta.json             （卷级元数据 + 指回父本）
    <WORK>/volumes/_manifest.json                          （整套级索引，供 00/05 合成）

【默认 --dry-run：只打印拆分方案不落盘；--apply 才真实切片。】
    语义检测路径额外要求 --confirm（避免误拆）。

用法示例：
    # 先预览（自动判定）
    python split_volumes.py <WORK>
    # 书专属标题语义拆分（Indian 案例，需 --confirm）
    python split_volumes.py <WORK> --title-pattern "History and Culture of the Indian People, Volume (\\d+)" --apply --confirm
    # 显式 spec 拆分
    python split_volumes.py <WORK> --volumes vol_spec.json --apply
"""

import re, os, json, argparse, sys
from _common import read_text_cn, read_json_cn, safe_write  # 读取/写入单一入口（utf-8-sig 容错 + N3 原子写）

# ---- 锚正则 ----
ANCHOR_BLOCK_RE = re.compile(
    r"<<<PAGE phys=(\d+) book=([^>]*)>>>\r?\n(.*?)(?=\r?\n<<<PAGE|\Z)", re.S)
ANCHOR_PHYS_RE = re.compile(r"<<<PAGE phys=(\d+)")  # 统一匹配 struct/raw/clean（有无 book 皆可）

# ---- 默认语义标题模式（捕获组1 = 卷序号）----
DEFAULT_TITLE_PATTERNS = [
    r"Volume\s+(\d+|[IVXLC]+)",
    r"VOLUME\s+(\d+|[IVXLC]+)",
    r"Book\s+(\d+|[IVXLC]+)",
    r"BOOK\s+(\d+|[IVXLC]+)",
    r"Part\s+(\d+|[IVXLC]+)",
    r"PART\s+(\d+|[IVXLC]+)",
    r"第[一二三四五六七八九十百零\d]+卷",
    r"第[一二三四五六七八九十百零\d]+册",
]

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}

def roman_to_int(s):
    """简易罗马数字解析（IVXLC，够用）。非合法串返回 None。"""
    s = s.upper()
    if not s or any(c not in _ROMAN for c in s):
        return None
    total, prev = 0, 0
    for c in reversed(s):
        v = _ROMAN[c]
        total += -v if v < prev else v
        prev = v
    return total

def parse_ordinal(s):
    """把捕获到的卷序号归一为整数；数字直接转，罗马数字转，中文数字转。"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    r = roman_to_int(s)
    if r is not None:
        return r
    cn = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100}
    if all(ch in cn for ch in s):
        # 支持 十一/二十 这类简单组合
        val = 0
        section = 0
        for ch in s:
            if ch in "零一二三四五六七八九":
                section = cn[ch]
            elif ch == "十":
                val += (section or 1) * 10
                section = 0
            elif ch == "百":
                val += (section or 1) * 100
                section = 0
        return val + section
    return None

def slug(s):
    return re.sub(r"[\\/:*?\"<>|]", "_", s)[:40].strip()

def parse_blocks(struct):
    """返回 [(phys:int, book:str, body:str)] 按 phys 升序。"""
    return [(int(m.group(1)), m.group(2), m.group(3))
            for m in ANCHOR_BLOCK_RE.finditer(struct)]

def count_phys(struct):
    return len(ANCHOR_PHYS_RE.findall(struct))

def detect_book_reset(blocks):
    """印页重置检测：book 由大值跌回低值（≤100，典型为新卷起印页=1）即判为新卷起点。

    连续编码合订本（book==phys 单调增）不会误触发；仅真正『各卷印页独立、每卷从低页重印』的合订本命中。"""
    bounds, prev_book = [], None
    for phys, book, _ in blocks:
        b = int(book) if book.isdigit() else None
        if b is not None and prev_book is not None and b < prev_book and b <= 100:
            bounds.append(phys)
        if b is not None:
            prev_book = b
    return bounds

def detect_semantic(struct, blocks, extra_patterns):
    """扫描每页 body 的卷标题，返回按 phys 升序的 [(phys, ordinal_int, title_text)]。"""
    patterns = [re.compile(p) for p in (DEFAULT_TITLE_PATTERNS + list(extra_patterns))]
    hits = []
    for phys, _book, body in blocks:
        for pat in patterns:
            m = pat.search(body)
            if m:
                ordinal = parse_ordinal(m.group(1))
                # 取命中行文本作标题（截断）
                line = body[max(0, m.start() - 0): m.end() + 40].splitlines()
                title = (line[0] if line else m.group(0))[:50]
                hits.append((phys, ordinal if ordinal is not None else 0, title))
                break  # 一页只取首个命中
    # 去重：相邻命中 < 20 phys 且为同一卷序号 → 视为同卷（保留首个）；不同序号即使近也保留
    hits.sort(key=lambda x: x[0])
    dedup, last_phys, last_ord = [], None, None
    for phys, ordn, title in hits:
        if last_phys is None or phys - last_phys >= 20 or ordn != last_ord:
            dedup.append((phys, ordn, title))
            last_phys, last_ord = phys, ordn
    return dedup

def build_volumes(detection, max_phys):
    """由检测产出 [(start, end), ...] 与每卷标题。

    关键：卷1 恒从 phys=1 起（含其前页/扉页）；其余卷的起点 = 检测到的『卷间切点』。
      - spec：直接用 phys_start/phys_end。
      - book_reset：检测到的 book 重置 phys 即卷2+ 起点。
      - semantic：标题 phys 中 ordinal>=2 的才是卷间切点（ordinal==1 是卷1标题，非边界）。
    """
    method, payload, _t = detection
    if method == "spec":
        ranges = [(s, e) for (s, e, _tt) in payload]
        titles = [t for (_s, _e, t) in payload]
        return ranges, titles
    if method == "book_reset":
        boundaries = payload  # 卷2+ 起点
        all_starts = [1] + boundaries
        titles = ["卷1"] + [f"卷{i+2}" for i in range(len(boundaries))]
    else:  # semantic：payload = [(phys, ordinal, title), ...]
        sem = payload
        interior = [(p, t) for (p, o, t) in sem if o and o >= 2]
        if not interior:  # 序号未能解析时退化为全部标题 phys 作切点
            interior = [(p, t) for (p, _o, t) in sem]
        boundaries = [p for (p, _t) in interior]
        all_starts = [1] + boundaries
        v1_title = next((t for (p, o, t) in sem if o == 1), "卷1")
        titles = [v1_title] + [t for (_p, t) in interior]
    ranges = []
    for i, s in enumerate(all_starts):
        e = all_starts[i + 1] - 1 if i + 1 < len(all_starts) else max_phys
        ranges.append((s, e))
    return ranges, titles

def slice_text(text, ranges):
    """按 phys 区间把文本切成 N 份（通用：struct/raw/clean 皆可）。返回 {key:(start,end):content}。"""
    anchors = list(ANCHOR_PHYS_RE.finditer(text))
    n = len(anchors)
    buckets = {r: [] for r in ranges}
    dropped = 0
    for i, m in enumerate(anchors):
        ph = int(m.group(1))
        start = m.start()
        end = anchors[i + 1].start() if i + 1 < n else len(text)
        placed = False
        for (a, b) in ranges:
            if a <= ph <= b:
                buckets[(a, b)].append(text[start:end])
                placed = True
                break
        if not placed:
            dropped += 1
    return {r: "".join(parts) for r, parts in buckets.items()}, dropped

def main():
    ap = argparse.ArgumentParser(description="多卷合订本按卷拆分为 per-volume 工作子目录")
    ap.add_argument("work", help="工作区目录（含 fulltext_struct.txt）")
    ap.add_argument("--threshold", type=int, default=3000, help="触发拆分的 phys 页阈值（默认 3000）")
    ap.add_argument("--volumes", help="显式卷 spec JSON：[{no,title,phys_start,phys_end}]")
    ap.add_argument("--title-pattern", action="append", default=[],
                   help="追加书专属卷标题正则（捕获组1=卷序号），可多次")
    ap.add_argument("--apply", action="store_true", help="真实切片落盘（默认仅 dry-run 预览）")
    ap.add_argument("--confirm", action="store_true", help="语义检测路径下确认无误才允许 --apply")
    ap.add_argument("--force", action="store_true", help="volumes/ 已存在时覆盖")
    ap.add_argument("--update-meta", action="store_true", help="把 volumes 索引写回父 book_meta.json")
    ap.add_argument("--list-default-patterns", action="store_true", help="打印默认语义标题正则后退出")
    args = ap.parse_args()

    if args.list_default_patterns:
        for p in DEFAULT_TITLE_PATTERNS:
            print(p)
        return

    work = args.work
    struct_path = os.path.join(work, "fulltext_struct.txt")
    if not os.path.isfile(struct_path):
        print(f"❌ 找不到 {struct_path}", file=sys.stderr)
        sys.exit(2)
    struct = read_text_cn(struct_path)
    total = count_phys(struct)
    print(f"[split_volumes] 工作区 {work}")
    print(f"[split_volumes] fulltext_struct.txt 物理页(phys)总数 = {total}（阈值 {args.threshold}）")

    if total <= args.threshold:
        print(f"✅ 未超阈值，判定为单本/小合订本，无需拆分。")
        return

    blocks = parse_blocks(struct)
    max_phys = blocks[-1][0] if blocks else 0
    meta_path = os.path.join(work, "book_meta.json")
    meta = read_json_cn(meta_path) if os.path.isfile(meta_path) else {}

    # ---- 边界检测 ----
    detection = None  # (method, payload, _)
    if args.volumes and os.path.isfile(args.volumes):
        spec = read_json_cn(args.volumes)
        starts = [(int(s["phys_start"]), int(s["phys_end"]), s.get("title", f"卷{s['no']}")) for s in spec]
        detection = ("spec", starts, None)
    elif meta.get("volumes"):
        spec = meta["volumes"]
        starts = [(int(s["phys_start"]), int(s["phys_end"]), s.get("title", f"卷{s['no']}")) for s in spec]
        detection = ("spec", starts, None)
    else:
        reset_bounds = detect_book_reset(blocks)
        if len(reset_bounds) >= 1:
            detection = ("book_reset", reset_bounds, None)
        else:
            sem = detect_semantic(struct, blocks, args.title_pattern)
            if len(sem) >= 2:
                detection = ("semantic", sem, None)
            else:
                print("❌ 无法自动判定卷边界：book= 无重置且语义识别命中不足。", file=sys.stderr)
                print("   请二选一：① --title-pattern '<书专属卷标题正则>'；② --volumes spec.json", file=sys.stderr)
                sys.exit(3)

    method, _pl, _ = detection
    ranges, titles_out = build_volumes(detection, max_phys)
    n_vol = len(ranges)
    n_vol = len(ranges)

    # ---- 打印方案 ----
    print(f"\n[检测方式] {method} ｜ 拟拆分为 {n_vol} 卷")
    print(f"{'卷':<4}{'标题':<46}{'phys 起':>10}{'phys 止':>10}{'页数':>8}")
    for i, ((a, b), t) in enumerate(zip(ranges, titles_out), 1):
        print(f"{i:<4}{t:<46}{a:>10}{b:>10}{b-a+1:>8}")

    semantic_needs_confirm = (method == "semantic" and not args.confirm)
    if not args.apply:
        print("\n🔍 仅预览（dry-run）。加 --apply 落盘；语义检测还需 --confirm。")
        return
    if semantic_needs_confirm:
        print("\n⚠ 语义检测结果未经 --confirm 确认，拒绝落盘。请核对上方边界无误后加 --confirm。", file=sys.stderr)
        sys.exit(4)

    # ---- 落盘 ----
    out_root = os.path.join(work, "volumes")
    if os.path.isdir(out_root) and not args.force:
        print(f"\n❌ {out_root} 已存在，请用 --force 覆盖或先移除。", file=sys.stderr)
        sys.exit(5)
    os.makedirs(out_root, exist_ok=True)

    files_to_slice = ["fulltext_struct.txt", "fulltext_raw.txt", "fulltext_clean.txt"]
    parent_book = meta.get("book", os.path.basename(os.path.normpath(work)))
    manifest_vols = []
    total_dropped = 0

    # 先建各卷目录
    vdirs = []
    for i, t in enumerate(titles_out, 1):
        vd = os.path.join(out_root, f"V{i:02d}_{slug(t)}")
        os.makedirs(vd, exist_ok=True)
        vdirs.append(vd)

    # 每个源文件一次性切完所有卷（dropped = 真正未落入任何卷的 phys，如卷前的系列封面页）
    for fn in files_to_slice:
        src = os.path.join(work, fn)
        if not os.path.isfile(src):
            continue
        text = read_text_cn(src)
        sliced, dropped = slice_text(text, ranges)
        total_dropped += dropped
        for idx, (a, b) in enumerate(ranges):
            safe_write(os.path.join(vdirs[idx], fn), sliced[(a, b)])

    # 卷级元数据 + 清单
    for idx, ((a, b), t) in enumerate(zip(ranges, titles_out)):
        i = idx + 1
        vdir = vdirs[idx]
        vmeta = {
            "title": f"{parent_book} · 第{i}卷 {t}",
            "vol_no": i,
            "vol_title": t,
            "parent_book": parent_book,
            "parent_work": os.path.abspath(work),
            "phys_start": a,
            "phys_end": b,
            "is_volume_of_multivolume": True,
        }
        safe_write(os.path.join(vdir, "book_meta.json"),
                   json.dumps(vmeta, ensure_ascii=False, indent=2))
        manifest_vols.append({
            "no": i, "title": t, "phys_start": a, "phys_end": b,
            "num_pages": b - a + 1, "dir": os.path.relpath(vdir, out_root),
        })
        print(f"  ✅ V{i:02d} {t}: phys {a}-{b} → {os.path.relpath(vdir, work)}")

    manifest = {
        "parent_book": parent_book,
        "parent_work": os.path.abspath(work),
        "threshold": args.threshold,
        "detection_method": method,
        "total_phys": total,
        "num_volumes": n_vol,
        "volumes": manifest_vols,
    }
    safe_write(os.path.join(out_root, "_manifest.json"),
               json.dumps(manifest, ensure_ascii=False, indent=2))

    if args.update_meta and os.path.isfile(meta_path):
        meta["volumes"] = [{"no": v["no"], "title": v["title"],
                            "phys_start": v["phys_start"], "phys_end": v["phys_end"]}
                           for v in manifest_vols]
        safe_write(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"  📝 已把 volumes 索引写回 {meta_path}")

    print(f"\n✅ 拆分完成：{n_vol} 卷 → {out_root}")
    if total_dropped:
        print(f"⚠ 有 {total_dropped} 个物理页未落入任何卷区间（通常是卷前的系列封面/空白页，如物理页 phys=1）", file=sys.stderr)

if __name__ == "__main__":
    main()
