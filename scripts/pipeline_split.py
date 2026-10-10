#!/usr/bin/env python3
# pipeline_split.py —— 按章切分 fulltext_struct.txt -> chapters/NN_章名.txt（保留 <<<PAGE phys=N book=M>>> 锚）
#
# 用法：python pipeline_split.py <WORK目录> <book_meta.json>
#   book_meta.json 须含 "chapters": [{"no":1,"name":"...","phys_start":24,"phys_end":37}, ...]
#   切出 chapters/NN_章名.txt，并写出/更新 chapters/_chapters.json（含 book_start/book_end）。
#
# 注意：book 字段取自 fulltext_struct 的真实页码（phys-22 或分段 OFFSET），不可误写为正文！

import re, os, json, argparse, sys
from _common import read_text_cn, read_json_cn, safe_write  # #24 读取单一入口（统一 utf-8-sig 容错）；safe_write=N3 原子写（2026-10-04）

def slug(s):
    return re.sub(r"[\\/:*?\"<>|]", "_", s)[:30]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("meta")
    args = ap.parse_args()
    B = args.work
    meta = read_json_cn(args.meta)  # #24：统一 utf-8-sig 容错
    ch = meta["chapters"]

    struct = read_text_cn(os.path.join(B, "fulltext_struct.txt"))  # #24：单一入口
    # phys -> (book, body)
    # v1.16.2 修复：锚正则容忍 CRLF（旧版要求 `>>>\n` 紧跟单换行，CRLF 底本全部失配 → 每章 0 字符仍 exit 0）。
    blocks = {}
    for m in re.finditer(r"<<<PAGE phys=(\d+) book=([^>]*)>>>\r?\n(.*?)(?=\r?\n<<<PAGE|\Z)", struct, re.S):
        blocks[int(m.group(1))] = (m.group(2), m.group(3))

    out_dir = os.path.join(B, "chapters")
    os.makedirs(out_dir, exist_ok=True)
    meta_out = []
    missing_total = []  # 全书缺失物理页汇总（含章名）
    empty_chapters = []  # 切出 0 字符的章（phys 范围错误或章节不在 struct）
    for c in ch:
        no, name = c["no"], c["name"]
        a, b = c["phys_start"], c["phys_end"]
        parts = []
        missing = []  # 本章缺失物理页
        for p in range(a, b + 1):
            if p in blocks:
                book, body = blocks[p]
                parts.append(f"<<<PAGE phys={p} book={book}>>>\n{body}")
            else:
                missing.append(p)
        if missing:
            missing_total.append((no, name, missing))
            print(f"⚠ {no:02d} {name}: 缺失物理页 {missing}（phys_start/phys_end 可能写错，或该页不在 struct 中）")
        content = "\n".join(parts)
        # v1.16.2 修复：切出 0 字符的章 = phys 范围错误或章节根本不在 struct，须报错而非静默写空文件。
        if not content.strip():
            empty_chapters.append((no, name, a, b))
            print(f"❌ {no:02d} {name}: phys{a}-{b} 切出 0 字符（phys 范围错误或该章不在 struct，须修正 book_meta 后重跑）")
        fn = os.path.join(out_dir, f"{no:02d}_{slug(name)}.txt")
        safe_write(fn, content)  # N3（2026-10-04 审查）：走 safe_write 原子写
        meta_out.append({"no": no, "name": name, "phys_start": a, "phys_end": b,
                         "book_start": c.get("book_start"), "book_end": c.get("book_end")})
        print(f"{no:02d} {name}: phys{a}-{b} 字符{len(content)}")
    if empty_chapters:
        print(f"\n❌ 共 {len(empty_chapters)} 章切出 0 字符，pipeline_split 以非零状态退出（须修正 book_meta 的 phys_start/phys_end 后重跑）。", file=sys.stderr)
        sys.exit(1)
    safe_write(os.path.join(out_dir, "_chapters.json"),  # N3（2026-10-04 审查）：走 safe_write 原子写
              json.dumps(meta_out, ensure_ascii=False, indent=2))
    # 缺页汇总落盘，避免静默丢页
    if missing_total:
        with open(os.path.join(out_dir, "_missing_pages.log"), "w", encoding="utf-8", newline="\n") as f:
            for no, name, missing in missing_total:
                f.write(f"第{no}章 {name}: 缺失物理页 {missing}\n")
        print(f"\n⚠ 共 {len(missing_total)} 章存在缺失物理页，详见 chapters/_missing_pages.log")
    print(f"\n总章 {len(ch)}，已写 chapters/_chapters.json")

if __name__ == "__main__":
    main()
