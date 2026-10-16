#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
02_ncx_split_epub.py —— EPUB 路径「源头级」NCX 节锚细化（ebook-precise-structure 技能 · 2026-09-20 新增）

为什么需要它（与旧流程的区别）：
  旧流程：`01b_extract_epub.py` 按 spine 文档抽取，一个 HTML 文档 = 一个 SEC 单元（常是「一章一锚」），
  锚密度过粗（实测 100–270 行/锚），失去「可回溯原文」精度。此前靠交付后跑 `epub_ncx_split.py`
  在 03 上补插节锚——这是「生成 03 后检索优化」的手动补丁，易漏、且补在 03 上无法惠及 struct/纠错粒度。
  本脚本把同一套 NCX 节锚逻辑**前移进源头**：在 `fulltext_struct.txt`（纠错前）就按 NCX 把 SEC 细化到节级，
  并**重新顺排 SEC idx（1..N 连续）**，同步重建 `page_index.json` / `toc_tree.json` / `book_meta.json`，
  使后续纠错补丁（corrections/notes 引用 SEC idx）、覆盖率审计、00/01/02 标签全部基于细粒度锚。
  新书在 EPUB 梳理阶段即自动产出细锚，不再需要交付后回补。

关键安全约束（来自 assemble_epub.apply_corrections_core 用 by_phys 字典、phys 为唯一键）：
  插入的节锚**绝不复用父章 phys**——否则同一 phys 多块会触发字典覆盖、纠错错配。
  本脚本对全锚（原章锚 + 新节锚）按文件阅读序重新赋 1..N 连续 SEC idx，天然无碰撞。

用法：
  python 02_ncx_split_epub.py <book.epub> <work_dir> [--apply] [--min-depth 1] [--no-meta]

输入（落 <work_dir>/）：
  - fulltext_struct.txt  当前章级 PAGE 锚（`<<<PAGE phys=N book=B title=...>>>`）
  - page_index.json      SEC 索引（将重建）
  - toc_tree.json        NCX 树（将重建）
  - book_meta.json       【可选】存在则同步把 chapters[].phys_start / total_phys 改到新 SEC idx（带 .bak）

输出：
  - 重写 fulltext_struct.txt（细粒度 PAGE 锚，phys 连续 1..N）
  - 重写 page_index.json / toc_tree.json
  - 更新 book_meta.json（若 --no-meta 未设且文件存在）
  -   默认 dry-run：仅打印匹配率 / 密度 / 新旧 SEC 数 / 待更新 phys_start，不写盘。

