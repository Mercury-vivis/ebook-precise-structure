#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_environment.py —— 环境自检

依赖清单的单一真相源是技能包根的 `skill-dependencies.json`；本脚本只消费它，
不在代码里另写一份包名（改依赖只需改 json）。新增脚本引入新第三方库时，
同步往 json 里登记，本脚本自动纳入检查。

用法：
    python scripts/check_environment.py                # 全量自检
    python scripts/check_environment.py --group epub   # 只查某一组
    python scripts/check_environment.py --json         # 机器可读输出

退出码：
    0  必需依赖齐备（可选依赖缺失只告警，不影响主流程）
    1  有必需依赖缺失
    2  依赖声明文件缺失 / 不可解析
"""

import argparse
import importlib.util
import json
import os
import sys

# ---- 解释器守卫（v1.30.0）：自检必须跑在 EBOOK_PY 指定的 venv 上，结果才有意义 ----
# 否则拿托管解释器自检会误报缺失、诱发「反复补装」。详见 _interp_guard.py。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

DEP_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skill-dependencies.json")
)


def probe(import_name):
    """探测模块可否导入（只查 spec，不执行模块，避免导入开销与副作用）。"""
    try:
        return importlib.util.find_spec(import_name) is not None
    except (ImportError, ValueError):
        return False


def read_decl():
    with open(DEP_FILE, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="ebook-precise-structure 环境自检")
    ap.add_argument("--group", help="只检查指定组 id（见 skill-dependencies.json 的 groups[].id）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出，便于程序消费")
    args = ap.parse_args()

    if not os.path.isfile(DEP_FILE):
        print(f"[ERROR] 依赖声明文件缺失：{DEP_FILE}", file=sys.stderr)
        return 2
    try:
        dep = read_decl()
    except (ValueError, UnicodeDecodeError) as e:
        print(f"[ERROR] 依赖声明解析失败：{e}", file=sys.stderr)
        return 2

    groups = dep.get("groups", [])
    if args.group:
        wanted = {g.strip() for g in args.group.split(",")}
        groups = [g for g in groups if g.get("id") in wanted]
        if not groups:
            print(f"[ERROR] 未找到组：{args.group}", file=sys.stderr)
            return 2

    results = []
    for g in groups:
        gid = g.get("id", "?")
        title = g.get("title", gid)
        grp_required = bool(g.get("required"))
        items = []
        for p in g.get("packages", []):
            name = p.get("import_name")
            if not name:
                continue
            ok = probe(name)
            pkg_optional = bool(p.get("optional"))
            items.append({
                "import_name": name,
                "pip": p.get("pip", name),
                "ok": ok,
                "optional": pkg_optional,
                "note": p.get("note", ""),
            })
        results.append({
            "id": gid,
            "title": title,
            "required": grp_required,
            "used_by": g.get("used_by", []),
            "note": g.get("note", ""),
            "packages": items,
        })

    missing_required = []
    missing_optional = []
    for r in results:
        for p in r["packages"]:
            if p["ok"]:
                continue
            is_required = r["required"] and not p["optional"]
            (missing_required if is_required else missing_optional).append(p)

    if args.json:
        print(json.dumps({
            "python": sys.version.split()[0],
            "python_exe": sys.executable,
            "groups": results,
            "missing_required": [p["import_name"] for p in missing_required],
            "missing_optional": [p["import_name"] for p in missing_optional],
        }, ensure_ascii=False, indent=2))
        return 1 if missing_required else 0

    print(f"ebook-precise-structure 环境自检")
    print(f"Python {sys.version.split()[0]}  @ {sys.executable}")
    print("=" * 62)
    for r in results:
        tag = "必装" if r["required"] else "可选"
        print(f"\n[{tag}] {r['title']}  (id={r['id']})")
        if r["used_by"]:
            print(f"       用于：{', '.join(r['used_by'])}")
        for p in r["packages"]:
            mark = "✔" if p["ok"] else ("✘" if not p["optional"] else "·")
            state = "已装" if p["ok"] else ("缺失" if not p["optional"] else "缺失(可降级)")
            print(f"       {mark} {p['import_name']:<24} {state:<12} pip: {p['pip']}")
            if not p["ok"] and p["note"]:
                print(f"         ↳ {p['note']}")
        if r["note"]:
            print(f"       注：{r['note']}")

    print("\n" + "=" * 62)
    if missing_required:
        print(f"✘ 必需依赖缺失 {len(missing_required)} 项，主流程会中断：")
        pip_list = " ".join(sorted({p["pip"] for p in missing_required}))
        print(f"    pip install {pip_list}")
    else:
        print("✔ 必需依赖齐备")
    if missing_optional:
        pip_list = " ".join(sorted({p["pip"] for p in missing_optional}))
        print(f"⚠ 可选依赖缺失 {len(missing_optional)} 项（对应功能降级跳过，不影响主流程）：")
        print(f"    pip install {pip_list}")

    return 1 if missing_required else 0


if __name__ == "__main__":
    sys.exit(main())
