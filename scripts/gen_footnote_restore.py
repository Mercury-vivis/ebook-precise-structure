#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_footnote_restore.py —— 把被剥离的脚注本体按页锚恢复进 fulltext_struct.txt（路径三·幂等可回退）

为什么单独一步
  路径三预产出端 book_ocr.py 若把 footnote 剥进 `<名>_版式区块.md`，
  00_ingest_bookocr.py 会按 label 拆到 work/other_strip_log.md 的 `[footnote]` 块。
  这些脚注不回到 struct，后续精读与 03 里就看不到它——脚注承载的引文出处、
  版本校勘、文献信息随之丢失。SKILL §0.3「保留 footnote」纪律的执行落点即本脚本。
  恢复形态为页末引用块 `> 〔注〕原文`，**一字不改**：保守噪声校对与台账留痕
  是精读阶段的人工动作（SKILL §3.11），脚本只做确定性搬运。

两种输入形态（详见 SKILL §3.11）
  来源A·被剥离：footnote 在 _版式区块.md → other_strip_log.md 的 `[footnote]` 块 → 本脚本恢复。
  来源B·在正文：footnote 本就在干净 .md（已进 struct）→ 不搬运，精读原样保留 + 00z 形态归一。
  逐页分类：某页正文已有同内容 `> 〔注〕` 的，判为来源B，跳过，不重复插入。

调用时机
  路径三 `00_ingest_bookocr.py` 之后、Stage 3 精读之前，与 00b/00c/00d 同批预处理。
  来源B（脚注本就齐全）的书不必跑；跑了也是零改动。

用法
  python gen_footnote_restore.py --work <work>            # 默认 dry-run，只报告将改什么
  python gen_footnote_restore.py --work <work> --apply     # 落盘恢复 + 写台账
  python gen_footnote_restore.py --work <work> --strip     # 按台账回退本脚本插入的块

