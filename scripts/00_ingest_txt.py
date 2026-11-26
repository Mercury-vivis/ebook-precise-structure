#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""00_ingest_txt.py — 纯文本（GB18030 / UTF-8）接入 SEC 锚体系。

背景：ebook-precise-structure 原有三条输入路径（OCR-PDF / EPUB / PaddleOCR 三件套）
均不接纯 TXT。本脚本按用户裁定 ①a 新增，把无印刷页的纯文本教材转成与 EPUB 路径
同构的加工层产物，供 01c_add_headings / assemble_epub / check_book 直接消费。

层级与锚（用户裁定 ②a）：章 → ##，节 → ###，「一、」→ ####，「（一）」→ #####。
锚打在章 / 节 / 一、三级标题行（每处一个 SEC 单元）；「（一）」不单独打锚，
只在 fulltext_struct.txt 就地加 ##### 前缀。每章扉页重复标题保留在卷首单元、
不作章节锚；第二处出现者为正章标题。

产物（落 <work>/）：
  fulltext_raw.txt   逐字法医底本（仅加 SEC 锚；行尾 CRLF→LF 属格式转写）
  fulltext_struct.txt PAGE 锚 = SEC idx，四级标题加 ##### 前缀
  page_index.json / toc_tree.json / chapters/NN_章名.txt / book_meta.json

用法：
  python 00_ingest_txt.py <input.txt> <work> --encoding gb18030 \
      --book "军事理论教程" --author "张政文、陆华 主编" \
      --publisher "南京大学出版社" --year "2020（2026年8月第26次印刷）"
