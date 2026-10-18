#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_correction_ledger.py —— 生成 04_改错留痕.md（§6.3）

生成方式：**台账主源 + diff 兜底**。
  · 主源：corrections 台账（patch_manifest 中 status=active）逐条取 old/new/类型/依据/来源
          → §一 通改总清单（按类型分组、合并同 old→new）
          → §二 逐页明细（按 phys 排序，"翻到某页查改了什么"的主体）
  · 兜底：diff(03 干净正文, pristine 底本) 找"台账没记的改处"，只报警、不定性
          （纯 diff 会混入剥书眉/加锚/结构清洗，靠它生成清单必然把结构变化当改错）

五节结构（§6.3）：
  §一 通改总清单  —— 只列在底本真实命中的改动。0 命中的"预防性登记"不入本节：
                     那不是"改了几处"，是"准备了没用上"，会把真改过的淹没。
  §二 逐页明细    —— 同上只列命中项；按 phys 分组，是"查得到"的实现主体，不抽稀。
  §三 一致性自校验 —— 条数/类型分布/diff 兜底/未命中汇总。
  §四 撤销与人工修订 —— 4.1 经核实撤销的补丁 / 4.2 人工定点修订索引 / 4.3 派生件同步。
  §五 交付指纹    —— 由 assemble 定稿前追加（本脚本不写，避免覆盖）。