依赖：标准库（zipfile 读 EPUB 内 .ncx，xml.etree 解析 NCX）；不依赖 ebooklib / bs4。
"""
import argparse
import json
import os
import re
import shutil
import sys
import zipfile
from xml.etree import ElementTree as ET

# ---- 解释器守卫（v1.30.0）：先切到 EBOOK_PY 指定的 venv，再 import 第三方库 ----
# 详见 _interp_guard.py。本脚本当前纯标准库，接入是为口径统一。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

NCX_NS = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}

# 默认视为辅文、不插锚的标题（与旧 epub_ncx_split.py 一致）
SKIP_TITLES = {"封面", "书名页", "编委会", "《中国兵学通史》编委会", "总序", "总　序", "目录"}


# ---------- NCX 解析 ----------
def extract_ncx_entries(epub_path):
    """从 EPUB 的 NCX 中提取目录条目：[(depth, title, src), ...]"""
    with zipfile.ZipFile(epub_path, "r") as z:
        ncx_files = [f for f in z.namelist() if f.endswith(".ncx")]
        if not ncx_files:
            raise FileNotFoundError(f"EPUB 中未找到 .ncx 文件: {epub_path}")
        ncx_content = z.read(ncx_files[0]).decode("utf-8")
    root = ET.fromstring(ncx_content)
    navmap = root.find("ncx:navMap", NCX_NS)
    if navmap is None:
        raise ValueError("NCX 中未找到 navMap")

    def walk(elem, depth=0):
        results = []
        for navpoint in elem.findall("ncx:navPoint", NCX_NS):
            label = navpoint.find("ncx:navLabel/ncx:text", NCX_NS)
            content = navpoint.find("ncx:content", NCX_NS)
            title = (label.text or "").strip() if label is not None else ""
            src = content.get("src", "") if content is not None else ""
            results.append((depth, title, src))
            results.extend(walk(navpoint, depth + 1))
        return results

    return walk(navmap)


# ---------- struct 解析 ----------
PAGE_RE = re.compile(
    r"<<<PAGE\s+phys=(\d+)\s+book=(\S+)(?:\s+title=(?:\"([^\"]*)\"|(.*?(?=\s*>>>))))?\s*>>>"
)


def find_page_anchors(lines):
    """现有章级 PAGE 锚：[(line_idx, phys, book, title), ...]"""
    anchors = []
    for i, line in enumerate(lines):
        m = PAGE_RE.search(line)
        if m:
            phys, book = int(m.group(1)), m.group(2)
            title = m.group(3) or m.group(4) or ""
            anchors.append((i, phys, book, title))
    return anchors


def find_toc_range(lines, anchors):
    for i, (idx, phys, book, title) in enumerate(anchors):
        if title and "目录" in title:
            start = idx
            end = anchors[i + 1][0] if i + 1 < len(anchors) else len(lines)
            return start, end
    return None, 0


def normalize_text(s):
    return s.replace("　", "").replace(" ", "")


def find_title_in_body(title, lines, search_start):
    """在正文范围内搜索标题位置（多级回退匹配，已实战验证）。返回行号或 None。"""
    # 1. 精确匹配
    for i in range(search_start, len(lines)):
        if title in lines[i]:
            return i
    # 2. 去空格后匹配
    title_compact = normalize_text(title)
    if title_compact:
        for i in range(search_start, len(lines)):
            if title_compact in normalize_text(lines[i]):
                return i
    # 3. 稳定前缀（NCX 标题被截断）
    if len(title) >= 6:
        prefix = title[:6]
        for i in range(search_start, len(lines)):
            if lines[i].startswith(prefix):
                return i
    # 4. 章节词 + 第一个实词（NCX 缺字但 03 已补字）
    if len(title) >= 4:
        first_break = None
        for j, ch in enumerate(title):
            if ch in "　 \u3000、，。：；！？":
                first_break = j
                break
        if first_break is not None:
            prefix = title[: first_break + 1]
            second_start = first_break + 1
            while second_start < len(title) and title[second_start] in "　 \u3000、，。：；！？":
                second_start += 1
            second_end = second_start
            while second_end < len(title) and title[second_end] not in "　 \u3000、，。：；！？":
                second_end += 1
            if second_end > second_start:
                prefix = title[:second_end]
                matches = [i for i in range(search_start, len(lines)) if lines[i].startswith(prefix)]
                if len(matches) == 1:
                    return matches[0]
    # 5. 关键词组合（顿号/逗号分隔；"X 的 Y"）
    if len(title) >= 6:
        tokens = re.split(r"[、，,]", title)
        tokens = [t.strip().replace("　", "").replace(" ", "") for t in tokens if len(t.strip()) >= 2]
        if len(tokens) >= 2:
            kw1, kw2 = tokens[0], tokens[1]
            section_prefix = ""
            for j, ch in enumerate(title):
                if ch in "　 \u3000、，。：；！？":
                    section_prefix = title[:j]
                    break
            cands = []
            for i in range(search_start, len(lines)):
                lc = normalize_text(lines[i])
                if section_prefix and not lc.startswith(normalize_text(section_prefix)):
                    continue
                if normalize_text(kw1) in lc and normalize_text(kw2) in lc:
                    cands.append(i)
            if len(cands) == 1:
                return cands[0]
        m = re.match(r"^([一二三四五六七八九十]+[、，,])(.+?)的(.+)$", title)
        if m:
            sp, kw1, kw2 = m.group(1), m.group(2).strip(), m.group(3).strip()
            if len(kw1) >= 1 and len(kw2) >= 2:
                cands = []
                for i in range(search_start, len(lines)):
                    lc = normalize_text(lines[i])
                    if not lc.startswith(normalize_text(sp)):
                        continue
                    if normalize_text(kw1) in lc and normalize_text(kw2) in lc:
                        cands.append(i)
                if len(cands) == 1:
                    return cands[0]
    return None


def build_anchor_line(phys, book, title):
    safe = title.replace('"', "＂").replace("<", "＜").replace(">", "＞")
    return f'<<<PAGE phys={phys} book={book} title="{safe}">>>'


def main():
    ap = argparse.ArgumentParser(description="EPUB 源头级 NCX 节锚细化（重排 SEC idx）")
    ap.add_argument("epub", help="EPUB 文件路径")
    ap.add_argument("work", help="本书 work 目录（含 fulltext_struct.txt / page_index.json / toc_tree.json）")
    ap.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    ap.add_argument("--min-depth", type=int, default=1, help="仅插入 depth≥该值的 NCX 条目（默认 1；设 2 只插节/小节）")
    ap.add_argument("--no-meta", action="store_true", help="不更新 book_meta.json")
    args = ap.parse_args()

    if not os.path.exists(args.epub):
        print(f"[ERROR] EPUB 不存在: {args.epub}")
        sys.exit(1)
    struct_path = os.path.join(args.work, "fulltext_struct.txt")
    if not os.path.exists(struct_path):
        print(f"[ERROR] 未找到 fulltext_struct.txt: {struct_path}")
        sys.exit(1)

    # 读 struct（统一 LF：折叠任何 CR 连写，杜绝 \r\r\n 双 CR 腐化）
    with open(struct_path, encoding="utf-8", newline="") as f:
        raw = f.read()
    while "\r\r\n" in raw:
        raw = raw.replace("\r\r\n", "\n")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")

    anchors = find_page_anchors(lines)
    if not anchors:
        print("[ERROR] 未在 fulltext_struct.txt 中找到 PAGE 锚，跳过。")
        sys.exit(1)
    toc_start, toc_end = find_toc_range(lines, anchors)
    search_start = toc_end if toc_start is not None else 0

    entries = extract_ncx_entries(args.epub)
    matched = []
    unmatched = []
    for depth, title, src in entries:
        if not title or title in SKIP_TITLES:
            continue
        if depth == 0 and any(k in title for k in ("封面", "书名页", "编委会", "总序", "目录")):
            continue
        if depth < args.min_depth:
            continue
        found = find_title_in_body(title, lines, search_start)
        if found is not None:
            # 父章 book：found 之前最近的现有 PAGE 锚
            nearest_book = anchors[0][2]
            for a in anchors:
                if a[0] <= found:
                    nearest_book = a[2]
                else:
                    break
            matched.append((depth, title, src, found, nearest_book))
        else:
            unmatched.append((depth, title, src))

    print(f"\n=== NCX 节锚细化（源头级）: {os.path.basename(args.epub)} ===")
    print(f"现有章级锚: {len(anchors)}")
    print(f"NCX 候选条目(depth≥{args.min_depth}, 去辅文): {len(matched) + len(unmatched)}")
    print(f"匹配成功: {len(matched)}  未匹配: {len(unmatched)}")
    if unmatched:
        print("未匹配（需人工核对 NCX 标题与正文差异）:")
        for d, t, s in unmatched[:30]:
            print(f"  [{d}] {t}")

    # 构建事件（章锚 + 新节锚），按文件行序排序后顺排 SEC idx
    events = []  # (line_idx, kind, payload)
    for (li, phys, book, title) in anchors:
        events.append((li, "chap", (phys, book, title)))
    for (depth, title, src, found, book) in matched:
        events.append((found, "sec", (title, book, src)))
    events.sort(key=lambda e: e[0])

    # 去重：若某节锚行恰好与某章锚同行（NCX 章标题=文档首标题），保留章锚、丢弃该节锚
    seen_line = set()
    dedup = []
    for e in events:
        if e[1] == "sec" and e[0] in seen_line:
            continue
        if e[1] == "chap":
            seen_line.add(e[0])
        dedup.append(e)

    new_sec_count = sum(1 for e in dedup if e[1] == "sec")
    total_new = len(dedup)
    old_density = len(lines) / len(anchors)
    new_density = len(lines) / total_new if total_new else 0
    print(f"\n新 SEC 总数: {len(anchors)} → {total_new}（新增节锚 {new_sec_count}）")
    print(f"锚密度: {old_density:.1f} 行/锚 → {new_density:.1f} 行/锚（提升 {old_density/new_density:.1f}x）")

    if not args.apply:
        print("\n[Dry-run] 未写盘。加 --apply 执行（会先备份 fulltext_struct/page_index/toc_tree/book_meta）。")
        sys.exit(0)

    # ---------- 写盘（先备份）----------
    for fn in ("fulltext_struct.txt", "page_index.json", "toc_tree.json"):
        p = os.path.join(args.work, fn)
        if os.path.exists(p):
            shutil.copy2(p, p + ".bak-ncx")
    meta_path = os.path.join(args.work, "book_meta.json")
    if os.path.exists(meta_path) and not args.no_meta:
        shutil.copy2(meta_path, meta_path + ".bak-ncx")

    # 赋新 phys（按 dedup 顺序 1..N）
    new_phys_by_event = {}
    for i, e in enumerate(dedup, start=1):
        new_phys_by_event[id(e)] = i

    # 旧章 phys → 新 phys 映射（用于 book_meta phys_start）
    old_to_new = {}
    for e in dedup:
        if e[1] == "chap":
            old_to_new[e[2][0]] = new_phys_by_event[id(e)]

    # 重写 struct：章锚行替换为新 phys；同前行前插入节锚行（统一 LF，不拼 \r）
    chap_line_event = {e[0]: e for e in dedup if e[1] == "chap"}
    inserts_before = {}
    for e in dedup:
        if e[1] == "sec":
            np = new_phys_by_event[id(e)]
            title, book, _src = e[2]
            inserts_before.setdefault(e[0], []).append(build_anchor_line(np, book, title))

    new_lines = []
    for i, line in enumerate(lines):
        if i in inserts_before:
            for al in inserts_before[i]:
                new_lines.append(al)
        ev = chap_line_event.get(i)
        if ev is not None:
            np = new_phys_by_event[id(ev)]
            _old_phys, book, title = ev[2]
            line = build_anchor_line(np, book, title)
        new_lines.append(line)
    if len(lines) in inserts_before:  # 理论不会发生
        for al in inserts_before[len(lines)]:
            new_lines.append(al)

    new_struct = "\n".join(new_lines)
    with open(struct_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_struct)
    print(f"[APPLIED] fulltext_struct.txt 已重写（{total_new} 锚，phys 连续 1..{total_new}）")

    # 重建 page_index.json / toc_tree.json
    page_index = read_json_safe(os.path.join(args.work, "page_index.json"))
    old_href_by_idx = {x.get("idx"): x for x in page_index} if isinstance(page_index, list) else {}
    new_index = []
    for e in dedup:
        np = new_phys_by_event[id(e)]
        if e[1] == "chap":
            old_phys, book, title = e[2]
            old = old_href_by_idx.get(old_phys, {})
            href = old.get("href", "")
            path = old.get("path", [])
            pp = int(book) if (book.isdigit() and int(book) > 0) else None
            cc = old.get("char_count", 0)
        else:
            title, book, src = e[2]
            # 父章 href
            parent = None
            for ee in dedup:
                if ee[1] == "chap" and ee[0] <= e[0]:
                    parent = ee
            href = old_href_by_idx.get(parent[2][0], {}).get("href", "") if parent else ""
            path = []
            pp = int(book) if (book.isdigit() and int(book) > 0) else None
            cc = 0
        new_index.append({"idx": np, "href": href, "title": title, "path": path,
                          "char_count": cc, "print_page": pp})
    with open(os.path.join(args.work, "page_index.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(new_index, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.work, "toc_tree.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"sections": new_index}, f, ensure_ascii=False, indent=2)
    print(f"[APPLIED] page_index.json / toc_tree.json 已重建（{len(new_index)} 条）")

    # 更新 book_meta.json（chapters[].phys_start → 新 SEC idx，phys_end 同步重映射）
    if not args.no_meta and os.path.exists(meta_path):
        meta = read_json_safe(meta_path)
        if isinstance(meta, dict) and "chapters" in meta:
            # phys_end 漏映射修复（2026-10-04）：重排 SEC idx 后，chapter 的 phys_end 若仍指旧索引，
            # pipeline_assemble 用 setdefault 补全 phys_end 不会覆盖陈旧值 → 章边界错位。
            # 新 phys_end = 下一章新 phys_start - 1；末章 = total_new（章节连续无空隙）。
            chaps_sorted = sorted(meta["chapters"],
                                  key=lambda c: c.get("phys_start", 0) or 0)
            for c in meta["chapters"]:
                old_start = c.get("phys_start")
                if old_start in old_to_new:
                    c["phys_start"] = old_to_new[old_start]
            for i, c in enumerate(chaps_sorted):
                os_ = c.get("phys_start")
                if os_ in old_to_new:
                    nxt = chaps_sorted[i + 1] if i + 1 < len(chaps_sorted) else None
                    if nxt is not None and nxt.get("phys_start") in old_to_new:
                        c["phys_end"] = old_to_new[nxt.get("phys_start")] - 1
                    else:
                        # 末章或下一章无映射：phys_end 取重排后末 SEC（total_new）
                        c["phys_end"] = total_new
            meta["total_phys"] = total_new
            with open(meta_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            print(f"[APPLIED] book_meta.json 已更新（{len(old_to_new)} 个 phys_start 重映射，"
                  f"phys_end 同步重映射，total_phys={total_new}）")
        else:
            print("[WARN] book_meta.json 无 chapters 字段，跳过更新（请手动据下方映射改 phys_start/phys_end）")
    elif not args.no_meta:
        print("[WARN] 未找到 book_meta.json，跳过更新（请手动据上方新 phys 改 chapters.phys_start/phys_end）")

    print("\n[OK] 源头级 NCX 节锚细化完成。下一步：照常跑 Stage 3 纠错 + assemble_epub.py（03 即细粒度）。")


def read_json_safe(p):
    """读 JSON，缺文件视为空配置；**损坏/非 UTF-8 必须报**——静默返回 {} 会让下游按空结构
    继续跑，把「文件坏了」伪装成「本来就没配」(2026-09-26 修：原 except 吞掉一切异常)。"""
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[ERROR] {p} 读取失败（编码错误 / JSON 损坏）：{e}"
              f"——请修好再继续；按空配置跑下去只会掩盖真实故障。")
        return {}


if __name__ == "__main__":
    main()
