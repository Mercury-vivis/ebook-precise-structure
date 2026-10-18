#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_patch_schema_fields.py —— 补丁字段契约（2026-10-17）

守三件事（都是"静默失效"型缺陷，不报错但语义变了）：
  1. "deleted": true  → 必须 ERROR（工具只认 status:"deleted"，否则补丁被当 active 施加）
  2. 拼写错误键（sysmatic）→ 必须 ERROR（未知键被静默忽略）
  3. 未知扩展键（extra_note）→ 只 WARN，不阻断（未来扩展不能被一刀切死）
  4. 合法 status:"deleted" tombstone → 不报 ERROR（旧数据兼容）
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
fails = []


def run(script, work):
    r = subprocess.run([PY, os.path.join(HERE, script), work],
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def write_work(patches):
    d = tempfile.mkdtemp(prefix="sf_")
    os.makedirs(os.path.join(d, "corrections"))
    json.dump(patches, open(os.path.join(d, "corrections", "01.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    json.dump({"total_phys": 50}, open(os.path.join(d, "book_meta.json"), "w", encoding="utf-8"))
    return d


def check(desc, cond):
    if not cond:
        fails.append(desc)


# 1. deleted:true → ERROR（两脚本都要拦）
d = write_work([{"phys": 3, "old": "自已", "new": "自己", "deleted": True, "reason": "t"}])
rc, out = run("validate_corrections.py", d)
check("validate 应拦 deleted:true", rc != 0 and "字段 deleted 已废弃" in out)
rc, out = run("check_patch.py", d)
check("check_patch 应拦 deleted:true", rc != 0 and "字段 deleted 已废弃" in out)

# 2. 拼写错误 → ERROR
d = write_work([{"phys": 3, "old": "甲乙", "new": "丙丁", "sysmatic": True, "reason": "t"}])
rc, out = run("validate_corrections.py", d)
check("validate 应拦疑似拼写错误", rc != 0 and "疑似拼写错误" in out)

# 3. 未知扩展键 → 只 WARN
d = write_work([{"phys": 3, "old": "戊己", "new": "庚辛", "reason": "t", "extra_note": "x"}])
rc, out = run("validate_corrections.py", d)
check("未知扩展键不得阻断", rc == 0 and "warn=1" in out)

# 4. status:deleted tombstone 不报 ERROR
d = write_work([{"phys": 3, "old": "甲乙", "new": "丙丁", "status": "deleted", "reason": "t"}])
rc, out = run("validate_corrections.py", d)
check("tombstone 不得报 ERROR", rc == 0 and "字段 deleted 已废弃" not in out)

if fails:
    print("❌ test_patch_schema_fields 失败 %d 项：" % len(fails))
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ test_patch_schema_fields 通过（deleted 废弃键 / 拼写错误 / 未知扩展 / tombstone 兼容）")