退出码：0 = 正常（含无数据源 / 零改动的正常情形）；2 = work 下缺 fulltext_struct.txt。
"""
import os
import re
import sys
import json
import argparse

from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

from _common import read_text_cn, safe_write  # noqa: E402

PAGE_ANCHOR_RE = re.compile(r"(^<<<PAGE phys=(\d+)[^>]*>>>)", re.M)
SIDECAR_FOOTNOTE_RE = re.compile(r"^-\s*\*\*\[footnote\]\*\*\s*(.*)$", re.M)
SIDECAR_PAGE_RE = re.compile(r"^##\s*PAGE\s+phys=(\d+)\s*$", re.M)
LEDGER_FILE = "footnote_restore.json"
SIDECAR_FILE = "other_strip_log.md"
STRUCT_FILE = "fulltext_struct.txt"


def parse_sidecar(text):
    """从 other_strip_log.md 解析 [(phys, footnote_text)]，只认 [footnote] 块。"""
    out = []
    cur = None
    for line in text.splitlines():
        pm = SIDECAR_PAGE_RE.match(line.strip())
        if pm:
            cur = int(pm.group(1))
            continue
        fm = SIDECAR_FOOTNOTE_RE.match(line.strip())
        if fm and cur is not None:
            t = fm.group(1).strip()
            if t:
                out.append((cur, t))
    return out


def split_pages(text):
    """按页锚切块，返回 [{phys, anchor, body, body_start, body_end}]（保留原文位置）。"""
    marks = list(PAGE_ANCHOR_RE.finditer(text))
    pages = []
    for i, m in enumerate(marks):
        body_start = m.end()
        body_end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        pages.append({
            "phys": int(m.group(2)),
            "anchor": m.group(1),
            "body": text[body_start:body_end],
            "body_start": body_start,
            "body_end": body_end,
        })
    return pages


def note_block(text):
    """把一条脚注正文渲染成页末引用块（自带前后换行，保证 --strip 逐字节回退）。

    原文含换行时逐行加 `>`，保持引用块连续。前后换行纳入 block 一并登记进台账，
    apply 只做 text 拼接、strip 只删 block，故回退后与原字节完全一致。
    """
    lines = ("〔注〕" + text).splitlines() or [""]
    return "\n" + "\n".join("> " + ln for ln in lines) + "\n"


def load_ledger(work):
    p = os.path.join(work, LEDGER_FILE)
    if not os.path.isfile(p):
        return []
    try:
        obj = json.loads(read_text_cn(p))
        return obj.get("items", [])
    except (ValueError, OSError):
        return []


def save_ledger(work, items):
    p = os.path.join(work, LEDGER_FILE)
    obj = {
        "purpose": "gen_footnote_restore 台账：记录由本脚本插入的脚注块，供 --strip 精确回退",
        "source": SIDECAR_FILE,
        "target": STRUCT_FILE,
        "items": items,
    }
    safe_write(p, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def count_note_blocks(body):
    """正文块里已有的页末脚注引用块数（按行首 `> 〔注〕` 计）。"""
    return sum(1 for ln in body.splitlines() if ln.startswith("> 〔注〕"))


def apply_restore(work, footnotes, dry_run):
    struct_path = os.path.join(work, STRUCT_FILE)
    text = read_text_cn(struct_path)
    pages = split_pages(text)
    if not pages:
        print("[i] fulltext_struct.txt 无页锚，无需恢复。")
        return 0

    by_phys = {}
    for pg in pages:
        by_phys.setdefault(pg["phys"], []).append(pg)

    # 按 phys 分组数据源（一页可多条）
    grouped = {}
    for phys, note in footnotes:
        grouped.setdefault(phys, []).append(note)

    ledger = load_ledger(work)
    known = {(it.get("phys"), it.get("text")) for it in ledger}
    new_items = []
    inserts = []  # (body_end, block) 倒序插入
    already, no_page = 0, 0

    for phys, notes in grouped.items():
        targets = by_phys.get(phys, [])
        if not targets:
            no_page += len(notes)
            continue
        pg = targets[-1]  # 同 phys 多锚取末块（正常书一页一锚）
        body = pg["body"]
        # 幂等判据＝按页脚注块数差额，不做文本逐字比对：
        #   精读阶段允许对脚注做保守噪声校对（如清孤立 r./空格规整），
        #   文本可能与剥离日志原文不同；按「该页已有块数 ≥ 应恢复条数」跳过，
        #   既认来源B（已在正文），又不会因校对差异重复插入。
        existing = count_note_blocks(body)
        need = len(notes) - existing
        if need <= 0:
            already += len(notes)
            continue
        for note in notes[-need:]:
            if (phys, note) in known:  # 台账已登记过，尊重，不重复
                already += 1
                continue
            block = note_block(note)
            new_items.append({"phys": phys, "text": note, "block": block})
            inserts.append((pg["body_end"], block))

    print(f"[i] 脚注数据源 {len(footnotes)} 条：待恢复 {len(new_items)} / 已在正文或已登记 {already} / 无对应页锚 {no_page}")
    if not new_items:
        print("[i] 零改动（脚注已齐全或本书无脚注需恢复）。")
        return 0

    if dry_run:
        print(f"[dry-run] 将按页锚插入 {len(new_items)} 条 `> 〔注〕` 引用块，未落盘。")
        for it in new_items[:8]:
            print(f"    phys {it['phys']}: {it['text'][:60]}")
        if len(new_items) > 8:
            print(f"    …… 另 {len(new_items) - 8} 条")
        return 0

    # 按位置倒序插入，避免前面的插入改变后面的偏移；换行已在 block 内
    for pos, block in sorted(inserts, reverse=True):
        text = text[:pos] + block + text[pos:]
    safe_write(struct_path, text)
    save_ledger(work, ledger + new_items)
    print(f"[✔] 已恢复 {len(new_items)} 条脚注到 {STRUCT_FILE}，台账 {LEDGER_FILE} 已更新。")
    return 0


def strip_restore(work, dry_run):
    struct_path = os.path.join(work, STRUCT_FILE)
    ledger = load_ledger(work)
    if not ledger:
        print("[i] 台账为空或不存在，无需回退。")
        return 0
    text = read_text_cn(struct_path)
    removed = 0
    for it in ledger:
        block = it.get("block") or note_block(it.get("text", ""))
        if block in text:
            text = text.replace(block, "", 1)
            removed += 1
    print(f"[i] 台账登记 {len(ledger)} 条，正文命中并移除 {removed} 条。")
    if dry_run:
        print("[dry-run] 未落盘。")
        return 0
    safe_write(struct_path, text)
    p = os.path.join(work, LEDGER_FILE)
    if os.path.isfile(p):
        os.remove(p)
    print(f"[✔] 已回退，台账 {LEDGER_FILE} 已清除（正文里非本脚本插入的脚注不受影响）。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="把被剥离的脚注本体按页锚恢复进 fulltext_struct.txt（幂等可回退）")
    ap.add_argument("--work", required=True, help="工作目录（含 fulltext_struct.txt / other_strip_log.md）")
    ap.add_argument("--apply", action="store_true", help="落盘恢复（默认只 dry-run）")
    ap.add_argument("--strip", action="store_true", help="按台账回退本脚本插入的脚注块")
    args = ap.parse_args()

    if not os.path.isfile(os.path.join(args.work, STRUCT_FILE)):
        print(f"[错误] 未找到 {STRUCT_FILE}：{os.path.join(args.work, STRUCT_FILE)}")
        return 2

    if args.strip:
        return strip_restore(args.work, dry_run=not args.apply)

    side_path = os.path.join(args.work, SIDECAR_FILE)
    if not os.path.isfile(side_path):
        # 无剥离日志 = 上游未剥离（来源B）或本书无脚注：流程正常，无需任何提示
        print(f"[i] 无 {SIDECAR_FILE}：脚注形态为「已在正文」或本书无脚注，无需恢复。")
        return 0

    footnotes = parse_sidecar(read_text_cn(side_path))
    if not footnotes:
        print(f"[i] {SIDECAR_FILE} 中无 [footnote] 块，无需恢复。")
        return 0

    return apply_restore(args.work, footnotes, dry_run=not args.apply)


if __name__ == "__main__":
    sys.exit(main())
