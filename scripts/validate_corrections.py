#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【已被 check_ledger.py --strict（schema 契约并入统一入口；仍会在 assemble 前被调用，但不再单独挂主链） 取代（2026-10-18）】本文件保留供历史核对，新流程不要调用。
validate_corrections.py —— 改造6（P1）：corrections/*.json 的**严格 schema 校验**

定位：assemble 加载补丁**之前**的硬闸（加载前调用，exit(1) 阻断脏数据）。
与 check_patch.py（启发式预检：struct 命中 / 冲突 / 描述性文字 / silent 误用）互补——
本脚本只校验「schema 契约」本身：

  [ERROR]（阻断 assemble）
    - 缺 old / 缺 new 键（删除型须显式 "new": ""；缺键会让 apply_corrections 读取 c["new"] 时 KeyError 崩）
    - old / new 非字符串类型
    - per-phys 补丁 phys 非整数 / <1（无法定位唯一物理页）
    - merge 补丁 phys 非整数 / <1
    - status 字段存在但非 "active"/"deleted"
    - systematic / regex / silent / ocr 字段存在但非布尔
    - regex:true 但 old 无法 re.compile
    - 存在字段 "deleted"（已废弃：工具只认 status:"deleted"，带该键的补丁会被静默当 active 施加）
    - 未知字段名与核心键高度相似（疑似拼写错误，如 sysmatic；未知键会被静默忽略）
  [WARN ]（不阻断，仅提示）
    - 未知扩展字段（不在补丁字段契约内，将被忽略）
    - systematic 但 phys 非 ALL（系统噪声通改应 phys=ALL）
    - 裸单字 systematic（len(old)==1，非 regex）：需 --force-systematic 放行或改复合补丁
    - old 含描述性文字（可能命不中）

退出码：0 = 通过（仅 WARN 或无问题）；1 = 存在 ERROR（须修复再 assemble）。
注意：corrections/ 目录无 *.json 时（即"零补丁"）打印告警并 return 0（不阻断）——此时 03 将等于未纠错结构清洗稿。

用法：python validate_corrections.py <WORK目录> [--verbose]
"""

import json, glob, os, re, sys, argparse, difflib
from _common import DESC_RE, expand_patch_data  # 2026-10-04 审查 P2-14：与 check_patch 共用同一正則；expand_patch_data=2026-10-05 漂移收敛

# ---- 补丁字段契约（2026-10-17 立）----
# 取值为全库 1705 份 corrections / 54863 条补丁的实测键频次（Books_final 全量扫），
# 只收「补丁级」键；文件级容器键（patches/candidates/total_corrections 等）不在其列。
# 用途：未知键会被 apply_corrections 静默忽略，补丁语义随之改变而不报错——
# 事故实况：125 条用 "deleted": true 表达废弃，工具只认 status:"deleted"，
# 这些补丁照常被施加，validate 却 error=0 放行。
KNOWN_KEYS = {
    "old", "new", "phys", "reason", "type", "source", "systematic", "ocr", "silent",
    "status", "regex", "merge", "id", "kind", "book",
    "delete_reason", "deleted_reason", "delete_date", "deleted_date",
    "note", "notes", "verified", "count", "tail", "category", "fix_note",
    "total", "group", "date", "items", "doubt", "unit", "skipped", "by_conf",
    "revision",  # 人工定点修订标记（修订路径）
}
# 拼写错误判据只在核心键上做（其余扩展键拼错，影响面小、报 WARN 即可）
CORE_KEYS = ["old", "new", "phys", "reason", "systematic", "status", "regex", "silent", "ocr", "type", "source", "merge"]


def _load_patches(cf):
    """容错加载单份 corrections 文件 → list[dict]（BOM + JSONL 回退）。"""
    try:
        data = json.load(open(cf, encoding="utf-8-sig"))
    except json.JSONDecodeError:
        try:
            data = [json.loads(l) for l in open(cf, encoding="utf-8-sig") if l.strip()]
        except Exception as e:
            return None, f"JSON 解析失败（既非合法 JSON 也非合法 JSONL）：{e}"
    if not isinstance(data, (dict, list)):
        return None, "顶层既非 list 也非含 patches 的对象"
    # 三形态归一（2026-10-05：收敛到 _common.expand_patch_data 单一真相源）
    data = expand_patch_data(data)
    return data, None


# DESC_RE 已收敛到 _common（2026-10-04 审查 P2-14），本文件通过顶部 import 引用。


def main():
    ap = argparse.ArgumentParser(description="corrections/*.json 严格 schema 校验（assemble 前置硬闸）")
    ap.add_argument("work", help="工作目录（含 corrections/*.json）")
    ap.add_argument("--verbose", action="store_true", help="打印每条 WARN 明细")
    args = ap.parse_args()
    B = args.work

    corr_files = sorted(glob.glob(os.path.join(B, "corrections", "*.json")))
    if not corr_files:
        print("无 corrections/*.json，跳过 schema 校验（03 将等于未纠错结构清洗稿）")
        return 0

    errors, warnings = [], []
    warn_detail = []

    for cf in corr_files:
        base = os.path.basename(cf)
        data, err = _load_patches(cf)
        if err:
            errors.append(f"{base}: {err}")
            continue
        for i, c in enumerate(data):
            if not isinstance(c, dict):
                continue
            tag = f"{base}[{i}]"
            # 非校订标记（无 old/merge/phys）→ 跳过（兼容 _meta / section 类文件）
            if "old" not in c and "merge" not in c and "phys" not in c:
                continue
            # ---- merge 补丁 ----
            if c.get("merge"):
                try:
                    pnum = int(c.get("phys"))
                    if pnum < 1:
                        errors.append(f"{tag}: merge 补丁 phys={pnum} 须 ≥1")
                except (TypeError, ValueError):
                    errors.append(f"{tag}: merge 补丁 phys={c.get('phys')!r} 非整数（须为具体页码）")
                continue
            # N6（2026-10-04 审查）：deleted tombstone 已声明不施加，跳过完整 schema 校验，
            # 与 check_patch.py 行为对齐（避免对"不施加补丁"误报 ERROR）。
            if c.get("status") == "deleted":
                continue

            # ---- 字段契约校验（2026-10-17 立，见顶部 KNOWN_KEYS 的由来）----
            # 只对「会被施加的补丁」校验：status=deleted 的 tombstone 不施加，多余键无害。
            _unknown = [k for k in c if k not in KNOWN_KEYS]
            if "deleted" in _unknown:
                errors.append(
                    f"{tag}: 字段 deleted 已废弃——工具不识别它，本补丁会被当作 active 正常施加。"
                    f"改用 \"status\": \"deleted\"（old/new 保留），或整条删除。")
                _unknown = [k for k in _unknown if k != "deleted"]
            for k in _unknown:
                _guess = difflib.get_close_matches(k, CORE_KEYS, n=1, cutoff=0.8)
                if _guess:
                    errors.append(
                        f"{tag}: 字段名 {k!r} 疑似拼写错误（应为 {_guess[0]!r}）——"
                        f"未知键会被静默忽略，补丁语义随之改变")
                else:
                    warnings.append(f"{tag}: 字段 {k!r} 不在契约内（未知扩展，将被忽略）")
                    warn_detail.append(f"{tag}: 未知字段 {k!r}")

            # ---- 普通 / systematic 补丁 ----
            if "old" not in c:
                errors.append(f"{tag}: 缺 old 字段（无法定位片段）")
                continue
            old = c["old"]
            if not isinstance(old, str):
                errors.append(f"{tag}: old 非字符串类型（{type(old).__name__}），须为 str")
                continue
            if "new" not in c:
                errors.append(f"{tag}: 缺 new 键。删除型补丁须显式写 \"new\": \"\"；"
                              f"空着会让 apply_corrections 读 c[\"new\"] 时 KeyError 崩溃。")
                continue
            new = c["new"]
            if not isinstance(new, str):
                errors.append(f"{tag}: new 非字符串类型（{type(new).__name__}），须为 str")
                continue
            if not old:
                errors.append(f"{tag}: old 为空字符串（无法定位唯一片段，等价全书级误替换）")
                continue
            systematic = bool(c.get("systematic")) or str(c.get("phys", "")).upper() == "ALL"
            is_regex = bool(c.get("regex"))
            # 布尔字段类型校验
            for bf in ("systematic", "regex", "silent", "ocr"):
                if bf in c and not isinstance(c[bf], bool):
                    errors.append(f"{tag}: 字段 {bf}={c[bf]!r} 非布尔（须 true/false）")
            # status 枚举校验
            if "status" in c and c["status"] not in ("active", "deleted"):
                errors.append(f"{tag}: status={c['status']!r} 非法（须 active/deleted）")
            # 正则型 old 可编译
            if is_regex:
                try:
                    re.compile(old)
                except re.error as e:
                    errors.append(f"{tag}: regex:true 但 old「{old}」无法编译（{e}）")
            # phys 合法性
            if systematic:
                if c.get("phys") not in (None, "ALL", "all", "All") and str(c.get("phys", "")).upper() != "ALL":
                    warnings.append(f"{tag}: systematic:true 但 phys={c.get('phys')}（非 ALL），系统噪声通改应 phys=ALL")
                    warn_detail.append(f"{tag}: systematic 但 phys 非 ALL")
            else:
                try:
                    pnum = int(c.get("phys"))
                    if pnum < 1:
                        errors.append(f"{tag}: per-phys 补丁 phys={pnum} 须 ≥1")
                except (TypeError, ValueError):
                    errors.append(f"{tag}: per-phys 补丁 phys={c.get('phys')!r} 非整数（须为具体页码）")
            # 裸单字 systematic 告警
            if systematic and not is_regex and len(old) < 2:
                warnings.append(f"{tag}: 裸单字 systematic「{old}」易全书误替换，须 --force-systematic 放行或改复合补丁")
                warn_detail.append(f"{tag}: 裸单字 systematic「{old}」")
            # 描述性文字告警
            if not is_regex and DESC_RE.search(old):
                warnings.append(f"{tag}: old「{old}」疑似描述性文字（非原文连续片段），可能命不中")
                warn_detail.append(f"{tag}: old 含描述性文字")

    sep = "=" * 64
    print(sep)
    print(f"validate_corrections：{len(corr_files)} 份 → error={len(errors)} / warn={len(warnings)}")
    print(sep)
    for e in errors:
        print(f"[ERROR  ] {e}")
    if args.verbose:
        for w in warn_detail:
            print(f"[WARN   ] {w}")
    elif warnings:
        print(f"[WARN   ] 共 {len(warnings)} 条（加 --verbose 看明细）")
    print(sep)
    if errors:
        print("存在 schema ERROR，须修复后再 assemble（脏数据会被静默混入 03）。")
        return 1
    print("schema 校验通过（可 assemble）。")
    return 0


RETIRED = (
    "❌ validate_corrections.py 已退场（2026-10-18 schema 契约并入 check_ledger.py）。\n"
    "   请用：python check_ledger.py <work> --strict\n"
    "   （--strict 仅对 schema ERROR 返回 1，与 assemble 的崩溃防护同口径；其余只报告。）\n"
    "   本文件不再接受命令行调用。"
)


if __name__ == "__main__":
    print(RETIRED)
    sys.exit(2)
