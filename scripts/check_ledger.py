#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_ledger.py —— 台账可落地性校验（统一入口，§6.1.3 闸前移）

定位：**提醒工具，不是闸**。它回答一个问题——「这条补丁能不能落到正文上」。
     它不判断这本书好不好（那是 check_book.py 的事），也不阻止交付。

三种用法
  事中（推荐，写一条验一条）：
      python check_ledger.py <work> --one 06_corrections.json:1780
      → 只看这一条，当场判得了。有确定性缺陷返回 1，改完再写下一行。
  全量体检（写完整批之后看一眼）：
      python check_ledger.py <work> [--verbose]
      → 汇总报告，**永远 exit 0**。上千条积压错误是噪声，不阻断、不返工。
  崩溃防护（assemble 内部调用）：
      python check_ledger.py <work> --strict
      → 仅 schema ERROR（缺 new 键等会让 assemble KeyError 崩的确定性脏数据）返回 1；
        可落地性问题仍只报告。grounding 不阻断——补丁 old 找不到是台账的事，
        不该把整本书拦下来（详见下方「为什么 0 命中不阻断」）。

五类检查
  ① schema 契约：缺 old/new 键、类型错、phys 非法、status 非法、regex 不可编译、
     已废弃的 "deleted": true（工具只认 status:"deleted"，带该键会被当 active 施加）。
  ② 可落地性（grounding）：per-phys 补丁的 old 须在 body0 命中。
     body0 = struct 先施加全部 active systematic 通改后的正文（与 apply_corrections 同口径）。
     逐页补丁的 old 是「body0 形态」（§3.4），拿未通改的 raw struct 验会假阴性。
  ③ 冲突：同一 systematic old 或多个不同 new；同一 (phys, old) 多个不同 new。
  ④ 加字通改误伤：systematic 的 new 是 old 头/尾超串时，底本里 old 嵌在别的正确词内
     （「整个社会」含「个社会」）会被改坏；底本已是正确 new 处会被重复加字。
  ⑤ 形态（2026-10-19 增）：old==new 空转 / old 写成一行而底本跨行 / 同页两条 old 命中区间相交。
    三条都不影响「能不能改」，但都会让账与书对不上号——复核时会照着 old 去找却找不到。

为什么「0 命中」不阻断（2026-10-18）：
  0 命中只说明**这条补丁对不上正文**，可能三种原因——old 写成了通改前形态、phys 记错页、
  这一步原文本来就没错。三种都是**台账要修**，不是**书要重跑**。
  历史教训：把 0 命中做成硬闸后，一批书积压上千条错误，执行者为了交付只能想办法让它闭嘴，
  反而绕开了真正该做的事。所以此处只提示，处置权交执行者：改 old / 标 status:deleted / 确认无需。

