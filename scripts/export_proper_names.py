#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_proper_names.py —— 跨册专名词典增量导出（v1.28.0 新增 · P1）

用途：一本精读完成后，把本书**实战确认过的专名与其 OCR 错形**增量并回
`references/proper_names_cross_book.json`（单一真相源，见 `_common.load_proper_names_lib`）。

为什么现在做（而不是等到引用阶段）：精读是**逐字过底本**的过程，此刻「顺手记下异形」
的增量成本几乎为零；等将来跨册引用/检索时再回头补，要重翻全库，成本高且必然漏。
同题材多册（如《六十年来中国与日本》八卷）里同一个专名各卷异写不同，
本库让后续各卷精读**开局就带着前卷经验**——这是「打地基」，不是超前建设。

防污染纪律（与 v1.27.0 E1 一脉相承）：
  - 新导出条目一律 `confirmed: false`——**只有人工确认后才进派工 brief**；
  - 只做**并集**，绝不覆盖已有条目（同名的错形取并集，source 追加书名）；
  - **不自动改字**：本库是提示性参考，不参与任何自动替换。

用法：
  python export_proper_names.py --work <work目录> [--book <书名>] [--min-len 2]
  python export_proper_names.py --work <work> --apply      # 默认 --dry-run，--apply 才写盘
  python export_proper_names.py --list                     # 只打印当前库摘要