用法：python gen_correction_ledger.py --work <work> --out <交付目录> --meta <book_meta.json> --prefix <书名前缀>
"""
import re
import os
import sys
import glob
import difflib
import argparse
from collections import defaultdict

from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

from _common import (  # noqa: E402
    read_text_cn, read_json_cn, safe_write, strip_correction_traces,
    expand_patch_data, iter_spans,
)


def _classify_patch(c):
    """细分纠错类型。优先级：silent 结构性删除 > regex 正则型 > systematic 通改 >
    ocr 转录层 > 删除型 > 按 reason 关键词细分。"""
    if c.get("silent"):
        return "silent结构性删除"
    if c.get("regex"):
        return "regex正则型通改"
    if c.get("systematic"):
        return "systematic通改"
    if c.get("ocr"):
        return "ocr转录层纠错"
    if c.get("revision"):
        return "人工定点修订"
    if c.get("new") == "":
        return "删除型"
    reason = c.get("reason", "") or ""
    if any(k in reason for k in ["形近", "形近字", "误读"]):
        return "形近字误读"
    if any(k in reason for k in ["专名", "人名", "地名", "书名", "官名", "年号"]):
        return "专名校正"
    if any(k in reason for k in ["数字", "页码", "年代", "年份", "标点"]):
        return "数字标点校正"
    if any(k in reason for k in ["漏字", "衍字", "衍", "脱文"]):
        return "衍漏字校正"
    if any(k in reason for k in ["义校", "据义", "语境", "文义"]):
        return "义校（语境推断）"
    return "其他纠错"


def load_active(work):
    """加载 active 补丁（manifest 优先，否则全部 corrections/*.json）。"""
    active = []
    mp = os.path.join(work, "patch_manifest.json")
    if os.path.isfile(mp):
        try:
            for p in read_json_cn(mp).get("patches", []):
                if p.get("status") == "active" and p.get("old"):
                    p = dict(p)
                    p["type"] = _classify_patch(p)
                    active.append(p)
            if active:
                return active
        except Exception:
            pass
    for fp in sorted(glob.glob(os.path.join(work, "corrections", "*.json"))):
        try:
            data = expand_patch_data(read_json_cn(fp))
        except Exception:
            continue
        for c in data:
            if not isinstance(c, dict) or not c.get("old") or c.get("merge"):
                continue
            if c.get("status") == "deleted":
                continue
            active.append({"old": c["old"], "new": c.get("new", ""),
                           "phys": c.get("phys", ""),
                           "reason": c.get("reason", ""),
                           "type": _classify_patch(c),
                           "regex": bool(c.get("regex")),
                           "systematic": bool(c.get("systematic")) or str(c.get("phys", "")).upper() == "ALL",
                           "source": os.path.basename(fp)})
    return active


def load_deleted(work):
    """加载经核实撤销（status=deleted）的补丁，供 §四 4.1。"""
    out = []
    mp = os.path.join(work, "patch_manifest.json")
    if os.path.isfile(mp):
        try:
            for p in read_json_cn(mp).get("patches", []):
                if p.get("status") == "deleted" and p.get("old"):
                    out.append(p)
            if out:
                return out
        except Exception:
            pass
    for fp in sorted(glob.glob(os.path.join(work, "corrections", "*.json"))):
        try:
            data = expand_patch_data(read_json_cn(fp))
        except Exception:
            continue
        for c in data:
            if isinstance(c, dict) and c.get("old") and c.get("status") == "deleted":
                out.append({**c, "source": os.path.basename(fp)})
    return out


def _phys_sort_key(ph):
    if str(ph).upper() == "ALL":
        return (2, 0)
    if str(ph).isdigit():
        return (0, int(ph))
    return (1, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--prefix", default="")
    args = ap.parse_args()
    B, OUT, META = args.work, args.out, args.meta
    try:
        meta = read_json_cn(META)
    except Exception:
        meta = {}
    book = meta.get("book", "")

    active = load_active(B)
    deleted = load_deleted(B)

    struct_path = os.path.join(B, "fulltext_struct.txt")
    struct = read_text_cn(struct_path) if os.path.isfile(struct_path) else ""

    # ---- 关键分流：这条补丁在本书底本里到底碰到过字没有 ----
    # 0 命中 = 预防性登记 / old 写成通改前形态 / phys 记错页。它没有改过任何一个字，
    # 列进"改错留痕"就是虚报。单独汇总在 §三，不占 §一 §二 的篇幅。
    hit_patches, dead_patches = [], []
    for p in active:
        if not struct or p.get("regex"):
            hit_patches.append(p)  # 无底本或正则型无法逐字核，按命中处理
            continue
        if iter_spans(struct, p["old"]):
            hit_patches.append(p)
        else:
            dead_patches.append(p)

    # ---- §一 通改总清单（只列真实命中）----
    by_type = defaultdict(list)
    for p in hit_patches:
        by_type[p.get("type", "其他纠错")].append(p)
    sec1 = ["## 一、通改总清单（按类型）", ""]
    if not hit_patches:
        sec1 += ["- （本批无在底本命中的改动）", ""]
    for t in sorted(by_type):
        sec1.append(f"### {t}")
        merged = defaultdict(list)
        for p in by_type[t]:
            merged[(p["old"], p["new"])].append(p)
        for (old, new), lst in merged.items():
            pages = sorted({str(p.get("phys", "")) for p in lst
                            if str(p.get("phys", "")) not in ("", "ALL")},
                           key=lambda x: int(x) if x.isdigit() else 999999)
            srcs = sorted({str(p.get("source", "")) for p in lst})
            page_disp = (f"命中 {len(pages)} 页（{', '.join(pages[:10])}{'…' if len(pages) > 10 else ''}）"
                         if pages else "全局/跨页")
            sec1.append(f"- 「{old}」→「{new}」：{page_disp}；来源 {', '.join(srcs)}")
        sec1.append("")

    # ---- §二 逐页明细（只列真实命中；按 phys 分组）----
    by_phys = defaultdict(list)
    for p in hit_patches:
        by_phys[p.get("phys", "")].append(p)
    sec2 = ["## 二、逐页明细", ""]
    if not hit_patches:
        sec2 += ["- （本批无在底本命中的改动）", ""]
    for ph in sorted(by_phys, key=_phys_sort_key):
        sec2.append(f"### phys={ph}" + ("（全局 systematic 通改）" if str(ph).upper() == "ALL" else ""))
        for p in by_phys[ph]:
            reason = p.get("reason", "") or ""
            sec2.append(f"- 原OCR「{p['old']}」→改「{p['new']}」"
                        f"（{p.get('type','')}" + (f"，{reason}" if reason else "")
                        + f"，来源 {p.get('source','')}）")
        sec2.append("")

    # ---- §三 一致性自校验 ----
    cands = sorted(glob.glob(os.path.join(OUT, "*03_*.md")))
    if args.prefix:
        cands = [c for c in cands if os.path.basename(c).startswith(args.prefix)] or cands
    type_counts = defaultdict(int)
    for p in active:
        type_counts[p.get("type", "其他")] += 1
    sec3 = ["## 三、一致性自校验", ""]
    sec3.append(f"- active 补丁 {len(active)} 条：底本真实命中 {len(hit_patches)} 条 / "
                f"0 命中 {len(dead_patches)} 条（未改到任何字，见下）。")
    sec3.append("- 类型分布：" + "；".join(f"{t} {n}条"
                for t, n in sorted(type_counts.items(), key=lambda x: -x[1])) + "。")
    if cands and struct:
        pristine = re.sub(r"<<<PAGE[^>]*>>>\n?", "", struct)
        clean03 = strip_correction_traces(read_text_cn(cands[0]), strip_tonggai_lines=True)
        sec3.append(f"- 03 干净正文 {len(clean03)} 字符；pristine 底本 {len(pristine)} 字符（去锚）。")
        diff = list(difflib.unified_diff(pristine.splitlines(), clean03.splitlines(),
                                         fromfile="pristine(去锚)", tofile="03干净正文",
                                         lineterm="", n=0))

        def _structural(line):
            s = line.lstrip("+-").strip()
            return (not s or s.startswith("---") or s.startswith("+++") or s.startswith("@@")
                    or re.match(r"^第[一二三四五六七八九十百千\d]+[章节编卷]", s)
                    or "<<<PAGE" in s)

        content = [l for l in diff if not _structural(l)]
        # 排除已登记改处：相邻 -/+ 行命中某条命中补丁的 old→new
        pairs = [(p.get("old", ""), p.get("new", "")) for p in hit_patches if p.get("old")]
        filtered, i = [], 0
        while i < len(content):
            l = content[i]
            if l.startswith("-") and i + 1 < len(content) and content[i + 1].startswith("+"):
                minus, plus = l[1:], content[i + 1][1:]
                if any(old and old in minus and new in plus for old, new in pairs):
                    i += 2
                    continue
            filtered.append(l)
            i += 1
        n_add = sum(1 for l in filtered if l.startswith("+"))
        n_del = sum(1 for l in filtered if l.startswith("-"))
        sec3.append(f"- diff 兜底：行级差异 {len(filtered)} 行（+{n_add}/-{n_del}，已过滤锚/页眉/空行）。")
        if filtered:
            sec3.append("- 疑似「台账遗漏的改处」（前 20 行，须人工复核；纯 diff 会混入结构清洗）：")
            for l in filtered[:20]:
                sec3.append(f"  {l}")
            if len(filtered) > 20:
                sec3.append(f"  …（共 {len(filtered)} 行，仅列前 20）")
        else:
            sec3.append("- diff 兜底：无内容级差异（03 与 pristine 的差异全为结构性变化）。")
        sec3.append("- 所列 old 在 03 干净正文的残留是否为 0，由交付前 `check_book.py` 闸B 双向回放校验"
                    "（§6.1.2），不在本文件重算。")
    else:
        sec3.append("- 未找到 03 或 pristine 底本，diff 兜底跳过（须先 assemble）。")
    if dead_patches:
        sec3.append(f"- **0 命中补丁 {len(dead_patches)} 条**（old 在底本找不到，没有改到任何字；"
                    f"不列入 §一 §二）：")
        for p in dead_patches[:30]:
            sec3.append(f"  - phys={p.get('phys','')} 「{p.get('old','')}」→「{p.get('new','')}」"
                        f"（{p.get('source','')}）")
        if len(dead_patches) > 30:
            sec3.append(f"  …（共 {len(dead_patches)} 条，跑 `check_ledger.py` 看完整处置建议）")
        sec3.append("  > 处置：old 改写成通改后形态 / 标 `\"status\": \"deleted\"` / 确认此处无需补丁。"
                    "不需要为此重跑全书。")
    sec3.append("")

    # ---- §四 撤销与人工修订 ----
    sec4 = ["## 四、撤销与人工修订", ""]
    sec4 += ["### 4.1 经核实撤销的补丁", ""]
    if deleted:
        for p in deleted[:200]:
            why = p.get("delete_reason") or p.get("deleted_reason") or p.get("reason") or ""
            how = p.get("verified") or p.get("delete_date") or p.get("deleted_date") or ""
            sec4.append(f"- phys={p.get('phys','')} 「{p.get('old','')}」→「{p.get('new','')}」"
                        f"｜撤销理由：{why or '（未记）'}" + (f"｜核实方式：{how}" if how else ""))
        if len(deleted) > 200:
            sec4.append(f"- …（共 {len(deleted)} 条，仅列前 200）")
    else:
        sec4.append("- （无）")
    sec4 += ["", "### 4.2 人工定点修订（修订路径：以书为准改 03 后回写）", ""]
    revs = [p for p in hit_patches if p.get("type") == "人工定点修订"] + \
           [p for p in dead_patches if p.get("type") == "人工定点修订"]
    if revs:
        for p in revs:
            sec4.append(f"- phys={p.get('phys','')} ｜ 原文「{p.get('old','')}」→ 改为「{p.get('new','')}」"
                        f"｜ 理由：{p.get('reason','') or '（未记）'}")
        sec4.append("")
        sec4.append("> 人工修订同样进 §一 §二 的账，此处只做汇总索引——"
                    "修订路径下以书为准改了 03，凭这一节即可追到改了什么、为什么改。")
    else:
        sec4.append("- （本次未走修订路径）")
    sec4 += ["", "### 4.3 派生件同步（notes → 01/02 与 03 一致）", ""]
    sl_path = os.path.join(B, "derived_sync_log.json")
    if os.path.isfile(sl_path):
        try:
            sl = read_json_cn(sl_path)
            chs = sl.get("changes", [])
            tot = sum(int(x.get("count", 0)) for x in chs)
            sec4.append(f"- 同步规则：{sl.get('rule', '')}")
            sec4.append(f"- 本次同步 {tot} 处（{len(chs)} 条规则）。01/02 由 notes 编译，notes 写于台账"
                        f"定稿之前、援引的是未修正原文；若不同步，01/02 会保留已判定的错形，"
                        f"用 02 的名句到 03 检索命中 0，「可检索/可回溯」契约断裂。")
            for x in chs:
                sec4.append(f"- 「{x.get('old', '')}」→「{x.get('new', '')}」：{x.get('count', 0)} 处"
                            f"（来源 {x.get('file', '')}）")
            if not chs:
                sec4.append("- 台账已全部裁定但 notes 中无命中（notes 本就与 03 一致）。")
        except Exception as e:
            sec4.append(f"- ⚠️ derived_sync_log.json 解析失败（{e}）——同步留痕缺失，须复核。")
    else:
        sec4.append("- 本次 assemble 未发生派生件同步（notes 与 03 本就一致，或 corrections/ 为空）。")
    sec4.append("")

    body = "\n".join(sec1 + sec2 + sec3 + sec4)
    out = (f"# 《{book}》· 改错留痕（04）\n\n"
           f"> 由 gen_correction_ledger.py 生成（台账主源 + diff 兜底）。"
           f"逐页改错可追溯：翻到 03 的某一页，可在此查到这一页改了什么、为什么改。\n\n"
           f"---\n\n" + body)
    path = os.path.join(OUT, args.prefix + "04_改错留痕.md")
    safe_write(path, out)
    print(f"04 完成：active {len(active)} 条（真实命中 {len(hit_patches)} / 0 命中 {len(dead_patches)}）"
          f"→ {path}")


if __name__ == "__main__":
    main()