"""
import argparse
import glob
import json
import os
import re
import sys

from _interp_guard import ensure_interpreter  # noqa: E402
from _work_guard import ensure_work_dir  # noqa: E402

ensure_interpreter()

CN = "一二三四五六七八九十百〇零"
CH_RE = re.compile(r"^第([" + CN + r"]+)章\s*(.*)$")
SEC_RE = re.compile(r"^第([" + CN + r"]+)节\s*(.*)$")
L3_RE = re.compile(r"^([" + CN + r"]+)、\s*(.*)$")
L4_RE = re.compile(r"^（([" + CN + r"]+)）\s*(.*)$")
REF_RE = re.compile(r"^(参考文献|参\s*考\s*文\s*献|图书在版编目（CIP）数据)")
ANCHOR_UNSAFE = re.compile(r"[\r\n>]+")


def safe_title(t):
    t = re.sub(r"\s+", " ", (t or "").strip())
    return ANCHOR_UNSAFE.sub(lambda m: "＞" if ">" in m.group(0) else " ", t)[:80] or "无题"


def read_source(path, enc):
    raw = open(path, "rb").read()
    try:
        txt = raw.decode(enc)
    except UnicodeDecodeError as e:
        sys.exit(f"❌ 解码失败：{enc} 无法解码 {path}（{e}）。请显式指定 --encoding。")
    # 行尾归一：CRLF / CR → LF。属格式转写，不改任何字符字节。
    return re.sub(r"\r+\n", "\n", txt).replace("\r", "\n")


def detect_units(lines):
    """返回 (units, stats)。unit = {title, level, lines: []}。"""
    units = []
    cur = {"title": "卷首（书名·章扉页）", "level": 2, "lines": [], "kind": "front"}
    chapter_seen = {}
    stats = {"ch": 0, "sec": 0, "l3": 0, "l4": 0, "tail": 0}

    def flush():
        nonlocal cur
        units.append(cur)

    for ln in lines:
        s = ln.strip()
        m = CH_RE.match(s)
        if m:
            key = m.group(1)
            cnt = chapter_seen.get(key, 0) + 1
            chapter_seen[key] = cnt
            if cnt == 1:
                # 章扉页：留在卷首单元，不新建锚（章标题重复第一次）
                cur["lines"].append(ln)
                continue
            flush()
            title = f"第{key}章{m.group(2)}".strip()
            cur = {"title": title, "level": 2, "lines": [ln], "kind": "ch"}
            stats["ch"] += 1
            continue
        m = SEC_RE.match(s)
        if m:
            flush()
            title = f"第{m.group(1)}节{m.group(2)}".strip()
            cur = {"title": title, "level": 3, "lines": [ln], "kind": "sec"}
            stats["sec"] += 1
            continue
        m = L3_RE.match(s)
        if m:
            flush()
            cur = {"title": s, "level": 4, "lines": [ln], "kind": "l3"}
            stats["l3"] += 1
            continue
        if REF_RE.match(s) and cur["kind"] != "tail":
            flush()
            cur = {"title": "参考文献与出版信息", "level": 2, "lines": [ln], "kind": "tail"}
            stats["tail"] += 1
            continue
        m = L4_RE.match(s)
        if m:
            stats["l4"] += 1
            cur["lines"].append("##### " + ln.lstrip())  # 四级标题只加前缀，不打锚
            continue
        cur["lines"].append(ln)
    flush()
    # 丢弃纯空尾单元（文件末尾换行造成的空块）
    while units and units[-1]["kind"] != "front" and not any(x.strip() for x in units[-1]["lines"]):
        units.pop()
    return units, stats


def main():
    ap = argparse.ArgumentParser(description="纯 TXT → SEC 锚体系加工层产物")
    ap.add_argument("input")
    ap.add_argument("work")
    ap.add_argument("--encoding", default="gb18030")
    ap.add_argument("--book", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--publisher", default="")
    ap.add_argument("--year", default="")
    ap.add_argument("--type", dest="btype", default="② 思想/理论型（高校教材，按概念与体系组织）")
    ap.add_argument("--source", default="")
    ap.add_argument("--overview", default="")
    ap.add_argument("--pagemap-desc",
                    default="SEC idx 为三级标题单元序（含章/节/一、），底本无印刷页码，非页码引用")
    args = ap.parse_args()

    work = ensure_work_dir(os.path.abspath(args.work), "00_ingest_txt")
    lines = read_source(os.path.abspath(args.input), args.encoding).split("\n")
    units, stats = detect_units(lines)

    # ---- 产物 1：法医底本（逐字原样 + SEC 锚） ----
    raw_parts = []
    for i, u in enumerate(units, 1):
        raw_parts.append(f"<<<SEC idx={i:03d} title={safe_title(u['title'])}>>>\n" + "\n".join(u["lines"]))
    with open(os.path.join(work, "fulltext_raw.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n\n".join(raw_parts))

    # ---- 产物 2：struct（PAGE 锚 = SEC idx；四级标题已加前缀） ----
    struct_parts = []
    for i, u in enumerate(units, 1):
        struct_parts.append(
            f"<<<PAGE phys={i:03d} book={i:03d} title={safe_title(u['title'])}>>>\n" + "\n".join(u["lines"]))
    with open(os.path.join(work, "fulltext_struct.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n\n".join(struct_parts))

    # ---- 产物 3/4：索引与目录树 ----
    index = []
    for i, u in enumerate(units, 1):
        index.append({
            "idx": i,
            "href": f"sec{i:03d}",
            "title": u["title"],
            "path": [u["title"]] if u["kind"] in ("front", "tail") else [],
            "char_count": sum(len(x) for x in u["lines"]),
            "print_page": None,
        })
    meta_head = {
        "title": args.book or os.path.splitext(os.path.basename(args.input))[0],
        "creator": args.author,
        "publisher": args.publisher,
        "date": args.year,
        "format": "TXT(GB18030)→SEC",
    }
    for fn, obj in (("page_index.json", index),
                    ("toc_tree.json", {"meta": meta_head, "sections": index})):
        with open(os.path.join(work, fn), "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)

    # ---- 产物 5：章文件（按正章标题切；供派工与章内覆盖核对） ----
    ch_dir = os.path.join(work, "chapters")
    os.makedirs(ch_dir, exist_ok=True)
    # 幂等重跑：清掉本目录上一轮生成的章文件（仅限 *.txt，不碰其他文件）
    for stale in glob.glob(os.path.join(ch_dir, "*.txt")):
        os.remove(stale)
    chapters = []
    ch_units = [(i, u) for i, u in enumerate(units, 1) if u["kind"] == "ch"]
    for no, (start_i, ch_unit) in enumerate(ch_units, 1):
        end_i = ch_units[no][0] - 1 if no < len(ch_units) else len(units)
        name = ch_unit["title"]
        body = []
        for j in range(start_i, end_i + 1):
            u = units[j - 1]
            body.append(f"<<<PAGE phys={j:03d} book={j:03d} title={safe_title(u['title'])}>>>\n"
                        + "\n".join(u["lines"]))
        safe_name = re.sub(r'[\\/:*?"<>|\n]', "_", name)[:40]
        with open(os.path.join(ch_dir, f"{no:02d}_{safe_name}.txt"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write("\n\n".join(body))
        chapters.append({"no": no, "name": name, "phys_start": start_i, "level": 2})

    # ---- 产物 6：book_meta.json（schema 严格，不自造键） ----
    headings = []
    for i, u in enumerate(units, 1):
        if u["kind"] in ("ch", "sec", "l3"):
            headings.append({"text": u["title"], "level": u["level"], "phys": i})
        elif u["kind"] == "tail" and i == len(units):
            headings.append({"text": u["lines"][0].strip(), "level": u["level"], "phys": i})
    meta = {
        "book": meta_head["title"],
        "author": args.author,
        "publisher": args.publisher,
        "year": args.year,
        "type": args.btype,
        "source": args.source or f"唯一底本：{os.path.abspath(args.input)}（{args.encoding} 纯文本）",
        "pagemap_desc": args.pagemap_desc,
        "total_phys": len(units),
        "is_traditional": False,
        "overview": args.overview,
        "chapters": chapters,
        "headings": headings,
    }
    with open(os.path.join(work, "book_meta.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("✅ TXT 接入完成")
    print(f"   底本行数 {len(lines)} / 字符 {sum(len(x) for x in lines)}")
    print(f"   SEC 单元 {len(units)}（卷首 + 章 {stats['ch']} + 节 {stats['sec']} "
          f"+ 一、{stats['l3']} + 尾部 {stats['tail']}）")
    print(f"   （一）四级标题 {stats['l4']} 处（已在 struct 加 #####，未单独打锚）")
    print(f"   正章 {len(chapters)} 篇，phys_start = {[c['phys_start'] for c in chapters]}")
    print(f"   work = {work}")


if __name__ == "__main__":
    main()
