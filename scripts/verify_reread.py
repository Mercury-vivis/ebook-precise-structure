#!/usr/bin/env python3
# verify_reread.py —— 回读验证：自动抽查关键纠错点在03中的正确性（P1-D3，v1.27.0）
# 【已被 check_book.py（闸B 正向半量回放即全量重读） 取代（2026-10-18）】本文件保留供历史核对，新流程不要调用。
#
# 用法：python verify_reread.py <WORK目录> <OUT交付目录> [--top N]
# 读 patch_manifest.json 获取 active 补丁，自动选 top N 条关键纠错点（按出现次数/专名/系统性排序），
# 扫描 03 正文：正确形态存在 / 错误形态零残留。输出验证报告，替代人工 grep。
#
# 设计原则：只做机械验证（存在性/零残留），不做语义判断；存疑补丁(old==new)跳过。

import os, sys, json, argparse, re, glob
from _common import read_text_cn, read_json_cn, strip_correction_traces  # #24 / 闸2同口径剥留痕


def load_active_patches(work):
    """从 patch_manifest.json 或 corrections/*.json 加载 active 补丁"""
    mp = os.path.join(work, "patch_manifest.json")
    if os.path.isfile(mp):
        data = read_json_cn(mp)
        return [p for p in data.get("patches", []) if p.get("status") == "active" and p.get("old")]
    # 回退：读 corrections/*.json
    patches = []
    for fp in sorted(glob.glob(os.path.join(work, "corrections", "*.json"))):
        data = read_json_cn(fp)
        if isinstance(data, list):
            for p in data:
                if isinstance(p, dict) and p.get("old") and not p.get("deleted"):
                    patches.append(p)
    return patches


def find_out_file(out_dir):
    """找 03_带页码全文.md"""
    cands = glob.glob(os.path.join(out_dir, "*03_*.md"))
    return cands[0] if cands else None


def select_key_patches(patches, top_n=20):
    """选关键纠错点：优先 systematic / 专名（old/new≥2字）/ 非存疑 / 非删除型"""
    scored = []
    for p in patches:
        old = p.get("old", "")
        new = p.get("new", "")
        if not old or old == new:  # 存疑/空
            continue
        score = 0
        if p.get("systematic") or p.get("type") == "systematic":
            score += 100
        if len(old) >= 2 and len(new) >= 2:  # 专名/复合错形
            score += 50
        if p.get("ocr"):
            score += 10
        if len(old) >= 4:  # 长old更可能是专名
            score += 20
        scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:top_n]]


def verify_patch(p, text):
    """验证单条补丁：正确形态存在 / 错误形态零残留（剥留痕后，与闸2同口径）"""
    old = p["old"]
    new = p.get("new", "")
    old_count = text.count(old)
    new_count = text.count(new) if new else 0
    # 剥留痕后再判（03中〔原书作「old」〕/〔原书OCR衍"old"〕记号内合法保留old）
    # 使用 _common.strip_correction_traces，与闸2 / apply_report 单一实现，杜绝口径漂移
    clean = strip_correction_traces(text)
    old_clean = clean.count(old)
    return {
        "old": old, "new": new,
        "old_count_raw": old_count, "old_count_clean": old_clean,
        "new_count": new_count,
        "pass": old_clean == 0 and (not new or new_count > 0),
        "phys": p.get("phys", ""),
        "reason": p.get("reason", "")[:60],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("out")
    ap.add_argument("--top", type=int, default=20, help="抽查关键纠错点数（默认20）")
    args = ap.parse_args()

    work = args.work
    out_dir = args.out

    # 加载补丁
    patches = load_active_patches(work)
    if not patches:
        print("❌ 未找到 active 补丁（patch_manifest.json 或 corrections/*.json）", file=sys.stderr)
        sys.exit(2)

    # 找03
    out_file = find_out_file(out_dir)
    if not out_file:
        print(f"❌ 未找到 03_带页码全文.md（交付目录：{out_dir}）", file=sys.stderr)
        sys.exit(2)
    text = read_text_cn(out_file)

    # 选关键补丁
    key_patches = select_key_patches(patches, args.top)
    print("=" * 68)
    print(f"回读验证：{len(patches)} 条 active 补丁 → 抽查 {len(key_patches)} 条关键纠错点")
    print(f"03 文件：{os.path.basename(out_file)}（{len(text)}字）")
    print("=" * 68)

    # 逐条验证
    results = []
    pass_count = 0
    for p in key_patches:
        r = verify_patch(p, text)
        results.append(r)
        status = "✅" if r["pass"] else "❌"
        if r["pass"]:
            pass_count += 1
        phys_note = f"phys{r['phys']}" if r["phys"] else ""
        print(f"{status} 「{r['old']}」→「{r['new']}」{phys_note}："
              f"错误形态残留{r['old_count_clean']}处 / 正确形态{r['new_count']}处")
        if not r["pass"]:
            if r["old_count_clean"] > 0:
                print(f"   ⚠ 错误形态仍残留 {r['old_count_clean']} 处（剥留痕后）")
            if r["new"] and r["new_count"] == 0:
                print(f"   ⚠ 正确形态「{r['new']}」在03中未出现")

    # 汇总
    print("\n" + "=" * 68)
    print(f"回读验证结果：{pass_count}/{len(key_patches)} 通过")
    if pass_count == len(key_patches):
        print("✅ 全部关键纠错点验证通过（错误形态零残留 + 正确形态在位）")
    else:
        print(f"❌ {len(key_patches) - pass_count} 条未通过，须核实补丁是否生效或03是否已更新")
    print("=" * 68)

    # 写报告
    report_path = os.path.join(work, "reread_verify_report.md")
    lines = [
        "# 回读验证报告",
        f"- 抽查补丁数：{len(key_patches)}（共 {len(patches)} 条 active）",
        f"- 通过：{pass_count}/{len(key_patches)}",
        f"- 03文件：{os.path.basename(out_file)}",
        "",
        "| 状态 | 错误形态 | 正确形态 | phys | 错误残留 | 正确在位 | 判据 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        status = "✅" if r["pass"] else "❌"
        lines.append(f"| {status} | {r['old']} | {r['new']} | {r['phys']} | {r['old_count_clean']} | {r['new_count']} | {r['reason']} |")
    with open(report_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"报告已写入：{report_path}")

    sys.exit(0 if pass_count == len(key_patches) else 1)


RETIRED = (
    "❌ verify_reread.py 已退场（2026-10-18 由 check_book.py 闸B 正向取代）。\n"
    "   请用：python check_book.py --work <work> --out <交付目录>\n"
    "   （闸B 正向把台账每一条 old 都在 03 干净正文回放一遍，是抽样的全量版。）\n"
    "   本文件不再接受命令行调用。"
)


if __name__ == "__main__":
    print(RETIRED)
    sys.exit(2)
