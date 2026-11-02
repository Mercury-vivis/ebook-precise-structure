#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_patch_manifest_id.py —— manifest 内容键 id 三向回归（v2.2.0 · S1）

守三件事（2026-10-22《西周铜器断代》实测教训：id=文件名#下标时，corrections
重生成/增删条目致下标平移，旧 manifest 的 deleted 被误标到另一条 active 补丁
→ assemble 二级兜底跳过 → 补丁不施加 → 闸B 正向阻断；全库扫描 4 本 1036 条相左）：
  1. 墓碑不漂移：条目前插/后插条目（下标平移）后，deleted 仍钉在原内容上；
  2. 误杀不发生：被平移的 active 条目全部保持 active，一个不少；
  3. legacy 墓碑迁移存活：旧（下标式）manifest 的墓碑按 (source, old, phys)
     迁到内容键 id；内容暂缺的墓碑转 orphan_tombstones，复现时自动重新生效。

运行：python scripts/test_patch_manifest_id.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
GEN = os.path.join(HERE, "gen_patch_manifest.py")
fails = []


def check(desc, cond):
    if not cond:
        fails.append(desc)


def write_corr(work, entries, fname="00z_fixup.json"):
    d = os.path.join(work, "corrections")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, fname), "w", encoding="utf-8", newline="\n") as f:
        json.dump(entries, f, ensure_ascii=False)


