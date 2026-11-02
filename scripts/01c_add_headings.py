#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""01c_add_headings.py —— 给 fulltext_struct.txt 的标题行加 Markdown 层级（v2.3.0 新增）

**为什么需要这一步**
03 由 `03 = fulltext_struct + corrections 台账` 渲染。若标题只加在 03 上（手改 / 外部平台改），
① 重跑 assemble 即全丢（struct 里没有）；② 闸B「书账互释」会逐条报成「③ 真未登记」
（03 有、struct+台账 复现不出来的东西）。故标题必须落 **struct**，与锚行同性质。

**落在流水线哪一步**
  01b_extract_epub（EPUB）/ 01a_raw + 01b_struct（PDF）→ 02_ncx_split_epub（EPUB 节锚细化）
  → **本步 01c** → pipeline_split 切章 → 派工 → assemble
必须排在 NCX 细化之后：细化会重排 SEC idx，先加标题再重编号会让「标题所在块」与 meta 对不上。

**标题从哪来**（按优先级）
  ① `book_meta.headings`：显式清单 `[{"text":"曲笔","level":3,"phys":31}, ...]`——**可完全复现**，
     书籍自己的层级曲线（如「前言/上册 → h2，篇名 → h3，篇内小节 → h4」）只能在这里表达；
  ② `book_meta.chapters[]`：用 `name` 作标题文本，`level` 字段定层级（缺省取 `--level`），
     搜索范围限该篇 phys 区间。
两条可并存：一本书既有篇级 chapters、又有篇内小节时，把小节写进 `headings` 即可。

**判据（宁少不多）**
只给「与标题文本归一后**等值**」的行加前缀，且每条只认**首处**命中。
归一＝去全部空白 + 去句末脚注号 `(1)`；不相等一律不动。这不是保守——
实测「前　言」「原　序」二字在正文里也会作为小标出现，按包含/模糊匹配会把正文句子误标成标题。

**幂等**：已带 `#` 的行直接跳过，重复跑不会叠前缀。

用法
    python 01c_add_headings.py --work <work> [--level 2] [--dry-run] [--strip]
      --level    未给 `level` 的条目的默认层级（1–6，默认 2 → `##`）
      --dry-run  只打印，不写盘
      --strip    反向：剥掉 struct 里所有行首 `#` 前缀（回退用）

