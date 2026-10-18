#!/usr/bin/env python3
# 01a_raw.py —— Stage 1 原样 fulltext（法医底本）
#
# 仅逐页 get_text() 并加 `<<<PAGE phys=N>>>` 标记，**不剥书眉、不剥离页脚、不改任何字符**。
# 这是整条流水线的"法医底本"——所有纠错的对照源，必须原样可还原。
# page_index.json 仅占位（phys 计数，book 在 01b 计算）。
#
# v1.31.0 新增 --with-format：用 get_text("dict") 提取字体/大小/颜色/坐标，
# 输出 fulltext_raw.jsonl（每行一个文本块），供 preprocess_nonbody.py 识别批注。
# 默认行为不变，仍输出纯文本 fulltext_raw.txt。
#
# 用法：
#   python 01a_raw.py <book.pdf> <WORK目录>
#   python 01a_raw.py <book.pdf> <WORK目录> --with-format  # 同时输出格式JSONL

# ---- 解释器守卫（v1.30.0）：先切到 EBOOK_PY 指定的 venv，再 import 第三方库 ----
# 详见 _interp_guard.py；必须位于 import fitz 之前。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

import fitz, json, os, sys, argparse

def extract_with_format(page, phys):
    """用 get_text("dict") 提取带格式的文本块，返回 JSONL 行列表。"""
    d = page.get_text("dict")
    lines = []
    for block in d.get("blocks", []):
        if block.get("type") != 0:  # 0=文本块，1=图像块
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                lines.append({
                    "phys": phys,
                    "text": text,
                    "font": span.get("font", ""),
                    "size": round(span.get("size", 0), 2),
                    "color": span.get("color", 0),
                    "bbox": [round(v, 1) for v in span.get("bbox", [0, 0, 0, 0])],
                    "flags": span.get("flags", 0),
                })
    return lines

def main():
    ap = argparse.ArgumentParser(description="Stage 1 原样 fulltext（法医底本，不剥不改）")
    ap.add_argument("pdf", help="PDF 路径")
    ap.add_argument("work", help="工作目录")
    ap.add_argument("--with-format", action="store_true",
                    help="同时输出 fulltext_raw.jsonl（含字体/大小/颜色/坐标，供非正文识别）")
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    TOTAL = len(doc)
    blocks = []
    format_lines = []
    for i in range(TOTAL):
        phys = i + 1
        text = doc[phys - 1].get_text()
        blocks.append(f"<<<PAGE phys={phys}>>>\n{text}")
        if args.with_format:
            format_lines.extend(extract_with_format(doc[phys - 1], phys))
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

    if args.with_format:
        jsonl_out = os.path.join(args.work, "fulltext_raw.jsonl")
        with open(jsonl_out, "w", encoding="utf-8", newline="\n") as f:
            for line in format_lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        print(f"01a 格式输出：{len(format_lines)} 文本块 -> {jsonl_out}（供 preprocess_nonbody.py 识别批注）")

if __name__ == "__main__":
    main()
