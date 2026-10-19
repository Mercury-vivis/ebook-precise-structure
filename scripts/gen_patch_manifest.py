#!/usr/bin/env python3
# gen_patch_manifest.py —— corrections 台账自动索引（§6.3）
#
#   扫描 work/corrections/*.json，生成 work/patch_manifest.json。
#   每条补丁记：id / old / new / phys / type / reason / source / status(active|deleted)。
#   status 字段回写：本脚本只从 corrections/*.json 推 active；已核实删除的条目由人工在
#   patch_manifest.json 把对应 id 的 status 改 "deleted" 并填 deleted_reason/deleted_date
#   （走 v1.13 三步走：快照→04 记录→核实），不从文件物理删、不维护第二份真相源。
#
#   用法：python gen_patch_manifest.py --work <工作目录>
#
import re, json, glob, os, argparse, sys
from _common import read_json_cn
from _common import expand_patch_data  # 补丁加载口径单一真相源


def stable_id(src_basename, idx):
    """稳定 id：源文件名 + 条目下标。corrections 内容不变则 id 不变，便于跨 run 保留 deleted 标记。"""
    return f"{src_basename}#{idx}"


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
    old = {}
    if os.path.isfile(manifest_path):
        try:
            old = {p["id"]: p for p in read_json_cn(manifest_path).get("patches", [])}
        except Exception as e:
            # v1.16.2 修复：旧 manifest 解析失败绝不能静默重置为空——否则已核实删除的
            # 记录全部丢失、补丁"复活"被重新施加。直接报错中止，保留磁盘旧 manifest 不覆盖。
            print(f"❌ 旧 patch_manifest.json 解析失败：{e}；为防已删除补丁复活，已中止"
                  f"（磁盘旧 manifest 未改动，请先修复它再跑）。", file=sys.stderr)
            sys.exit(1)

    patches = []
    if os.path.isdir(corr_dir):
        for base, i, c in _iter_patches(corr_dir):
            pid = stable_id(base, i)
            old_rec = old.get(pid)
            # 🟡P2 新-5/新-6 一致性修复：corrections/*.json 是活补丁文件，其 status=deleted 优先；
            # 旧 manifest 的 deleted 仅作兜底（兼容"人工只在 manifest 标 deleted"的旧工作流）。
            # 这样 corrections 里标 deleted 的补丁能在 manifest / apply / verifier 三处一致生效，
            # 不再出现"corrections 标了 deleted、manifest 仍当 active、回放校验误报残留"的脱节。
            status = c.get("status", "active")
            if status not in ("active", "deleted"):
                status = "active"
            if status == "active" and old_rec and old_rec.get("status") == "deleted":
                status = "deleted"
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
            if status == "deleted":
                # deleted_reason/date 优先取 corrections（活文件），兜底取旧 manifest
                rec["deleted_reason"] = c.get("delete_reason") or (old_rec.get("deleted_reason", "") if old_rec else "")
                rec["deleted_date"] = c.get("delete_date") or (old_rec.get("deleted_date", "") if old_rec else "")
            patches.append(rec)

    out = {
        "generated_by": "gen_patch_manifest.py",
        "note": "status=active 的补丁由 assemble/verifier 处理；删除须走 v1.13 三步走后在人工编辑此文件标 deleted，勿物理删 corrections/*.json。",
        "patches": patches,
    }
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    n_active = sum(1 for p in patches if p["status"] == "active")
    n_del = len(patches) - n_active
    print(f"patch_manifest.json 生成：共 {len(patches)} 条，active {n_active}，deleted {n_del}（{manifest_path}）")


if __name__ == "__main__":
    main()