def gen(work):
    r = subprocess.run([PY, GEN, "--work", work], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"gen_patch_manifest 失败：{r.stdout}{r.stderr}"
    with open(os.path.join(work, "patch_manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def by_old(man):
    return {p["old"]: p for p in man["patches"]}


def test_drift_and_miskill():
    """墓碑不漂移 + 误杀不发生：插入条目使下标平移，deleted 仍钉原内容"""
    tmp = tempfile.mkdtemp(prefix="pmi_drift_")
    entries = [
        {"phys": 10, "old": "甲甲甲", "new": "甲甲甲乙", "reason": "t"},
        {"phys": 11, "old": "乙乙乙", "new": "乙乙乙丙", "reason": "t"},
        {"phys": 12, "old": "丙丙丙", "new": "丙丙丙丁", "reason": "t"},
    ]
    write_corr(tmp, entries)
    man = gen(tmp)
    check("首轮 3 条全 active", all(p["status"] == "active" for p in man["patches"]))
    check("id 为内容键（含 10 位十六进制）",
          all(len(p["id"].split("#")[-1]) == 10 for p in man["patches"]))

    # 人工在 manifest 上把「乙乙乙」标 deleted（旧工作流：只在 manifest 手标）
    for p in man["patches"]:
        if p["old"] == "乙乙乙":
            p["status"] = "deleted"
            p["deleted_reason"] = "测试撤销"
    with open(os.path.join(tmp, "patch_manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(man, f, ensure_ascii=False)

    # corrections 重生成：前插 2 条 + 后插 1 条（旧方案下「乙乙乙」下标 1→3）
    entries2 = ([{"phys": 8, "old": "新新新", "new": "新新新甲", "reason": "t"},
                 {"phys": 9, "old": "旧旧旧", "new": "旧旧旧乙", "reason": "t"}]
                + entries + [{"phys": 13, "old": "丁丁丁", "new": "丁丁丁戊", "reason": "t"}])
    write_corr(tmp, entries2)
    man2 = gen(tmp)
    st = {p["old"]: p["status"] for p in man2["patches"]}
    check("墓碑不漂移：「乙乙乙」仍 deleted", st.get("乙乙乙") == "deleted")
    check("墓碑不带错：其余 5 条全 active",
          [k for k, v in st.items() if v == "deleted"] == ["乙乙乙"])
    check("误杀不发生：前插/后插条目均 active",
          st.get("新新新") == "active" and st.get("旧旧旧") == "active" and st.get("丁丁丁") == "active")
    check("deleted 理由继承", "测试撤销" in json.dumps(man2, ensure_ascii=False))
    print("✅ test_drift_and_miskill: 下标平移后墓碑不漂移、无误杀")


def test_legacy_migration_and_orphan():
    """legacy 下标式墓碑按内容迁移；内容暂缺转 orphan，复现自动生效"""
    tmp = tempfile.mkdtemp(prefix="pmi_legacy_")
    entries = [
        {"phys": 20, "old": "寅寅寅", "new": "寅寅寅卯", "reason": "t"},
        {"phys": 21, "old": "卯卯卯", "new": "卯卯卯辰", "reason": "t"},
    ]
    write_corr(tmp, entries)
    # 手写一份 legacy（下标式 id）manifest：把「卯卯卯」标 deleted、另带一条
    # 内容已不在 corrections 的墓碑（辰辰辰）
    legacy = {"patches": [
        {"id": "00z_fixup.json#1", "old": "卯卯卯", "new": "卯卯卯辰", "phys": 21,
         "source": "corrections/00z_fixup.json", "status": "deleted",
         "deleted_reason": "legacy 撤销", "deleted_date": "2026-10-01"},
        {"id": "00z_fixup.json#5", "old": "辰辰辰", "new": "辰辰辰巳", "phys": 22,
         "source": "corrections/00z_fixup.json", "status": "deleted",
         "deleted_reason": "legacy 撤销2", "deleted_date": "2026-10-02"},
    ]}
    with open(os.path.join(tmp, "patch_manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(legacy, f, ensure_ascii=False)

    man = gen(tmp)
    st = {p["old"]: p["status"] for p in man["patches"]}
    check("legacy 墓碑迁移：「卯卯卯」deleted", st.get("卯卯卯") == "deleted")
    check("legacy 迁移保留 deleted_reason", "legacy 撤销" in json.dumps(man, ensure_ascii=False))
    check("无辜条目保持 active", st.get("寅寅寅") == "active")
    check("id 已换内容键（无下标式 id）",
          all(not p["id"].split("#")[-1].isdigit() for p in man["patches"]))
    orph = man.get("orphan_tombstones", [])
    check("内容暂缺的墓碑转 orphan", any(o.get("old") == "辰辰辰" for o in orph))

    # 内容复现（「辰辰辰」写回 corrections）→ 墓碑自动重新生效
    write_corr(tmp, entries + [{"phys": 22, "old": "辰辰辰", "new": "辰辰辰巳", "reason": "t"}])
    man2 = gen(tmp)
    st2 = {p["old"]: p["status"] for p in man2["patches"]}
    check("orphan 复现自动生效：「辰辰辰」deleted", st2.get("辰辰辰") == "deleted")
    check("orphan 已被消费", not any(o.get("old") == "辰辰辰"
                                   for o in man2.get("orphan_tombstones", [])))
    print("✅ test_legacy_migration_and_orphan: legacy 墓碑内容迁移 + orphan 复现生效")


def test_occurrence_suffix_and_loader():
    """同文件重复内容加 #2/#3 后缀；_load_corr_entries 与 manifest active 数一致"""
    sys.path.insert(0, HERE)
    from _common import _load_corr_entries  # noqa: E402
    tmp = tempfile.mkdtemp(prefix="pmi_occ_")
    dup = {"phys": 30, "old": "巳巳巳", "new": "巳巳巳午", "reason": "t"}
    write_corr(tmp, [dup, dict(dup), {"phys": 31, "old": "午午午", "new": "午午午未", "reason": "t"}])
    man = gen(tmp)
    ids = [p["id"] for p in man["patches"]]
    check("重复内容第一条无后缀", ids[0].count("#") == 1)
    check("重复内容第二条带 #2", ids[1].endswith("#2"))
    check("不同内容不带 #2", ids[2].count("#") == 1)

    # 把其中一条重复条目标 deleted 后，_load_corr_entries 跳过的正是它（按内容键命中）
    for p in man["patches"]:
        if p["id"].endswith("#2"):
            p["status"] = "deleted"
    with open(os.path.join(tmp, "patch_manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(man, f, ensure_ascii=False)
    loaded = [c for c, _b in _load_corr_entries([os.path.join(tmp, "corrections", "00z_fixup.json")], tmp)]
    olds = [c["old"] for c in loaded]
    check("_load_corr_entries 与 manifest 一致：2 条 active", len(loaded) == 2)
    check("跳过的是被标 deleted 的那条重复内容之一", olds.count("巳巳巳") == 1)
    print("✅ test_occurrence_suffix_and_loader: 重复内容出现序后缀 + 加载侧一致")


if __name__ == "__main__":
    test_drift_and_miskill()
    test_legacy_migration_and_orphan()
    test_occurrence_suffix_and_loader()
    if fails:
        print(f"❌ test_patch_manifest_id 失败 {len(fails)} 项：")
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print("✅ test_patch_manifest_id 通过")