写盘走 `safe_write`（原子 + LF + 备份），另存快照 `work/_bak/fulltext_struct.before_headings.<ts>.txt`。
"""
import argparse
import os
import re
import sys
from datetime import datetime

from _common import read_text_cn, read_json_cn, safe_write, derive_phys_ends

ANCHOR_RE = re.compile(r"<<<PAGE[^>]*?phys=(\d+)[^>]*?>>>")


def norm(s):
    """归一：去全部空白（含全角空格）+ 去句末脚注号。"""
    return re.sub(r"\(\d+\)", "", re.sub(r"[\s\u3000]+", "", s))


def split_blocks(lines):
    """返回 [(phys, start_idx, end_idx)]；锚行本身计入该块。首个锚之前的导言不进任何块。"""
    blocks, cur, start = [], None, 0
    for i, l in enumerate(lines):
        m = ANCHOR_RE.search(l)
        if m:
            if cur is not None:
                blocks.append((cur, start, i - 1))
            cur, start = int(m.group(1)), i
    if cur is not None:
        blocks.append((cur, start, len(lines) - 1))
    return blocks


def build_specs(meta, level_default):
    """把 meta 里的 headings / chapters 归一成 [{text, level, lo, hi, tag}]。

    **headings 存在时，chapters 只补齐 headings 未覆盖的篇名**——否则同一篇名会出两条 spec，
    第一条占了首处命中后，第二条继续在本篇区间里找第二个同值行，把正文小标误标成标题
    （实测「原　序」在书内出现两次，正好被这样误伤）。
    """
    specs = []
    covered = set()
    for h in (meta.get("headings") or []):
        text = (h.get("text") or "").strip()
        if not text:
            continue
        lvl = h.get("level") if isinstance(h.get("level"), int) else level_default
        ph = h.get("phys")
        specs.append({"text": text, "level": max(1, min(6, lvl)),
                      "lo": ph if isinstance(ph, int) else None,
                      "hi": ph if isinstance(ph, int) else None,
                      "tag": "headings"})
        covered.add(norm(text))
    for ch in (meta.get("chapters") or []):
        text = (ch.get("name") or "").strip()
        ps, pe = ch.get("phys_start"), ch.get("phys_end")
        if not text or not isinstance(ps, int):
            continue
        if norm(text) in covered:
            continue
        lvl = ch.get("level") if isinstance(ch.get("level"), int) else level_default
        specs.append({"text": text, "level": max(1, min(6, lvl)),
                      "lo": ps, "hi": pe if isinstance(pe, int) else None,
                      "tag": f"ch{ch.get('no')}"})
    return specs


def plan_prefix(lines, specs):
    """返回 ([(行号, 层级, 文本)], 未命中 spec 列表)。

    一个锚块最多产出一处标题（`claimed`）：块内既可能首行就是标题，也可能标题在块中部；
    一旦该块定了一处，同块其他 spec 不再重复认领，避免「一篇多标」。
    """
    blocks = split_blocks(lines)
    hits, missed = [], []
    used, claimed = set(), set()
    for sp in specs:
        target = norm(sp["text"])
        if not target:
            continue
        found = False
        for phys, s, e in blocks:
            if phys in claimed:
                continue
            if sp["lo"] is not None and phys < sp["lo"]:
                continue
            if sp["hi"] is not None and phys > sp["hi"]:
                continue
            stop = False
            for i in range(s, e + 1):
                if i in used:
                    continue
                raw = lines[i].strip()
                if not raw or ANCHOR_RE.search(raw):
                    continue
                if raw.startswith("#"):
                    used.add(i)
                    claimed.add(phys)
                    stop = True      # 幂等：该块已有前缀，本 spec 不再找
                    break
                if norm(raw) == target:
                    hits.append((i, sp["level"], sp["text"]))
                    used.add(i)
                    claimed.add(phys)
                    found = True
                    stop = True
                    break
            if stop:
                break
        if not found:
            missed.append(sp)
    return hits, missed


def main():
    ap = argparse.ArgumentParser(description="给 fulltext_struct 的标题行加 Markdown 层级")
    ap.add_argument("--work", required=True, help="work 目录（含 fulltext_struct.txt 与 book_meta.json）")
    ap.add_argument("--meta", default=None, help="book_meta.json（默认 work/book_meta.json）")
    ap.add_argument("--level", type=int, default=2, help="默认层级 1–6（默认 2 → `##`）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写盘")
    ap.add_argument("--strip", action="store_true", help="反向：剥掉所有行首 # 前缀")
    args = ap.parse_args()

    work = args.work
    struct_p = os.path.join(work, "fulltext_struct.txt")
    if not os.path.isfile(struct_p):
        print(f"❌ 找不到 {struct_p}", file=sys.stderr)
        sys.exit(2)
    meta_p = args.meta or os.path.join(work, "book_meta.json")
    if not os.path.isfile(meta_p):
        print(f"❌ 找不到 {meta_p}", file=sys.stderr)
        sys.exit(2)

    raw = read_text_cn(struct_p)
    lines = raw.split("\n")

    if args.strip:
        n = 0
        for i, l in enumerate(lines):
            if l.strip().startswith("#"):
                lines[i] = re.sub(r"^(\s*)#{1,6}\s+", r"\1", l)
                n += 1
        print(f"剥除 {n} 行标题前缀")
        if args.dry_run:
            print("（--dry-run，未写盘）")
            return
        safe_write(struct_p, "\n".join(lines))
        print(f"✅ 已写回 {struct_p}")
        return

    meta = read_json_cn(meta_p)
    derive_phys_ends(meta)
    specs = build_specs(meta, args.level)
    if not specs:
        print("❌ book_meta 既无 headings 也无 chapters，无标题可用（见 SKILL §3E schema）", file=sys.stderr)
        sys.exit(2)

    hits, missed = plan_prefix(lines, specs)
    for i, lvl, text in sorted(hits):
        if args.dry_run:
            print(f"  [dry] 行{i + 1}: {'#' * lvl} {text}")
        else:
            lines[i] = "#" * lvl + " " + lines[i].lstrip()
    print(f"共加前缀 {len(hits)} 处 / 标题条目 {len(specs)} 条")
    if missed:
        names = "、".join(m["text"] for m in missed[:8])
        more = "…" if len(missed) > 8 else ""
        print(f"⚠ {len(missed)} 条未在 struct 中按等值命中（多为已有前缀或文本不一致）：{names}{more}",
              file=sys.stderr)

    if args.dry_run:
        print("（--dry-run，未写盘）")
        return

    bak_dir = os.path.join(work, "_bak")
    os.makedirs(bak_dir, exist_ok=True)
    bak = os.path.join(bak_dir, f"fulltext_struct.before_headings.{datetime.now():%Y%m%d-%H%M%S}.txt")
    with open(bak, "w", encoding="utf-8", newline="\n") as f:
        f.write(raw)
    safe_write(struct_p, "\n".join(lines))
    print(f"✅ 已写回 {struct_p}（快照 {bak}）")
    print("ℹ 下一步：重跑 assemble（03 即带标题；闸B 应零差异），再重算交付指纹")


if __name__ == "__main__":
    main()
