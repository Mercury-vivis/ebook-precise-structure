#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_footnote_restore.py —— gen_footnote_restore 的四态回归（不依赖真实书，自造最小样本）

四态：来源A 恢复 / 来源B 零改动 / 幂等二跑 / --strip 逐字节回退。
跑法：python test_footnote_restore.py   （需 scripts/ 在 sys.path，与被测脚本同目录即可）
"""
import os
import sys
import json
import tempfile
import shutil

from gen_footnote_restore import (
    parse_sidecar, note_block, apply_restore, strip_restore,
    SIDECAR_FILE, STRUCT_FILE, LEDGER_FILE,
)


def make_work(root, footnotes_in_sidecar, body):
    w = os.path.join(root, "work")
    os.makedirs(w, exist_ok=True)
    struct = "<<<PAGE phys=1 book=1>>>\n" + body + "\n<<<PAGE phys=2 book=2>>>\n" + body
    with open(os.path.join(w, STRUCT_FILE), "w", encoding="utf-8", newline="\n") as f:
        f.write(struct)
    side = "## PAGE phys=1\n\n" + "".join(
        f"- **[footnote]** {t}\n\n" for t in footnotes_in_sidecar
    )
    with open(os.path.join(w, SIDECAR_FILE), "w", encoding="utf-8", newline="\n") as f:
        f.write(side)
    return w, struct


def test_all():
    notes = ["注甲：某书卷一。", "注乙：另一条。"]
    pairs = [(1, n) for n in notes]  # apply_restore 接收 [(phys, note)]
    root = tempfile.mkdtemp(prefix="fn_restore_")
    try:
        # 来源A：正文无脚注 → 恢复
        wa, orig = make_work(os.path.join(root, "a"), notes, "正文内容。\n")
        assert apply_restore(wa, pairs, dry_run=False) == 0
        got = open(os.path.join(wa, STRUCT_FILE), encoding="utf-8").read()
        assert got.count("> 〔注〕") == 2, "来源A 应插入 2 条"
        led = json.load(open(os.path.join(wa, LEDGER_FILE), encoding="utf-8"))
        assert len(led["items"]) == 2
        print("[PASS] 来源A 恢复 2 条")

        # 幂等二跑 → 零改动
        before = open(os.path.join(wa, STRUCT_FILE), "rb").read()
        assert apply_restore(wa, pairs, dry_run=False) == 0
        after = open(os.path.join(wa, STRUCT_FILE), "rb").read()
        assert before == after, "幂等二跑不应改动正文"
        print("[PASS] 幂等二跑零改动")

        # --strip → 逐字节回退
        assert strip_restore(wa, dry_run=False) == 0
        back = open(os.path.join(wa, STRUCT_FILE), "rb").read()
        assert back == orig.encode("utf-8"), "strip 后应与原字节一致"
        assert not os.path.exists(os.path.join(wa, LEDGER_FILE))
        print("[PASS] --strip 逐字节回退")

        def with_notes_in_page1(work, blocks):
            """把脚注块插到 phys=1 页块末尾（下一页锚之前）。"""
            p = os.path.join(work, STRUCT_FILE)
            t = open(p, encoding="utf-8").read()
            anchor2 = "<<<PAGE phys=2 book=2>>>"
            t = t.replace(anchor2, "".join(blocks) + anchor2, 1)
            open(p, "w", encoding="utf-8", newline="\n").write(t)

        # 来源B：正文已有 2 条 → 零改动
        wb, _ = make_work(os.path.join(root, "b"), notes, "正文内容。\n")
        with_notes_in_page1(wb, [note_block(n) for n in notes])
        before = open(os.path.join(wb, STRUCT_FILE), "rb").read()
        assert apply_restore(wb, pairs, dry_run=False) == 0
        after = open(os.path.join(wb, STRUCT_FILE), "rb").read()
        assert before == after, "来源B 应零改动"
        print("[PASS] 来源B 零改动")

        # 保守校对容忍：正文脚注文本被改过，仍按块数跳过
        wc, _ = make_work(os.path.join(root, "c"), notes, "正文内容。\n")
        with_notes_in_page1(wc, [note_block("注甲：某书卷一（校对后）。"), note_block(notes[1])])
        before = open(os.path.join(wc, STRUCT_FILE), "rb").read()
        assert apply_restore(wc, pairs, dry_run=False) == 0
        after = open(os.path.join(wc, STRUCT_FILE), "rb").read()
        assert before == after, "脚注文本被校对过后仍应判已在正文、零改动"
        print("[PASS] 保守校对后文本差异不重复插入")

        # 解析：只认 [footnote] 块
        side = "## PAGE phys=1\n\n- **[number]** 3\n\n- **[footnote]** 真脚注\n\n"
        assert parse_sidecar(side) == [(1, "真脚注")]
        print("[PASS] 解析只认 [footnote]")
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    test_all()
    print("\nALL PASS (6 cases)")
