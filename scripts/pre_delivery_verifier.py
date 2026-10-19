#!/usr/bin/env python3
# pre_delivery_verifier.py —— 闸2（专项口径 §6.1.3 / 闸体系权威定义 §6.1）
# 【已被 check_book.py（闸B 书账互释：04 记录 ↔ 03 实际） 取代（2026-10-18）】本文件保留供历史核对，新流程不要调用。
#
#   机械强制、双向、只查 active、含 stale-03 检查。真实残留 >0 即非零退出拒交付。
#   含原 C1 补丁生效检查（动作8：C1 并入闸2）。
#
#   正向（补丁生效）：回放 active 补丁 old 对 03 干净正文——剔〔〕留痕 + 剔通改清单行；
#                     真实残留 >0 → 非零退出，列仍残留条目。
#   反向（补丁/底本错位）：active 补丁 old 在 fulltext_struct（未纠错底本）中 0 命中 → 报错位（仅强告警，不阻断）。
#   stale-03：03 的 mtime < 最新 active 补丁源文件 mtime → 报"03 疑似未重装"。
#
#   退出码：0 = 通过；1 = 内容正确性未过（residual / content_residual / stale-03）拒交付；2 = 03 文件缺失/无法定位。
#
#   用法：python pre_delivery_verifier.py --work <工作目录> --out <交付目录> [--meta <book_meta.json>]
#
import re, json, os, argparse, sys, glob
from _common import (read_text_cn, read_json_cn, strip_correction_traces,
                     _page_blocks, is_residual_full)  # 页块切分单一真相源；is_residual_full=残留权威口径（与 emit_apply_report 共用）
from _common import expand_patch_data  # 补丁加载口径单一真相源


def clean_03_body(text):
    """剔〔〕留痕 + 剔通改清单行，得可比对正文（单一真相源见 _common.strip_correction_traces）。"""
    return strip_correction_traces(text, strip_tonggai_lines=True)


