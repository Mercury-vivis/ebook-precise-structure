#!/usr/bin/env python3
# gen_patch_manifest.py —— corrections 台账自动索引（§6.3）
#
#   扫描 work/corrections/*.json，生成 work/patch_manifest.json。
#   每条补丁记：id / old / new / phys / type / reason / source / status(active|deleted)。
#
#   id 口径（v2.2.0 起）：**内容键** = 源文件名#sha1(old|new|phys)[:10]（同文件重复
#   内容加 #2/#3 出现序后缀），由 _common.patch_stable_id 单一真相源生成。历史方案
#   「文件名#下标」已废止——corrections 重生成/增删条目使下标平移，按 id 继承
#   deleted 会把墓碑误标到另一条 active 补丁上（实测误杀，见 patch_stable_id 文档）。
#
#   status 字段回写（两条继承通道，v2.2.0）：
#     ① id 相同：人工在 patch_manifest.json 把对应 id 的 status 改 "deleted"（新工作流，
#        内容键 id 跨 run 稳定，手标后重跑不丢）；
#     ② legacy 内容迁移：旧（下标式）manifest 的墓碑按 (source, old, phys) 内容匹配
#        移植到新 id——下标漂移误杀不可能发生，legacy 墓碑不丢；
#   内容暂不在 corrections 的墓碑转入 orphan_tombstones（内容复现时自动重新生效）。
#   corrections 单条 status=deleted 始终优先（活文件为准）。
#
#   用法：python gen_patch_manifest.py --work <工作目录>
#
import re, json, glob, os, argparse, sys
from _common import read_json_cn
from _common import expand_patch_data  # 补丁加载口径单一真相源
from _common import patch_stable_id  # 内容键 id 单一真相源（v2.2.0）

_LEGACY_ID_RE = re.compile(r"^.+#\d+$")  # 旧格式 id：文件名#下标


def _content_key(source, old, phys):
    # 分隔符归一：manifest 里 source 可能是「corrections/x.json」（历史正斜杠）或
    # Windows os.path.join 产的反斜杠——两侧必须同形，否则 legacy 墓碑内容匹配静默失配。
    return ((source or "").replace("\\", "/"), old or "",
            str(phys if phys is not None else ""))


