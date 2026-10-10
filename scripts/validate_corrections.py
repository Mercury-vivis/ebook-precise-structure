#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
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
  [WARN ]（不阻断，仅提示）
    - systematic 但 phys 非 ALL（系统噪声通改应 phys=ALL）
    - 裸单字 systematic（len(old)==1，非 regex）：需 --force-systematic 放行或改复合补丁
    - old 含描述性文字（可能命不中）

退出码：0 = 通过（仅 WARN 或无问题）；1 = 存在 ERROR（须修复再 assemble）。
注意：corrections/ 目录无 *.json 时（即"零补丁"）打印告警并 return 0（不阻断）——此时 03 将等于未纠错结构清洗稿。

用法：python validate_corrections.py <WORK目录> [--verbose]
"""

import json, glob, os, re, sys, argparse
from _common import DESC_RE, expand_patch_data  # 2026-10-04 审查 P2-14：与 check_patch 共用同一正則；expand_patch_data=2026-10-05 漂移收敛


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


if __name__ == "__main__":
    sys.exit(main())
