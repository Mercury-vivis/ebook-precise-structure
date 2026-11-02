#!/usr/bin/env python3
# gen_brief.py —— 自动生成派工公共规范 _brief_common.md（全书级单文件）
#
# 用法：python gen_brief.py <WORK目录> [--meta book_meta.json]
#        [--proper-names off|all|topic] [--topic <词>]
# 读取 book_meta + chapters/_chapters.json + simplify_leak_report + error_forms.candidates，
# 输出 work/_brief_common.md（书籍信息 / 章节切分 / 双产出要求 / 通改候选 / 专名 / 简繁 / 漏校模式 / 铁律）。
#
# v2.3.0 收口：段落渲染统一走 `_common.brief_sections()`（与 gen_dispatch_brief 同一真相源），
#   键名只认 schema 权威键（book / type / total_phys / offset / is_traditional），phys_end 由脚本推导。
#   缺字段**显式告警**（stderr），不再往交付件里写「（待填…）」冒充内容——旧写法会把键名写错
#   伪装成"内容待填"，交付件照发，错到下一个人读 brief 才发现。

import os
import sys
import argparse
import glob

from _common import (read_text_cn, read_json_cn, safe_write, derive_phys_ends,
                     check_meta_keys, brief_sections, select_cross_book_names)


def load_chapters(work):
    """读 chapters/_chapters.json，返回 [{no,name,phys_start,phys_end,chars,...}]。"""
    p = os.path.join(work, "chapters", "_chapters.json")
    if not os.path.isfile(p):
        return []
    return read_json_cn(p)


def count_chapter_lines(work, ch):
    """统计章节文件实际行数与字符数（_chapters.json 无 chars 字段时兜底）。"""
    pat = os.path.join(work, "chapters", f"{ch['no']:02d}_*.txt")
    files = glob.glob(pat)
    if not files:
        return 0, 0
    text = read_text_cn(files[0])
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    return lines, len(text)


def merge_chapter_stats(meta_chapters, split_chapters):
    """把 _chapters.json 的实测字数并进 book_meta 章表（book_meta 记结构，不记字数）。"""
    by_no = {c.get("no"): c for c in split_chapters}
    out = []
    for c in meta_chapters:
        c = dict(c)
        s = by_no.get(c.get("no")) or {}
        if isinstance(s.get("chars"), int):
            c["chars"] = s["chars"]
        out.append(c)
    return out


def gen_brief(work, meta_path=None, proper_names_mode="off", topic=""):
    meta_path = meta_path or os.path.join(work, "book_meta.json")
    if not os.path.isfile(meta_path):
        print(f"❌ book_meta.json 不存在：{meta_path}", file=sys.stderr)
        sys.exit(2)
    meta = read_json_cn(meta_path)
    derive_phys_ends(meta)   # phys_end 可省略（schema 已标），由相邻篇推导
    check_meta_keys(meta)    # 缺必需键 / 残留同义旧键 → stderr 告警，不静默降级

    chapters = merge_chapter_stats(meta.get("chapters") or [], load_chapters(work))
    for ch in chapters:
        if "chars" not in ch:
            _lines, cc = count_chapter_lines(work, ch)
            if cc:
                ch["chars"] = cc

    pn = select_cross_book_names(proper_names_mode, topic)
    sections = brief_sections(meta, chapters=chapters, proper_names=pn["names"], work=work)

    lines = ["# 公共精读规范（所有组必须遵守）", ""]
    for key, heading, body in sections:
        if not body:
            continue
        lines.append(f"## {heading}")
        lines.append("")
        lines.extend(body)
        lines.append("")

    content = "\n".join(lines).rstrip("\n") + "\n"
    out = os.path.join(work, "_brief_common.md")
    safe_write(out, content)
    print(f"✅ 已生成 {out}（{len(content)}字，{len(chapters)}章，专名 {len(pn['names'])} 条）")
    return out


def main():
    ap = argparse.ArgumentParser(description="生成派工公共规范 _brief_common.md")
    ap.add_argument("work")
    ap.add_argument("--meta", default=None, help="book_meta.json（默认 work/book_meta.json）")
    ap.add_argument("--proper-names", default="off", choices=["off", "all", "topic"],
                    help="跨卷专名注入策略：off=不注入（缺省）/ all=全部已确认 / topic=按 --topic 过滤")
    ap.add_argument("--topic", default="", help="配 --proper-names topic 用（如「甲午战争」）")
    args = ap.parse_args()
    if args.proper_names == "topic" and not args.topic.strip():
        print("❌ --proper-names topic 须配 --topic <词>", file=sys.stderr)
        sys.exit(2)
    gen_brief(args.work, args.meta, args.proper_names, args.topic if args.proper_names == "topic" else "")


if __name__ == "__main__":
    main()
