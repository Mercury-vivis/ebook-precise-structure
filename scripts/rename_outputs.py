#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rename_outputs.py — 对交付目录内已有文件批量加前缀（v1.15.1 简化版）。

【存量专用 · 新流程不使用】pipeline_assemble / assemble_epub 已一步到位带交付
目录名前缀（SKILL §0.2 收尾命名行：「五件统一，无需 rename」）。本脚本仅用于
历史交付目录（存量）事后补前缀，新流程不要调用。

前缀推导统一走 _common.derive_prefix（交付目录名，去 _OCR/_文字版等加工标注）——
包内唯一真相源，本脚本不含第二份实现。

用法：
  python rename_outputs.py <交付目录> [--apply]
  默认 dry-run：只打印计划改名，不落盘；加 --apply 才真正执行。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import derive_prefix  # v1.15.1：前缀=目录名（去_OCR/_文字版标注），五件统一


def plan_renames(folder: str):
    prefix = derive_prefix(folder)
    actions = []
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        if not os.path.isfile(full):
            continue  # 仅处理文件，不动子目录
        if name.startswith(prefix):
            continue  # 去重保护：已带前缀则跳过
        new_name = prefix + name
        actions.append((name, new_name))
    return prefix, actions


def main():
    if len(sys.argv) < 2:
        print("用法: python rename_outputs.py <交付目录> [--apply]")
        sys.exit(1)
    folder = sys.argv[1]
    apply = "--apply" in sys.argv
    if not os.path.isdir(folder):
        print(f"错误：目录不存在：{folder}", file=sys.stderr)
        sys.exit(1)

    prefix, actions = plan_renames(folder)
    print(f"交付目录：{folder}")
    print(f"推导前缀：{prefix}")
    print(f"待改名文件：{len(actions)} 个")
    for old, new in actions:
        print(f"  {old}  →  {new}")
    if not actions:
        print("（全部已带前缀，无需改名）")
    if apply and actions:
        done = 0
        skipped = 0
        for old, new in actions:
            src = os.path.join(folder, old)
            dst = os.path.join(folder, new)
            if os.path.exists(dst):
                # 目标已存在：绝不静默覆盖（防误删/覆盖），跳过并告警
                print(f"  ⚠ 跳过（目标已存在，避免覆盖）：{old} → {new}")
                skipped += 1
                continue
            os.rename(src, dst)
            done += 1
        print(f"\n已执行 {done} 个改名"
              + (f"，跳过 {skipped} 个（目标已存在）" if skipped else ""))
    elif not apply:
        print("\n（dry-run，未执行；加 --apply 落盘）")


if __name__ == "__main__":
    main()