退出码：0=正常；2=入参/文件缺失或 work 无可用数据。
"""

import argparse
import json
import os
import re
import sys

from _common import load_proper_names_lib, proper_names_lib_path

# 判定为「专名类」补丁的线索：type / reason 命中其一
PROPER_HINTS = ["专名", "人名", "地名", "职官", "官名", "字号", "谥号", "封爵", "书名", "国名", "族名"]

# 噪声过滤：new 侧含这些特征的不入典（数字/标点/过长描述性短语）
NOISE_RE = re.compile(r"^\s*$|^\d+$|^[^\u4e00-\u9fffA-Za-z]+$")

# 否定排除：reason 是自由文本，常出现「非专名」「不是人名」这类**否定表述**——
# 若只按关键词命中会把否定句也判成专名类（实测「非专名，不入典」即被误收）。
# type 是结构化字段，无否定写法，故仅对 reason 做否定排除。
NEG_RE = re.compile(r"(?:非|不是|并非|非是|不属于|无)\s*(?:" + "|".join(PROPER_HINTS) + r")")


def collect_from_corrections(work, min_len=2):
    """从 corrections/*.json 抽取专名类补丁的 (正字, [错形])。

    正字取 new，错形取 old。删除型（new 为空）跳过——无正字可立。
    """
    corr_dir = os.path.join(work, "corrections")
    if not os.path.isdir(corr_dir):
        return {}
    out = {}
    for name in sorted(os.listdir(corr_dir)):
        if not name.lower().endswith(".json"):
            continue
        path = os.path.join(corr_dir, name)
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        rows = data if isinstance(data, list) else data.get("corrections", [])
        if isinstance(rows, dict):
            rows = [rows]
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            typ = str(r.get("type", "") or "")
            reason = str(r.get("reason", "") or "")
            if not any(h in typ or h in reason for h in PROPER_HINTS):
                continue
            if NEG_RE.search(reason) and not any(h in typ for h in PROPER_HINTS):
                continue  # reason 为否定表述且 type 未明确标专名 → 不收
            new = str(r.get("new", "") or "").strip()
            old = str(r.get("old", "") or "").strip()
            if not new or not old or new == old:
                continue
            if NOISE_RE.match(new) or NOISE_RE.match(old):
                continue
            # 正则型补丁的 old 是正则原文，不是实际错形，不入典
            if r.get("regex"):
                continue
            if len(new) < min_len:
                continue
            out.setdefault(new, set())
            if len(old) >= min_len:
                out[new].add(old)
    return {k: sorted(v) for k, v in out.items()}


def collect_from_meta(work):
    """从 book_meta.json 的 proper_names 抽取正字（无错形）。"""
    meta_path = os.path.join(work, "book_meta.json")
    if not os.path.isfile(meta_path):
        return {}
    try:
        meta = json.load(open(meta_path, encoding="utf-8"))
    except Exception:
        return {}
    names = meta.get("proper_names", []) or []
    out = {}
    for n in names:
        if isinstance(n, dict):
            n = n.get("name", "")
        n = str(n or "").strip()
        if n and not NOISE_RE.match(n):
            out.setdefault(n, [])
    return out


def merge_into_lib(lib, candidates, book):
    """把候选并回库（只并集、不覆盖）。返回 (新增条目, 追加错形数, 跳过数, 明细)。"""
    by_name = {n["name"]: n for n in lib["names"]}
    added, appended, skipped = [], [], 0
    for name, forms in sorted(candidates.items()):
        if name in by_name:
            e = by_name[name]
            new_forms = [f for f in forms if f not in e["wrong_forms"]]
            if new_forms:
                e["wrong_forms"].extend(new_forms)
                appended.append((name, new_forms))
            if book and book not in [s for s in e["source"].split(" / ") if s]:
                e["source"] = (e["source"] + " / " + book).strip(" /") if e["source"] else book
            if not new_forms:
                skipped += 1
            continue
        e = {
            "name": name,
            "wrong_forms": list(forms),
            "source": book or "",
            # 防污染：新导出一律未确认，人工确认后才注入派工 brief
            "confirmed": False,
            "note": "",
        }
        lib["names"].append(e)
        by_name[name] = e
        added.append(name)
    lib["names"] = sorted(lib["names"], key=lambda x: x["name"])
    return added, appended, skipped


def main():
    ap = argparse.ArgumentParser(description="跨册专名词典增量导出（并集写入，新条目 confirmed=false）")
    ap.add_argument("--work", help="work 目录（读 corrections/ 与 book_meta.json）")
    ap.add_argument("--book", help="书名（写入 source 字段；缺省取 book_meta.title）")
    ap.add_argument("--min-len", type=int, default=2, help="专名最短字数（默认 2，单字不入典）")
    ap.add_argument("--apply", action="store_true", help="写盘（默认 --dry-run 只预览）")
    ap.add_argument("--list", action="store_true", help="只打印当前库摘要")
    args = ap.parse_args()

    if args.list:
        lib = load_proper_names_lib()
        print(f"库路径：{lib['path']}")
        print(f"条目 {len(lib['names'])}（已确认 {len([n for n in lib['names'] if n['confirmed']])}）"
              f" · 复合词 {len(lib['compound_words'])}")
        for n in lib["names"]:
            print(f"  {n['name']}{'' if n['confirmed'] else ' [未确认]'}"
                  f"  错形={n['wrong_forms']}  来源={n['source']}")
        return 0

    if not args.work:
        sys.stderr.write("[export_proper_names] 需指定 --work（或 --list 只看库）\n")
        sys.exit(2)
    if not os.path.isdir(args.work):
        sys.stderr.write(f"[export_proper_names] work 目录不存在：{args.work}\n")
        sys.exit(2)

    book = args.book
    if not book:
        meta_path = os.path.join(args.work, "book_meta.json")
        if os.path.isfile(meta_path):
            try:
                book = json.load(open(meta_path, encoding="utf-8")).get("title", "")
            except Exception:
                book = ""

    cands = {}
    for src, fn in (("corrections", collect_from_corrections), ("book_meta", collect_from_meta)):
        if src == "corrections":
            got = fn(args.work, args.min_len)
        else:
            got = fn(args.work)
        for k, v in got.items():
            cands.setdefault(k, set()).update(v)
    cands = {k: sorted(v) for k, v in cands.items()}

    if not cands:
        sys.stderr.write(f"[export_proper_names] {args.work} 未抽到专名条目"
                         f"（需 corrections 中 type/reason 命中专名线索，或 book_meta.proper_names 非空）。\n")
        sys.exit(2)

    lib_path = proper_names_lib_path()
    if not lib_path:
        sys.stderr.write("[export_proper_names] 找不到跨册专名库（references/ 或 resources/）\n")
        sys.exit(2)
    lib = json.load(open(lib_path, encoding="utf-8"))
    lib.setdefault("names", [])

    before = len(lib["names"])
    added, appended, skipped = merge_into_lib(lib, cands, book)

    print(f"来源：{args.work}" + (f"（书名：{book}）" if book else ""))
    print(f"抽到专名候选 {len(cands)} 个：")
    for k, v in list(cands.items())[:15]:
        print(f"  {k}  错形={v}")
    if len(cands) > 15:
        print(f"  … 另有 {len(cands) - 15} 个")
    print(f"\n合并结果：新增条目 {len(added)}，已有条目追加错形 {len(appended)}，无变化跳过 {skipped}")
    for name, forms in appended[:10]:
        print(f"  + 错形追加 {name} ← {forms}")
    if added:
        print(f"  新增：{'、'.join(added[:20])}")

    if not args.apply:
        print("\n[dry-run] 未写盘。确认无误后加 --apply 写入（新条目 confirmed=false，须人工确认后才生效）。")
        return 0

    lib["_date"] = __import__("datetime").date.today().isoformat()
    with open(lib_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(lib, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"\n已写入：{lib_path}（条目 {before} → {len(lib['names'])}）")
    print("提醒：新增条目 confirmed=false，不进派工 brief；人工核实后改为 true 才生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