def load_active(work):
    """优先 patch_manifest.json（只取 active）；无则退化为 corrections/*.json 全部视为 active。

    P0 改造11（v1.17.0）：manifest 模式加载时，每个补丁的 source 指向 manifest 文件本身
    （绝对路径），stale-03 比较用 manifest 的 mtime，而非缺失的 corrections 路径（旧版
    source 缺字段 → os.path.isfile 返 False → stale 检查被跳过）。"""
    mp = os.path.join(work, "patch_manifest.json")
    active = []
    if os.path.isfile(mp):
        mp_abs = os.path.abspath(mp)
        for p in read_json_cn(mp).get("patches", []):
            if p.get("status") == "active" and p.get("old"):
                active.append({**p, "source": mp_abs})
    else:
        for fp in sorted(glob.glob(os.path.join(work, "corrections", "*.json"))):
            # 加载口径统一（含顶层单条 dict 形态），否则该份补丁不在 active 内 → 闸2 不查它
            data = expand_patch_data(read_json_cn(fp))
            for c in data:
                if isinstance(c, dict) and c.get("old") and not c.get("merge"):
                    active.append({"old": c["old"], "new": c.get("new", ""),
                                   "phys": c.get("phys", ""), "source": os.path.join("corrections", os.path.basename(fp))})
    return active


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True, help="交付目录（03 所在）")
    ap.add_argument("--type", default="", help="书类型（如 制度史/职官），用于词典 char_class 书型过滤；省略时读 --meta")
    ap.add_argument("--meta", default="", help="book_meta.json 路径，取 type 字段（与 --type 二选一，--type 优先）")
    ap.add_argument("--no-fail", action="store_true",
                    help="【已废弃语义】P0 改造3 起：闸2 阻断项（residual 正向残留 / content_residual 词典命中 /"
                         " stale-03）无论是否 --no-fail 都拒交付；此旗仅保留兼容，不再豁免内容正确性"
                         "（mismatch 仅为强告警、不阻断）。")
    args = ap.parse_args()
    B, OUT = args.work, args.out

    # 书类型：--type 优先，否则从 --meta 读
    book_type = args.type or ""
    if not book_type and args.meta and os.path.isfile(args.meta):
        try:
            book_type = read_json_cn(args.meta).get("type", "")
        except Exception as e:
            # 书型取不到会让混淆词典的 char_class 全部不扫 → 内容正确性漏检。
            # 与词典扫描异常同口径：不得静默当"通过"。
            sys.stderr.write("⚠️ book_meta 读取失败，词典按书型过滤将失效（%s）；"
                             "请确认 --meta 路径与文件合法性。\n" % e)
    if args.meta and not os.path.isfile(args.meta):
        sys.stderr.write("⚠️ --meta 指向的文件不存在：%s（书型过滤将降级）\n" % args.meta)
    # 2026-10-05 P2（复审）：书型缺失时词典 char_class 分桶条目全部不激活（如「磨」仅 ④/⑥ 扫），
    # 内容正确性扫描静默降级——必须显著告警，不得无感。
    if not book_type:
        sys.stderr.write("⚠️⚠️ 书类型缺失（未提供 --type 且 --meta 不可读/缺 type 字段）："
                         "混淆词典按书型分桶的条目本轮全部不扫，内容正确性扫描降级；"
                         "请补传 --type 或 --meta 后重跑。\n")

    cands = sorted(glob.glob(os.path.join(OUT, "*03_*.md")))
    if not cands:
        # 03 不存在时绝不能 exit 0——那与"闸2 通过"同义，独立调用 verifier 会拿到假绿灯。
        print("❌ 未找到 03 交付件（*03_*.md），闸2 无法执行（请检查 --out 是否指向交付目录、03 是否已生成）")
        sys.exit(2)
    txt03 = read_text_cn(cands[0])
    clean = clean_03_body(txt03)

    struct_path = os.path.join(B, "fulltext_struct.txt")
    struct = read_text_cn(struct_path) if os.path.isfile(struct_path) else ""

    active = load_active(B)
    residual, mismatch = [], []
    # 修正 C（2026-10-03，v1.15.2）：逐页定位——按 <<<PAGE phys=N>>> 锚把 03 切成页块，
    # 逐页补丁只在「其目标 phys 页块」内查残留，避免目录页/正文重复短语/引文等
    # 合法同串被误判为残留（本轮「二关于太守…」/「一太守府…」的目录裸标题即此误报）。
    # systematic/全局补丁（无具体 phys）仍查全文档。
    blocks = _page_blocks(clean)
    for p in active:
        old = p["old"]
        if not old:
            continue
        new = p.get("new", "")
        phys = p.get("phys")
        phys_ok = phys not in (None, "", "ALL") and str(phys).isdigit()
        # 全文口径（权威，v1.16.2 修复环4 假通过）：剔除 new 后看 old 是否仍独立出现于整篇
        # 03 干净正文。这是「补丁是否已生效」的唯一权威判据——旧版只查声明页块（scope），
        # phys 一旦错位，old 在别的页，声明页查不到 → 误判"已应用"，真残留被洗成"通过"
        # （中层实测掩盖 77 条）。现改为：全文仍见 old → 一律判真残留，非零退出。
        # N5（2026-10-04 审查）：_still_full 统一走 is_residual_full 单一实现（与 emit_apply_report 共用），
        # 杜绝两处各自内联导致口径漂移。
        _still_full = is_residual_full(clean, old, new)
        # 逐页口径（诊断，不再作为通过依据）：声明页块内是否仍见 old，用于区分
        # 「声明页内残留」与「phys 错位·全文仍见」（后者是 phys 校准缺失的信号）。
        page_blk = blocks.get(int(phys)) if phys_ok else None
        if page_blk is not None:
            _still_page = (old in page_blk.replace(new, "\x00")) if (new and old != new) else (old in page_blk)
        else:
            _still_page = None
        if _still_full:
            if _still_page:
                residual.append(f"{old}（来源 {p.get('source','')}，phys {p.get('phys','')}）【声明页内残留】")
            else:
                residual.append(f"{old}（来源 {p.get('source','')}，phys {p.get('phys','')}）【phys 错位·全文仍见，真残留】")
        # 改造2 修正：systematic 通改补丁（manifest 记 type="systematic" / corrections 记 systematic:true）
        # 的 old 在底本0命中是预期（已通改替换），不算 mismatch；仅非 systematic（逐处纠错/per-phys）
        # 补丁 old 底本0命中才可疑（补丁白写/底本版本错）。
        _is_sys = bool(p.get("systematic")) or (p.get("type") == "systematic")
        if struct and struct.count(old) == 0 and not _is_sys:
            mismatch.append(f"{old}（来源 {p.get('source','')}）")

    # ---- P0 改造1（v1.17.0）：混淆词典内容正确性扫描（独立基准，不依赖台账）----
    # compound 直接 count（安全，≥2字串绝不合法）；char_class 仅本书型命中、ctx 正则扫描（禁裸计数，
    # 避免误伤合法同形字如 磨勘/磨练/秋毫）。这是「闭环 PASS 但内容有错」的兜底——账本没列的错误也能发现。
    # v1.33.0：从 meta 读取 proper_names 白名单，避免地名/专名（如「四盘磨」）被 ctx 正则误报。
    _wl = []
    if args.meta and os.path.isfile(args.meta):
        try:
            _wl = read_json_cn(args.meta).get("proper_names", []) or []
        except Exception:
            pass
    try:
        from ocr_confusion_dict import scan as _dict_scan
        content_residual = _dict_scan(clean, book_type, whitelist=_wl)
    except Exception as e:
        # N10（2026-10-04 审查）：内容正确性扫描异常不得静默吞掉——否则闸2 照常 PASS，
        # 与"内容正确性无逃生口"矛盾。异常即视为本次内容扫描未执行，强制判未通过。
        sys.stderr.write(f"⚠️ 混淆词典内容扫描异常未执行：{e}——闸2 不得据此判 PASS。\n")
        content_residual = ["⚠️ 混淆词典内容扫描异常未执行（内容正确性未核验）"]

    # stale-03 检查（改造11 修正）：统一基于 corrections/*.json 源文件最新 mtime，
    # 不依赖 manifest 自身 mtime（manifest 是 assemble 产物，必晚于 03，用其比必然假 stale）。
    stale = False
    t03 = os.path.getmtime(cands[0])
    _corr_files = sorted(glob.glob(os.path.join(B, "corrections", "*.json")))
    if _corr_files:
        t_corr = max(os.path.getmtime(f) for f in _corr_files)
        if t_corr > t03:
            stale = True

    print(f"闸2 verifier：active 补丁 {len(active)} 条，检查 03 = {os.path.basename(cands[0])}")
    if residual:
        print(f"❌ 正向残留 {len(residual)} 处（03 干净正文仍含未纠正 old，须补充纠错 + 重跑 assemble）：")
        for r in residual:
            print("   - " + r)
    if mismatch:
        print(f"⚠️ 反向底本 0 命中 {len(mismatch)} 处（补丁 old 在底本未出现，疑似补丁/底本错位，须人工核实）：")
        for m in mismatch:
            print("   - " + m)
    if content_residual:
        print(f"⚠️ 词典内容扫描命中 {len(content_residual)} 类（独立于台账的内容错字，须补充纠错后重跑 assemble）：")
        for r in content_residual:
            print("   - " + r)
    if stale:
        print("⚠️ stale-03：03 早于最新 active 补丁修改时间，疑似未重装，须重跑 assemble。")
    # 改造2 实测偏差（v1.17.0）：mismatch（补丁 old 底本0命中）经回测验证——三书 1/69/12 处
    # mismatch 全部为「03 已无此 old（内容已正确）仅台账废补丁」，不表示内容错。内容正确性
    # 由 residual（台账 old 在 03 仍出现）+ content_residual（词典命中）双硬闸保证。故 mismatch
    # 降级为强告警、不阻断交付；保留打印提示主控方清理台账废补丁。
    ok = (not residual) and (not content_residual) and (not stale)
    if ok:
        print("✅ 闸2 通过：台账残留 0、词典内容扫描 0、非 stale，03 与台账一致且内容正确。（mismatch 仅告警，见上）")
        sys.exit(0)
    else:
        # P0 改造3（v1.17.0）：内容残留 / 词典命中 / stale 一律拒交付，--no-fail 不豁免。
        # 铁律：闸2 内容正确性无逃生口；仅 phys 未命中（pipeline B2）可由 --no-fail/--tolerate-miss 容忍。
        # mismatch 不在此列（已降为告警，见 ok 条件注释）。
        print("❌ 闸2 未通过：存在内容残留 / 词典命中 / stale，拒交付（--no-fail 不豁免内容正确性）。")
        sys.exit(1)


RETIRED = (
    "❌ pre_delivery_verifier.py 已退场（2026-10-18 由 check_book.py 的闸B 取代）。\n"
    "   请用：python check_book.py --work <work> --out <交付目录>\n"
    "   （正向 = 台账 old 在 03 残留，是唯一阻断项；反向差异分三类报，不阻断。）\n"
    "   本文件不再接受命令行调用。"
)


if __name__ == "__main__":
    print(RETIRED)
    sys.exit(2)