退出码：0 = 无确定性缺陷；1 = 有（--one 与 --strict 模式下）；2 = 工作目录/台账缺失。
"""
import os
import re
import sys
import json
import glob
import argparse
from collections import defaultdict

from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

from _common import (  # noqa: E402
    read_text_cn, read_json_cn, expand_patch_data, compile_linebreak,
    iter_spans, find_span, _page_blocks, PUNCT_SET, check_systematic_overshoot,
    DESC_RE,
)

# 补丁字段契约（v1.34.x 立，取全库实测键频次）：未知键会被 apply_corrections 静默忽略，
# 补丁语义随之改变却不报错。事故：125 条用 "deleted": true 表达废弃，工具只认 status。
KNOWN_KEYS = {
    "old", "new", "phys", "reason", "type", "source", "systematic", "ocr", "silent",
    "status", "regex", "merge", "id", "kind", "book",
    "delete_reason", "deleted_reason", "delete_date", "deleted_date",
    "note", "notes", "verified", "count", "tail", "category", "fix_note",
    "total", "group", "date", "items", "doubt", "unit", "skipped", "by_conf",
    "revision",  # 人工定点修订标记（修订路径）
}
CORE_KEYS = ["old", "new", "phys", "reason", "systematic", "status", "regex",
             "silent", "ocr", "type", "source", "merge"]


# ============================ 加载 ============================

def load_entries(work):
    """加载全部补丁条目。

    返回 (active, deleted, corr_files)：
      active[i] = {old,new,phys,systematic,regex,silent,ocr,status,source,tag,raw}
    status="deleted" 的 tombstone 单独归入 deleted，不参与可落地性校验（本来就不施加）。
    """
    corr_files = sorted(glob.glob(os.path.join(work, "corrections", "*.json")))
    active, deleted = [], []
    for fp in corr_files:
        base = os.path.basename(fp)
        try:
            data = read_json_cn(fp)
        except Exception:
            try:
                data = [json.loads(l) for l in open(fp, encoding="utf-8-sig") if l.strip()]
            except Exception as e:
                active.append({"old": "", "new": "", "phys": "", "source": base,
                               "tag": f"{base}", "raw": {}, "parse_error": str(e)})
                continue
        for i, c in enumerate(expand_patch_data(data)):
            if not isinstance(c, dict):
                continue
            # 非校订标记（无 old/merge/phys）→ 跳过，兼容 _meta / section 类文件
            if "old" not in c and "merge" not in c and "phys" not in c:
                continue
            tag = f"{base}[{i}]"
            phys = c.get("phys")
            ent = {
                "old": c.get("old", ""),
                "new": c.get("new", ""),
                "phys": phys,
                "systematic": bool(c.get("systematic")) or str(phys).upper() == "ALL",
                "regex": bool(c.get("regex")),
                "silent": bool(c.get("silent")),
                "ocr": bool(c.get("ocr")),
                "merge": bool(c.get("merge")),
                "status": c.get("status", ""),
                "source": base,
                "tag": tag,
                "raw": c,
            }
            (deleted if c.get("status") == "deleted" else active).append(ent)
    return active, deleted, corr_files


# ============================ ① schema ============================

def check_schema(active, total_phys):
    """schema 契约校验。返回 (errors, warnings)。

    ERROR = 会让 assemble 崩溃或让补丁语义静默变形（缺 new 键 → KeyError；deleted:true → 被施加）。
    """
    errors, warnings = [], []
    for e in active:
        tag = e["tag"]
        c = e["raw"]
        if e.get("parse_error"):
            errors.append(f"{tag}: JSON 解析失败（既非合法 JSON 也非合法 JSONL）：{e['parse_error']}")
            continue
        # ---- 已废弃键 ----
        unknown = [k for k in c if k not in KNOWN_KEYS]
        if "deleted" in unknown:
            errors.append(f"{tag}: 字段 deleted 已废弃——工具只认 status:\"deleted\"，"
                          f"本补丁会被当作 active 施加（改格式或整条删除）")
            unknown = [k for k in unknown if k != "deleted"]
        for k in unknown:
            import difflib
            guess = difflib.get_close_matches(k, CORE_KEYS, n=1, cutoff=0.8)
            if guess:
                errors.append(f"{tag}: 字段名 {k!r} 疑似拼写错误（应为 {guess[0]!r}）——"
                              f"未知键会被静默忽略，补丁语义随之改变")
            else:
                warnings.append(f"{tag}: 字段 {k!r} 不在契约内（未知扩展，将被忽略）")
        # ---- merge 补丁 ----
        if e["merge"]:
            try:
                pnum = int(e["phys"])
                if pnum < 1 or (total_phys and pnum > total_phys):
                    errors.append(f"{tag}: merge 补丁 phys={e['phys']} 超出全书范围（1–{total_phys or '?'}）")
            except (TypeError, ValueError):
                errors.append(f"{tag}: merge 补丁 phys={e['phys']!r} 非整数（须为具体页码）")
            continue
        # ---- 普通 / systematic 补丁 ----
        if "old" not in c:
            errors.append(f"{tag}: 缺 old 字段（无法定位片段）")
            continue
        if not isinstance(e["old"], str):
            errors.append(f"{tag}: old 非字符串类型（{type(e['old']).__name__}）")
            continue
        if "new" not in c:
            errors.append(f"{tag}: 缺 new 键。删除型须显式写 \"new\": \"\"；"
                          f"空着会让 apply_corrections 读 c[\"new\"] 时 KeyError 崩溃")
            continue
        if not isinstance(e["new"], str):
            errors.append(f"{tag}: new 非字符串类型（{type(e['new']).__name__}）")
            continue
        if not e["old"]:
            errors.append(f"{tag}: old 为空字符串（无法定位唯一片段，等价全书级误替换）")
            continue
        # 布尔字段类型
        for bf in ("systematic", "regex", "silent", "ocr"):
            if bf in c and not isinstance(c[bf], bool):
                errors.append(f"{tag}: 字段 {bf}={c[bf]!r} 非布尔（须 true/false）")
        # status 枚举
        if "status" in c and c["status"] not in ("active", "deleted"):
            errors.append(f"{tag}: status={c['status']!r} 非法（须 active/deleted）")
        # 正则可编译
        if e["regex"]:
            try:
                re.compile(e["old"])
            except re.error as ex:
                errors.append(f"{tag}: regex:true 但 old「{e['old']}」无法编译（{ex}）")
                continue
        # phys 合法性
        if e["systematic"]:
            if str(e["phys"]).upper() not in ("ALL", "NONE"):
                warnings.append(f"{tag}: systematic:true 但 phys={e['phys']!r}（非 ALL）")
        else:
            try:
                pnum = int(e["phys"])
                if pnum < 1 or (total_phys and pnum > total_phys):
                    errors.append(f"{tag}: phys={e['phys']} 超出全书范围（1–{total_phys or '?'}）")
            except (TypeError, ValueError):
                errors.append(f"{tag}: phys={e['phys']!r} 非整数（逐页补丁须为具体页码）")
        # 裸单字 systematic（易全书误替换）
        if e["systematic"] and not e["regex"] and len(e["old"]) < 2:
            warnings.append(f"{tag}: 裸单字 systematic「{e['old']}」易全书误替换，改复合补丁")
        # 描述性文字（把解释写进 old）
        if not e["regex"] and DESC_RE.search(e["old"]):
            warnings.append(f"{tag}: old「{e['old']}」疑似描述性文字（非原文连续片段），可能命不中")
    return errors, warnings


# ============================ ② 可落地性 ============================

def build_body0(struct, active):
    """struct 施加全部 active systematic 通改 → body0（逐页补丁 old 的判定基准）。

    与 apply_corrections 同口径：裸 sub，不做 yield 让位（那是施加侧行为，影响产出，
    不影响「这条 old 在通改后正文里找不找得到」这个判断）。
    同 old 多 new 的冲突项不参与构造（取不到唯一形态，交给冲突项单独报）。
    """
    sys_map = defaultdict(set)
    for e in active:
        if e["systematic"] and e["old"] and not e["merge"]:
            sys_map[(e["old"], e["regex"])].add(e["new"])
    body = struct
    for (old, is_rx), news in sys_map.items():
        if len(news) != 1:
            continue
        new = next(iter(news))
        try:
            rx = re.compile(old) if is_rx else compile_linebreak(old)
            body = rx.sub((lambda m, n=new: m.expand(n)) if is_rx else new, body)
        except re.error:
            continue
    return body


def check_grounding(active, body0, struct, blocks, total_phys):
    """可落地性：per-phys old 须在 body0 命中。

    返回 (errors, redundant, multi)：
      errors     — 0 命中且 new 也不在（old 写错 / phys 记错 / 该处本不需补丁）
      redundant  — 0 命中但 new 已在（已被 systematic 代劳，建议 status:deleted）
      multi      — 命中 >1 处（old 太短，偏页风险）
    """
    errors, redundant, multi = [], [], []
    for e in active:
        if e["systematic"] or e["merge"] or e["regex"]:
            continue  # systematic 对底本验手在 check_overshoot；正则型只判可编译
        old, new = e["old"], e["new"]
        if not old:
            continue
        spans = iter_spans(body0, old)
        if not spans:
            if new and find_span(body0, new):
                redundant.append(e)
            else:
                hint = ""
                # phys 是否误用了 book 印页（book 常比 phys 小一个 offset）
                try:
                    pnum = int(e["phys"])
                    for off in range(5, 16):
                        blk = blocks.get(pnum + off)
                        if blk and find_span(blk, old):
                            hint = f"；old 实际在 phys{pnum + off}（偏移+{off}），疑似 phys 用了印页"
                            break
                except (TypeError, ValueError):
                    pass
                errors.append((e, hint))
        elif len(spans) > 1:
            multi.append((e, len(spans)))
    return errors, redundant, multi


# ============================ ③ 冲突 ============================

def check_conflicts(active):
    """同一 old 多个 new（systematic）；同一 (phys, old) 多个 new（逐页）。"""
    sys_map, per_map = defaultdict(set), defaultdict(set)
    for e in active:
        if e["merge"] or not e["old"]:
            continue
        if e["systematic"]:
            sys_map[e["old"]].add(e["new"])
        else:
            try:
                per_map[(int(e["phys"]), e["old"])].add(e["new"])
            except (TypeError, ValueError):
                pass
    out = []
    for old, news in sys_map.items():
        if len(news) > 1:
            out.append((f"通改「{old}」", sorted(news)))
    for (pnum, old), news in per_map.items():
        if len(news) > 1:
            out.append((f"phys{pnum}「{old}」", sorted(news)))
    return out


# ============================ ④ 加字通改误伤 ============================

def check_overshoot(active, struct):
    """systematic 加字型通改对底本的损伤 + 通改在本书 0 命中。"""
    hurt, dead = [], []
    seen = set()
    for e in active:
        if not e["systematic"] or e["merge"] or not e["old"] or not e["new"]:
            continue
        key = (e["old"], e["new"])
        if key in seen:
            continue
        seen.add(key)
        if e["regex"]:
            try:
                if not re.search(e["old"], struct):
                    dead.append(e)
            except re.error:
                pass
            continue
        if not compile_linebreak(e["old"]).search(struct):
            dead.append(e)
            continue
        n_over, n_dup, samples = check_systematic_overshoot(e["old"], e["new"], struct)
        if n_over or n_dup:
            hurt.append((e, n_over, n_dup, samples))
    return hurt, dead


# ============================ ⑤ 台账形态 ============================

def check_ledger_shape(active, blocks):
    """三条形态检查（都不阻断，都是「账与底本对不上形态」的信号）：

      idle      old == new：这条补丁不产生任何改动。占一条账、在 04 里刷一行，
                还让人以为改过了。典型成因是同一处被两条补丁先后覆盖、后一条被写成等值。
      flat      old 写成一行、底本此处跨行：old 里没有换行，逐字与「仅容忍换行」都命不中，
                只有把空白全抹掉才命中。东方主义 9 条 0 命中即此类——补丁能用，
                但按 old 去正文里搜会搜不到，人工复核与 04 叙述都对不上。
      overlap   同页两条 old 的命中区间相交（部分重叠或一条包含另一条）：assemble 按
                「长 old 优先」静默丢弃短的一条，账上却两条都在，事后无从判断哪条真生效。
    """
    idle, flat, overlap = [], [], []
    per_phys = defaultdict(list)
    for e in active:
        if e["merge"] or not e["old"]:
            continue
        if e["old"] == e["new"]:
            idle.append(e)
        if e["systematic"]:
            continue
        try:
            pnum = int(e["phys"])
        except (TypeError, ValueError):
            continue
        per_phys[pnum].append(e)

    for pnum, lst in per_phys.items():
        blk = blocks.get(pnum, "")
        if not blk:
            continue
        spans = []
        for e in lst:
            old = e["old"]
            if not find_span(blk, old):          # 完全命不中的归 ② 0 命中，这里只判形态
                continue
            if "\n" not in old and not (old in blk or compile_linebreak(old).search(blk)):
                flat.append(e)
            for (s, t) in iter_spans(blk, old):
                spans.append((s, t, e))
        spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
        for i in range(len(spans)):
            s, t, cur = spans[i]
            for j in range(i):
                ps, pt, prev = spans[j]
                if prev is cur:
                    continue
                if ps < t and s < pt:
                    overlap.append((pnum, prev, cur, blk[max(ps, s):min(pt, t)][:24]))
                    break
    return idle, flat, overlap


# ============================ 报告 ============================

def _short(e, n=60):
    old = e["old"] if len(e["old"]) <= n else e["old"][:n] + "…"
    return f"{e['tag']} phys={e['phys']} 「{old}」"


def main():
    ap = argparse.ArgumentParser(description="台账可落地性校验（提醒工具，非闸）")
    ap.add_argument("work", help="工作目录（含 corrections/*.json）")
    ap.add_argument("--one", default="", metavar="FILE[:IDX]",
                    help="只校验一份台账文件（可带 :序号）——事中写一条验一条")
    ap.add_argument("--strict", action="store_true",
                    help="schema ERROR 时返回 1（供 assemble 崩溃防护）；可落地性问题仍只报告")
    ap.add_argument("--verbose", action="store_true", help="打印全部明细（默认摘要 + 前 20 条）")
    args = ap.parse_args()
    B = args.work

    if not os.path.isdir(B):
        print(f"❌ 工作目录不存在：{B}")
        return 2
    corr_dir = os.path.join(B, "corrections")
    if not os.path.isdir(corr_dir):
        print(f"❌ 未找到 corrections/（台账目录）：{corr_dir}")
        return 2

    active, deleted, corr_files = load_entries(B)
    if not corr_files:
        print("无 corrections/*.json，跳过校验（03 将等于未纠错的结构清洗稿）")
        return 0

    struct_path = os.path.join(B, "fulltext_struct.txt")
    if not os.path.isfile(struct_path):
        print(f"❌ 未找到 fulltext_struct.txt（可落地性无法校验）：{struct_path}")
        return 2
    struct = read_text_cn(struct_path)

    total_phys = None
    meta_path = os.path.join(B, "book_meta.json")
    if os.path.isfile(meta_path):
        try:
            total_phys = read_json_cn(meta_path).get("total_phys")
        except Exception:
            pass

    # --one 只留目标文件的条目（body0 仍用全量 systematic 构造，否则逐页 old 基准会偏）
    target_file, target_idx = "", None
    if args.one:
        if ":" in args.one:
            target_file, _idx = args.one.rsplit(":", 1)
            target_idx = int(_idx) if _idx.isdigit() else None
        else:
            target_file = args.one
        target_file = os.path.basename(target_file)

    def _sel(ents):
        if not args.one:
            return ents
        out = []
        for e in ents:
            if e["source"] != target_file:
                continue
            if target_idx is not None:
                m = re.search(r"\[(\d+)\]$", e["tag"])
                if not m or int(m.group(1)) != target_idx:
                    continue
            out.append(e)
        return out

    sel_active = _sel(active)

    print("=" * 68)
    mode = "事中单条" if args.one else ("全量(阻断 schema)" if args.strict else "全量体检")
    print(f"check_ledger · {mode}：{len(corr_files)} 份台账 → active {len(active)} / deleted {len(deleted)}")
    if args.one:
        print(f"目标：{target_file}" + (f"[{target_idx}]" if target_idx is not None else "（全文件）")
              + f" → 选中 {len(sel_active)} 条")
    print("=" * 68)

    schema_err, schema_warn = check_schema(sel_active, total_phys)
    body0 = build_body0(struct, active)
    blocks = _page_blocks(body0)
    g_err, g_redun, g_multi = check_grounding(sel_active, body0, struct, blocks, total_phys)
    conflicts = check_conflicts(sel_active)
    hurt, dead = check_overshoot(sel_active, struct)
    idle, flat, overlap = check_ledger_shape(sel_active, blocks)

    # ---- ① schema ----
    if schema_err:
        print(f"\n【① schema ERROR】{len(schema_err)} 条 —— 会让 assemble 崩溃或补丁语义静默变形")
        for s in (schema_err if args.verbose else schema_err[:20]):
            print(f"  ❌ {s}")
        if not args.verbose and len(schema_err) > 20:
            print(f"  …（共 {len(schema_err)} 条，加 --verbose 看全部）")
    if schema_warn:
        # 2026-10-19：以前只报计数，维民 WARN 1 条无从处置。WARN 本身不痛不痒，
        # 但它指向的写法偏差（phys 非 ALL、裸单字、描述性 old）会让人在错的地方找原因，
        # 所以要能看到具体是哪一条。
        print(f"\n【① schema WARN】{len(schema_warn)} 条 —— 不阻断；写法偏离契约，逐条看")
        for s in (schema_warn if args.verbose else schema_warn[:20]):
            print(f"  ⚠ {s}")
        if not args.verbose and len(schema_warn) > 20:
            print(f"  …（共 {len(schema_warn)} 条，加 --verbose 看全部）")

    # ---- ② 可落地性 ----
    if g_err:
        print(f"\n【② 可落地性 · 0 命中】{len(g_err)} 条 —— 补丁对不上正文")
        for e, hint in (g_err if args.verbose else g_err[:20]):
            print(f"  · {_short(e)}{hint}")
        if not args.verbose and len(g_err) > 20:
            print(f"  …（共 {len(g_err)} 条，加 --verbose 看全部）")
        print("  说明：0 命中通常不是书的错，而是台账的一种偏差——old 写成了通改前形态、"
              "phys 记错页、或这一步原文本就没错。\n"
              "        处置：改 old 为通改后形态 / 标 \"status\":\"deleted\" / 确认此处无需补丁。"
              "不要为了让它消失而改计数或重跑全书。")
    if g_redun:
        print(f"\n【② 可落地性 · 冗余】{len(g_redun)} 条 —— old 已不在，new 已在（systematic 已代劳）")
        for e in (g_redun if args.verbose else g_redun[:10]):
            print(f"  · {_short(e)} → 建议 \"status\":\"deleted\"")
        if not args.verbose and len(g_redun) > 10:
            print(f"  …（共 {len(g_redun)} 条，加 --verbose 看全部）")
    if g_multi:
        print(f"\n【② 可落地性 · 多命中】{len(g_multi)} 条 —— old 太短，可能偏页")
        for e, n in (g_multi if args.verbose else g_multi[:10]):
            print(f"  · {_short(e)} 命中 {n} 处 → old 加 ≥6 字上下文使之唯一")
        if not args.verbose and len(g_multi) > 10:
            print(f"  …（共 {len(g_multi)} 条，加 --verbose 看全部）")

    # ---- ③ 冲突 ----
    if conflicts:
        print(f"\n【③ 冲突】{len(conflicts)} 组 —— 同一 old 有多个不同 new，须人工仲裁")
        for who, news in conflicts:
            print(f"  · {who} → " + " / ".join(f"「{n}」" for n in news))

    # ---- ④ 加字通改误伤 ----
    if hurt:
        print(f"\n【④ 加字通改误伤】{len(hurt)} 条 —— 加字型通改会改坏正确的词或重复加字")
        for e, n_over, n_dup, samples in hurt:
            ex = "；".join(f"{k}（「{c}」，边界「{a}」）" for k, c, a in samples)
            print(f"  · {e['tag']}「{e['old']}」→「{e['new']}」："
                  f"误伤正确词 {n_over} 处 / 重复加字 {n_dup} 处（{ex}）")
            print(f"    处置：改逐页补丁，或给 old 加上下文使全书唯一命中（中文无词边界，误伤类须人工复核）")
    if dead:
        print(f"\n【④ 通改本书 0 命中】{len(dead)} 条 —— 该通改在本书不适用")
        for e in (dead if args.verbose else dead[:10]):
            print(f"  · {e['tag']}「{e['old']}」→「{e['new']}」→ 建议置 deleted 或移出本书台账")
        if not args.verbose and len(dead) > 10:
            print(f"  …（共 {len(dead)} 条，加 --verbose 看全部）")

    # ---- ⑤ 台账形态 ----
    if idle:
        print(f"\n【⑤ 形态 · 空转】{len(idle)} 条 —— old == new，这条补丁不产生任何改动")
        for e in (idle if args.verbose else idle[:10]):
            print(f"  · {_short(e)}")
        if not args.verbose and len(idle) > 10:
            print(f"  …（共 {len(idle)} 条，加 --verbose 看全部）")
        print("  处置：多半是废补丁（被别的补丁覆盖后写成等值），标 \"status\":\"deleted\"。")
    if flat:
        print(f"\n【⑤ 形态 · 扁平】{len(flat)} 条 —— old 写成一行，底本此处跨行")
        for e in (flat if args.verbose else flat[:10]):
            print(f"  · {_short(e)}")
        if not args.verbose and len(flat) > 10:
            print(f"  …（共 {len(flat)} 条，加 --verbose 看全部）")
        print("  说明：补丁能施加（匹配会容忍空白），但按 old 去正文里搜搜不到，"
              "人工复核与 04 叙述都对不上。\n"
              "        处置：把 old 改成含真实换行的形态，或缩短到不跨行的片段。")
    if overlap:
        print(f"\n【⑤ 形态 · 同页重叠】{len(overlap)} 组 —— 同页两条 old 命中区间相交")
        for pnum, prev, cur, seg in overlap:
            print(f"  · phys{pnum}「{prev['old'][:22]}」×「{cur['old'][:22]}」重叠于「{seg}」")
        print("  说明：assemble 按「长 old 优先」静默丢弃短的一条，账上却两条都在。\n"
              "        处置：合并成一条，或把短的那条标 deleted。")

    # ---- 摘要 ----
    print("\n" + "-" * 68)
    print(f"schema ERROR {len(schema_err)} / schema WARN {len(schema_warn)}"
          f" | 0 命中 {len(g_err)}（冗余 {len(g_redun)} / 多命中 {len(g_multi)}）"
          f" | 冲突 {len(conflicts)} | 加字误伤 {len(hurt)} / 通改0命中 {len(dead)}"
          f" | 形态 空转 {len(idle)} / 扁平 {len(flat)} / 重叠 {len(overlap)}")
    print("-" * 68)

    # ---- 退出码 ----
    if args.one:
        bad = bool(schema_err) or bool(g_err)
        if bad:
            print("❌ 这一条有确定性缺陷，当场改掉再写下一行（改台账，不是改书）。")
            return 1
        print("✅ 这一条可落地。")
        return 0
    if args.strict and schema_err:
        print("❌ schema ERROR 会让 assemble 崩溃，先修台账。")
        return 1
    if schema_err or g_err or conflicts or hurt or idle or flat or overlap:
        print("以上为体检报告：**不阻断交付**。按提示逐条处置台账即可（0 命中 ≠ 书要重跑）。")
    else:
        print("✅ 台账干净：每条补丁都能落到正文上。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
