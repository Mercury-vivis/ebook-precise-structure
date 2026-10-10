#!/usr/bin/env python3
# gen_correction_ledger.py —— 生成 04_改错留痕.md（§6.3）
#
#   生成方式：台账主源 + diff 兜底。
#   - 主源：从 corrections 台账（patch_manifest 中 status=active）逐条取 old/new/类型/依据/来源
#           → §一 通改总清单（按类型分组、合并同 old→new）+ §二 逐页明细（按 phys 排序）
#   - 兜底：diff(最终 03 干净文本, 原始 pristine 文本) 发现"台账遗漏的改处"并报警
#           （纯 diff 会混入剥书眉/加锚/结构清洗等结构性变化，只做兜底提示，不靠它定改错）
#   - §三 一致性自校验：05 所列 old 在 03 残留 = 0（详见 pre_delivery_verifier 闸2）、条数对账、diff 兜底提示
#
#   用法：python gen_correction_ledger.py --work <工作目录> --out <交付目录> --meta <book_meta.json> --prefix <书名前缀>
#
import re, json, os, argparse, sys, glob, difflib
from collections import defaultdict
from _common import read_text_cn, read_json_cn, safe_write, strip_correction_traces
from _common import expand_patch_data  # 补丁加载口径单一真相源


def _classify_patch(c):
    """v1.14.0 P2-3：细分纠错类型（替代原 systematic/ocr_typo 二分）。
    分类优先级：silent结构性删除 > regex正则型 > systematic通改 > ocr转录层 > 删除型 > 按reason关键词细分。"""
    if c.get("silent"):
        return "silent结构性删除"
    if c.get("regex"):
        return "regex正则型通改"
    if c.get("systematic"):
        return "systematic通改"
    if c.get("ocr"):
        return "ocr转录层纠错"
    if c.get("new") == "":
        return "删除型"
    reason = c.get("reason", "")
    # 按 reason 关键词细分
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
    mp = os.path.join(work, "patch_manifest.json")
    active = []
    if os.path.isfile(mp):
        for p in read_json_cn(mp).get("patches", []):
            if p.get("status") == "active" and p.get("old"):
                p["type"] = _classify_patch(p)  # v1.14.0：细分类型
                active.append(p)
    else:
        for fp in sorted(glob.glob(os.path.join(work, "corrections", "*.json"))):
            # 加载口径统一（含顶层单条 dict 形态），否则该份补丁不进 04 台账
            data = expand_patch_data(read_json_cn(fp))
            for c in data:
                if isinstance(c, dict) and c.get("old") and not c.get("merge") \
                        and c.get("status") != "deleted":  # P2-16：与 manifest 分支对齐，过滤 tombstone
                    c["type"] = _classify_patch(c)  # v1.14.0：细分类型
                    active.append({"old": c["old"], "new": c.get("new", ""),
                                   "phys": c.get("phys", ""),
                                   "type": c["type"],
                                   "reason": c.get("reason", ""), "source": os.path.basename(fp)})
    return active


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
    meta = read_json_cn(META)
    book = meta.get("book", "")

    active = load_active(B)
    if not active:
        print("⚠️ 无 active 补丁，05 仍生成空壳（标注「无改错」）")

    # ---- §一 通改总清单（按类型分组，合并同 old→new）----
    by_type = defaultdict(list)
    for p in active:
        by_type[p.get("type", "ocr_typo")].append(p)
    sec1 = ["## 一、通改总清单（按类型）", ""]
    for t in sorted(by_type):
        sec1.append(f"### {t}")
        merged = defaultdict(list)
        for p in by_type[t]:
            merged[(p["old"], p["new"])].append(p)
        for (old, new), lst in merged.items():
            pages = sorted({str(p.get("phys", "")) for p in lst
                            if str(p.get("phys", "")) not in ("", "ALL")},
                           key=lambda x: int(x) if x.isdigit() else 999999)
            srcs = sorted({p.get("source", "") for p in lst})
            page_disp = f"命中 {len(pages)} 页（{', '.join(pages[:10])}{'…' if len(pages) > 10 else ''}）" if pages else "全局/跨页"
            sec1.append(f"- 「{old}」→「{new}」：{page_disp}；来源 {', '.join(srcs)}")
        sec1.append("")

    # ---- §二 逐页明细（按 phys 排序）----
    by_phys = defaultdict(list)
    for p in active:
        by_phys[p.get("phys", "")].append(p)
    sec2 = ["## 二、逐页明细", ""]
    for ph in sorted(by_phys, key=_phys_sort_key):
        sec2.append(f"### phys={ph}" + ("（全局 systematic 通改）" if str(ph).upper() == "ALL" else ""))
        for p in by_phys[ph]:
            sec2.append(f"- 原OCR「{p['old']}」→改「{p['new']}」（{p.get('type','')}，{p.get('reason','')}，来源 {p.get('source','')}）")
        sec2.append("")

    # ---- §三 一致性自校验（真实 diff 兜底 · v1.14.0 P2-2）----
    # v1.16.2 修复：优先选与本 prefix 同名的 03（目录里有旧 03 时不误核另一份）
    cands = sorted(glob.glob(os.path.join(OUT, "*03_*.md")))
    cands = [c for c in cands if os.path.basename(c).startswith(args.prefix)] or cands
    sec3 = ["## 三、一致性自校验", ""]
    sec3.append(f"- 05 条数 = active 补丁 {len(active)} 条（台账主源）。")
    # 类型统计
    type_counts = defaultdict(int)
    for p in active:
        type_counts[p.get("type", "其他")] += 1
    sec3.append("- 类型分布：" + "；".join(f"{t} {n}条" for t, n in sorted(type_counts.items(), key=lambda x: -x[1])) + "。")
    if cands and os.path.isfile(os.path.join(B, "fulltext_struct.txt")):
        struct = read_text_cn(os.path.join(B, "fulltext_struct.txt"))
        pristine = re.sub(r"<<<PAGE[^>]*>>>\n?", "", struct)
        clean03_raw = read_text_cn(cands[0])
        # v1.14.0：03 干净正文 = 剥留痕 + 剥通改清单块（与 verifier 同口径）
        clean03 = strip_correction_traces(clean03_raw, strip_tonggai_lines=True)
        sec3.append(f"- 03 干净正文 {len(clean03)} 字符；pristine 底本 {len(pristine)} 字符（去锚）。")
        # 真实行级 diff（v1.14.0 P2-2：替代原伪兜底的字符数统计）
        pristine_lines = pristine.splitlines()
        clean_lines = clean03.splitlines()
        diff = list(difflib.unified_diff(pristine_lines, clean_lines,
                                          fromfile="pristine(去锚)", tofile="03干净正文",
                                          lineterm="", n=0))
        # 过滤结构性变化：仅含锚/页眉/空行的差异行不算"疑似遗漏改处"
        def _is_structural(line):
            s = line.lstrip("+-").strip()
            return (not s or s.startswith("---") or s.startswith("+++") or s.startswith("@@")
                    or re.match(r"^第[一二三四五六七八九十百千\d]+[章节编卷]", s)
                    or "<<<PAGE" in s)
        content_diffs = [l for l in diff if not _is_structural(l)]
        # v1.16.2 修复：diff 兜底须排除已登记改处，否则每条正常纠错都进"疑似遗漏"使兜底失效。
        # 对相邻 -/+ 行，若命中某条 active 补丁的 old→new，则视为已登记改处，跳过这对。
        reg_pairs = {(p.get("old", ""), p.get("new", "")) for p in active if p.get("old")}
        _filtered = []
        i = 0
        while i < len(content_diffs):
            l = content_diffs[i]
            if l.startswith("-") and i + 1 < len(content_diffs) and content_diffs[i + 1].startswith("+"):
                minus, plus = l[1:], content_diffs[i + 1][1:]
                if any(old and old in minus and new in plus for old, new in reg_pairs):
                    i += 2
                    continue
            _filtered.append(l)
            i += 1
        content_diffs = _filtered
        n_add = sum(1 for l in content_diffs if l.startswith("+"))
        n_del = sum(1 for l in content_diffs if l.startswith("-"))
        sec3.append(f"- 真实 diff 兜底：行级差异 {len(content_diffs)} 行（+{n_add}/-{n_del}，已过滤锚/页眉/空行等结构性变化）。")
        if content_diffs:
            sec3.append("- 疑似「台账遗漏的改处」（diff 前 20 行，须人工复核；纯 diff 会混入结构清洗，不自动判定为纠错遗漏）：")
            for l in content_diffs[:20]:
                sec3.append(f"  {l}")
            if len(content_diffs) > 20:
                sec3.append(f"  …（共 {len(content_diffs)} 行，仅列前 20）")
        else:
            sec3.append("- diff 兜底：无内容级差异（03 与 pristine 的差异全为结构性变化，无疑似台账遗漏）。")
        sec3.append("- 05 所列 old 在 03 干净正文残留是否为 0，由 pre_delivery_verifier（闸2）机械校验，不在本文件重算。")
    else:
        sec3.append("- 未找到 03 或 pristine 底本，diff 兜底跳过（须先 assemble）。")
    sec3.append("")

    # ---- §四 派生件同步留痕（2026-10-05 P0）：notes → 01/02 与 03 一致化的每一处，逐条可追溯 ----
    # 零静默改动的延伸：派生件（01/02）的同步不是"顺便改掉"，而是与 03 同源同判据的
    # 一致性动作，每一处都须在本节可查，否则下游无法区分"已裁定同步"与"未知改动"。
    sec4 = ["## 四、派生件同步（notes → 01/02 与 03 一致）", ""]
    _sl = os.path.join(B, "derived_sync_log.json")
    if os.path.isfile(_sl):
        try:
            sl = read_json_cn(_sl)
            chs = sl.get("changes", [])
            tot = sum(int(x.get("count", 0)) for x in chs)
            sec4.append(f"- 同步规则：{sl.get('rule', '')}")
            sec4.append(f"- 本次同步 {tot} 处（{len(chs)} 条规则）。01/02 由 notes 编译，notes 写于"
                        f"纠错台账定稿之前、援引的是未修正原文；若不同步，01/02 会保留已判定的错形，"
                        f"形成「错形孤岛」（用 02 的名句到 03 检索命中 0，「可检索/可回溯」契约断裂）。"
                        f"故用同一份已裁定台账同步，并在此逐条留痕。")
            for x in chs:
                sec4.append(f"- 「{x.get('old', '')}」→「{x.get('new', '')}」：{x.get('count', 0)} 处（来源 {x.get('file', '')}）")
            if not chs:
                sec4.append("- 台账已全部裁定但 notes 中无命中（notes 本就与 03 一致）。")
        except Exception as e:
            sec4.append(f"- ⚠️ derived_sync_log.json 解析失败（{e}）——同步留痕缺失，不得视为无改动，须复核。")
    else:
        sec4.append("- 本次 assemble 未发生派生件同步（notes 与 03 本就一致，或 corrections/ 为空）。")
    sec4.append("")

    body = "\n".join(sec1 + sec2 + sec3 + sec4)
    out = (f"# 《{book}》· 改错留痕（04）\n\n"
           f"> 由 gen_correction_ledger.py 生成（台账主源 + diff 兜底）。逐页改错可追溯；05 引用本文件并附摘要。\n\n"
           f"---\n\n" + body)
    path = os.path.join(OUT, args.prefix + "04_改错留痕.md")
    safe_write(path, out)
    print(f"04 完成：{len(active)} 条 active 补丁 → {path}")


if __name__ == "__main__":
    main()