def _iter_patches(corr_dir):
    for fp in sorted(glob.glob(os.path.join(corr_dir, "*.json"))):
        try:
            data = read_json_cn(fp)
        except Exception as e:
            print(f"⚠️ 读取 {fp} 失败：{e}")
            continue
        # 加载口径统一（含顶层单条 dict 形态），否则该份补丁会从 manifest 消失 → 补丁回放不查它
        data = expand_patch_data(data)
        if not isinstance(data, list):
            continue
        base = os.path.basename(fp)
        for i, c in enumerate(data):
            if not isinstance(c, dict):
                continue
            if "old" not in c and "merge" not in c:
                continue
            if c.get("merge"):
                continue
            yield base, i, c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    args = ap.parse_args()
    B = args.work
    corr_dir = os.path.join(B, "corrections")
    manifest_path = os.path.join(B, "patch_manifest.json")

    # 旧 manifest：保留已核实删除的记录（status=deleted），跨 run 不丢
    old_patches, old_orphans = [], []
    if os.path.isfile(manifest_path):
        try:
            _om = read_json_cn(manifest_path)
            old_patches = _om.get("patches", [])
            old_orphans = _om.get("orphan_tombstones", [])
        except Exception as e:
            # v1.16.2 修复：旧 manifest 解析失败绝不能静默重置为空——否则已核实删除的
            # 记录全部丢失、补丁"复活"被重新施加。直接报错中止，保留磁盘旧 manifest 不覆盖。
            print(f"❌ 旧 patch_manifest.json 解析失败：{e}；为防已删除补丁复活，已中止"
                  f"（磁盘旧 manifest 未改动，请先修复它再跑）。", file=sys.stderr)
            sys.exit(1)

    # 旧 manifest 的 deleted 记录，建两套索引：
    #   by_id    —— id 相同直接继承（新工作流：人工在 manifest 上手标 deleted）
    #   by_content —— (source, old, phys) 内容匹配（legacy 下标式墓碑迁移 + 孤儿墓碑复现）
    old_by_id = {p.get("id", ""): p for p in old_patches if p.get("status") == "deleted"}
    old_del_by_content = {}
    for p in list(old_patches) + list(old_orphans):
        if p.get("status", "deleted") != "deleted":
            continue
        ck = _content_key(p.get("source"), p.get("old"), p.get("phys"))
        old_del_by_content.setdefault(ck, p)  # 同内容多条墓碑取首条

    patches = []
    seen_content = set()
    n_migrated, n_dup = 0, 0
    if os.path.isdir(corr_dir):
        occ = {}
        for base, i, c in _iter_patches(corr_dir):
            pid0 = patch_stable_id(base, c)
            n = occ.get(pid0, 0) + 1
            occ[pid0] = n
            if n > 1:
                n_dup += 1
            pid = pid0 if n == 1 else f"{pid0}#{n}"
            # 🟡P2 新-5/新-6 一致性修复：corrections/*.json 是活补丁文件，其 status=deleted 优先；
            # 旧 manifest 的 deleted 仅作兜底（兼容"人工只在 manifest 标 deleted"的旧工作流）。
            # 这样 corrections 里标 deleted 的补丁能在 manifest / apply / verifier 三处一致生效，
            # 不再出现"corrections 标了 deleted、manifest 仍当 active、回放校验误报残留"的脱节。
            status = c.get("status", "active")
            if status not in ("active", "deleted"):
                status = "active"
            inherited = None
            if status == "active":
                # 通道①：id 相同（含上一轮已是内容键 id 的 manifest 手标 deleted）
                cand = old_by_id.get(pid)
                # 通道②：legacy 下标式墓碑按内容迁移（source, old, phys 匹配）
                if cand is None:
                    cand = old_del_by_content.get(
                        _content_key(os.path.join("corrections", base), c.get("old"), c.get("phys")))
                if cand is not None and cand.get("status") == "deleted":
                    status, inherited = "deleted", cand
                    if _LEGACY_ID_RE.match(cand.get("id", "") or ""):
                        n_migrated += 1
            rec = {
                "id": pid,
                "old": c.get("old", ""),
                "new": c.get("new", ""),
                "phys": c.get("phys", ""),
                "type": ("systematic" if c.get("systematic")
                         else "ocr" if c.get("ocr")
                         else "silent" if c.get("silent")
                         else "ocr_typo"),
                "reason": c.get("reason", ""),
                "source": os.path.join("corrections", base),
                "status": status,
            }
            seen_content.add(_content_key(rec["source"], rec["old"], rec["phys"]))
            if status == "deleted":
                # deleted_reason/date 优先取 corrections（活文件），兜底取继承来的墓碑
                src = inherited or {}
                rec["deleted_reason"] = c.get("delete_reason") or src.get("deleted_reason", "")
                rec["deleted_date"] = c.get("delete_date") or src.get("deleted_date", "")
            patches.append(rec)

    # 内容已不在 corrections 的墓碑 → orphan_tombstones（内容复现时按 (source,old,phys) 自动重新生效）
    orphans = []
    for ck, p in old_del_by_content.items():
        if ck in seen_content:
            continue
        orphans.append({
            "status": "deleted",  # 自带状态：复现时通道②按它继承，不依赖默认值
            "source": ck[0], "old": ck[1], "phys": ck[2],
            "deleted_reason": p.get("deleted_reason", ""),
            "deleted_date": p.get("deleted_date", ""),
            "legacy_id": p.get("id", ""),
        })

    out = {
        "generated_by": "gen_patch_manifest.py",
        "note": "status=active 的补丁由 assemble/verifier 处理；删除优先在 corrections 单条标 "
                "\"status\": \"deleted\"（活文件为准），也可在本文件按 id 手标 deleted（id 为内容键，"
                "跨 run 稳定，勿物理删 corrections/*.json）。legacy 墓碑按内容自动迁移。",
        "patches": patches,
    }
    if orphans:
        out["orphan_tombstones"] = orphans
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    n_active = sum(1 for p in patches if p["status"] == "active")
    n_del = len(patches) - n_active
    print(f"patch_manifest.json 生成：共 {len(patches)} 条，active {n_active}，deleted {n_del}（{manifest_path}）")
    if n_migrated:
        print(f"  legacy 墓碑迁移：{n_migrated} 条（旧下标式 id → 内容键，按 (source, old, phys) 匹配）")
    if orphans:
        print(f"  orphan 墓碑携带：{len(orphans)} 条（内容暂不在 corrections，复现时自动生效）")
        for o in orphans[:5]:
            print(f"    - {o['source']} 「{o['old'][:24]}」 phys={o['phys']}（legacy id {o['legacy_id']}）")
        if len(orphans) > 5:
            print(f"    …（共 {len(orphans)} 条）")
    if n_dup:
        print(f"  ⚠️ 同文件重复内容 {n_dup} 处（id 已加出现序后缀 #2/#3；如非有意，请检查 corrections）")


if __name__ == "__main__":
    main()
