#!/usr/bin/env python3
# 01a_raw.py —— Stage 1 原样 fulltext（法医底本）
#
# 仅逐页 get_text() 并加 `<<<PAGE phys=N>>>` 标记，**不剥书眉、不剥离页脚、不改任何字符**。
# 这是整条流水线的"法医底本"——所有纠错的对照源，必须原样可还原。
# page_index.json 仅占位（phys 计数，book 在 01b 计算）。
#
# 用法：python 01a_raw.py <book.pdf> <WORK目录>

# ---- 解释器守卫（v1.30.0）：先切到 EBOOK_PY 指定的 venv，再 import 第三方库 ----
# 详见 _interp_guard.py；必须位于 import fitz 之前。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

import fitz, json, os, sys, argparse

def main():
    ap = argparse.ArgumentParser(description="Stage 1 原样 fulltext（法医底本，不剥不改）")
    ap.add_argument("pdf", help="PDF 路径")
    ap.add_argument("work", help="工作目录")
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    TOTAL = len(doc)
    blocks = []
    for i in range(TOTAL):
        phys = i + 1
        text = doc[phys - 1].get_text()
        blocks.append(f"<<<PAGE phys={phys}>>>\n{text}")
    os.makedirs(args.work, exist_ok=True)
    out = os.path.join(args.work, "fulltext_raw.txt")
    # v1.16.2 修复：覆盖写前先备份（法医底本必须可还原），且原子写（先 .tmp 再 os.replace）。
    if os.path.isfile(out):
        import shutil
        shutil.copy2(out, out + ".bak")
    content = "\n".join(blocks)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    os.replace(tmp, out)
    # v1.16.2 修复：page_index 补 char_count（旧版缺失 → 下游 get("char_count",0) 全归 0）。
    idx = [{"phys": i + 1, "book": None, "char_count": len(doc[i].get_text())} for i in range(TOTAL)]
    idx_tmp = os.path.join(args.work, "page_index.json") + ".tmp"
    with open(idx_tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)
    os.replace(idx_tmp, os.path.join(args.work, "page_index.json"))
    print(f"01a 完成：{TOTAL} 页 -> {out}（原样，未剥书眉/页脚、未改字）")

if __name__ == "__main__":
    main()
