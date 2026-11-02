#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 09_cleanup_after_delivery.py —— 图版生命周期收尾（v2.5.0）
#
# 把 SKILL.md §0.3「图版回收（v2.3.3）」规则脚本化落地。图走
#   raw/imgs → work/imgs → final/imgs  一遍流，
# final 落定后清扫上游冗余，图的权威位置只留交付目录一份。
#
# 为什么需要它：同一批图会同时存在三处——
#   Book_raw/<书>/imgs/（book_ocr 源，OneDrive 同步盘）
#   work/books/<书>/imgs/（加工层，00_ingest copy2 来的工作副本）
#   交付目录 imgs/（03 引用目标，权威交付物）
# 三份长期并存是纯占用（全库实测 raw 1.78GB + final 1.15GB），交付验收后必须回收。
#
# 前置硬门槛（任一不过即退出，不删任何东西）：
#   1. 五件 00–04 齐（含格式卫生交给 check_book，本脚本只验存在）
#   2. work/gate_report.md 结论为「通过」（终检未过不回收）
#   3. 全部交付 md（00–04）的 imgs/fig 引用 ⊆ 交付目录实体（断链必须为 0）
#   4. work/bookocr_dir.txt 存在（00_ingest v2.5.0 写入，记录 raw 真实路径）
#   5. work/cleanup_log.md 不存在（幂等：已回收过不重复跑）
#
# 两场景（对齐 SKILL.md §0.3 第 1/5 条）：
#   A 有图交付：03 有图引用且交付 imgs/fig 非空 → 逐文件 md5 比对，
#               仅删 work/raw 中「交付侧存在同名且 md5 相同」的副本；
#               交付侧没有的、或 md5 不同的一律保留（md5 不同＝可能新版，先重跑 assemble）。
#   B 纯文本交付：03 无任何图引用 → imgs/ 全是未使用素材，
#               只删 work/imgs（工作副本，须与 raw 同名同字节才删），
#               保留 Book_raw/imgs（唯一素材库）。
#
# 删除方式：os.remove 直删，禁用回收站/移动（这些本就可从交付目录回拷，
#           回收站只是换个地方占盘）。
#
# 留档（两处）：
#   ① work/00_after.md 追加「图版回收」段（成书过程源头，下次 assemble 自动并入 00，
#      指纹在其后计算，不破坏指纹纪律）；同时把该段追加到当前交付 00 末尾，并更新
#      04 §五 里 00 的 sha256 指纹，保证当下 check_book 指纹仍一致。
#   ② work/cleanup_log.md 写完整机器可读清单（文件名/md5/大小/处置/保留原因）。
#
# 盘面自检（v2.6.2）：dry-run 与 --apply 是两次独立扫描，两次之间若有外部增删，
#   只有自检能看见。交付侧有、上游本次扫描已无的图逐位置报出（stdout + 留痕同记），
#   措辞只写「本次扫描时已不在盘面，原因未核实」——上游曾否有过本脚本无从核实，
#   不得写成「上游本就没有」。00 指纹固定用 sha256（字段名即 00=sha256:）。
#
# 用法：
#   python 09_cleanup_after_delivery.py --work <work目录>                 # dry-run（只列清单）
#   python 09_cleanup_after_delivery.py --work <work目录> --apply         # 真删 + 留档
#   可选 --deliv <交付目录>（缺省按 EBOOK_DELIV_ROOT / .work 标记解析）
#
# 纯标准库，零第三方依赖。

import os, sys, re, glob, hashlib, argparse
from datetime import datetime

# 解释器守卫（口径统一；本脚本纯标准库，不创建 work 目录，无需 _work_guard）
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from _fig_common import resolve_deliv  # noqa: E402

DELIVERY_SUFFIX = {
    "00": "00_梳理总览与目录对照.md",
    "01": "01_章节详解.md",
    "02": "02_脉络与速查卡.md",
    "03": "03_带页码全文.md",
    "04": "04_改错留痕.md",
}
# 图引用（与 check_book.check_delivery 同口径；这里扫 00–04 全部五件，不只 03）
_IMG_REF_RE = re.compile(r"(?:imgs|fig)/[^)\s\"'>\]]+?\.(?:jpg|jpeg|png|gif|webp|svg)", re.I)
# 长度取 32–64：正常为 64（sha256），但历史上曾有版本把 md5（32）写进该字段，
# 若只认 64 位，那个已损坏的指纹就永远匹配不上、修不回来。
_FP00_RE = re.compile(r"00=sha256:[0-9a-f]{32,64}")


def read_text(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8-sig", errors="ignore")


def md5_file(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _sha256(path, chunk=1 << 20):
    """交付指纹口径：与 pipeline_assemble / check_book 一致，sha256（64 hex）。

    00 的指纹字段名就是 `00=sha256:`，必须用 sha256 计算；曾误用 md5 写入，
    写出的 32 hex 既不等于 sha256，也会让后续按 64 hex 匹配的修复正则失配。
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def find_prefix(out_dir):
    hits = glob.glob(os.path.join(out_dir, "*" + DELIVERY_SUFFIX["00"]))
    if hits:
        return os.path.basename(hits[0])[: -len(DELIVERY_SUFFIX["00"])]
    hits = glob.glob(os.path.join(out_dir, "*" + DELIVERY_SUFFIX["03"]))
    if hits:
        return os.path.basename(hits[0])[: -len(DELIVERY_SUFFIX["03"])]
    return ""


def index_img_dir(d):
    """返回 {文件名: (绝对路径, md5, 字节数)}，目录不存在返回 {}。"""
    idx = {}
    if not os.path.isdir(d):
        return idx
    for sub in ("imgs", "fig"):
        root = os.path.join(d, sub)
        if not os.path.isdir(root):
            continue
        for fn in sorted(os.listdir(root)):
            p = os.path.join(root, fn)
            if os.path.isfile(p):
                # 同名冲突时带子目录前缀，避免 imgs/fig 互相覆盖
                key = f"{sub}/{fn}"
                st = os.stat(p)
                idx[key] = (p, md5_file(p), st.st_size)
    return idx


def collect_refs(deliv, prefix):
    """扫五件 md 的 imgs/fig 引用，返回 set（相对路径，如 imgs/p17_img_1.jpg）。"""
    refs = set()
    for sfx in DELIVERY_SUFFIX.values():
        p = os.path.join(deliv, prefix + sfx)
        if os.path.isfile(p):
            refs.update(_IMG_REF_RE.findall(read_text(p)))
    return refs


def scan_selfcheck(final_imgs, work_imgs, raw_imgs):
    """盘面自检：交付侧有、上游本次扫描已无的图，逐位置报出（只陈述事实）。

    正常流程 raw/imgs 由 book_ocr 产出、work/imgs 由 00_ingest copy2 全量复制，
    两者都不该少于 final；少于即说明本次运行开始前盘面已变动（外部删除、部分
    ingest、前次中断等）。本脚本无从核实何时、被谁删除，故只写「本次扫描时已
    不在盘面」，不写「上游本就没有」——后者是脚本无法验证的推断。
    """
    warns = []
    for label, idx in (("work", work_imgs), ("raw", raw_imgs)):
        lost = [k for k in final_imgs if k not in idx]
        if lost:
            warns.append(f"{label} 缺 {len(lost)}/{len(final_imgs)} 个交付同名图"
                         f"（本次扫描时已不在盘面，原因未核实）")
    return warns


def fail(msg):
    print(f"❌ 前置检查未过：{msg}")
    print("   （未删除任何文件；修好后重跑本脚本）")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="交付后图版回收：final 落定后清扫 work/raw 冗余图（v2.5.0）")
    ap.add_argument("--work", required=True, help="工作目录 .work/books/<书名>")
    ap.add_argument("--deliv", default="", help="交付目录（缺省按 EBOOK_DELIV_ROOT / .work 标记解析）")
    ap.add_argument("--apply", action="store_true", help="真删（缺省 dry-run，只列清单）")
    args = ap.parse_args()

    work = os.path.abspath(args.work)
    if not os.path.isdir(work):
        fail(f"工作目录不存在：{work}")

    # ---- 幂等：已回收过 ----
    log_path = os.path.join(work, "cleanup_log.md")
    if os.path.isfile(log_path):
        print(f"[i] 已存在 cleanup_log.md，本书图版已回收过，退出（幂等）。")
        print(f"    留档：{log_path}")
        return

    # ---- 前置 4：raw 路径指针 ----
    ptr = os.path.join(work, "bookocr_dir.txt")
    if not os.path.isfile(ptr):
        fail(f"缺少 {ptr}——本书 00_ingest 是 v2.5.0 之前跑的，未记录 raw 路径。\n"
             f"   请手工补建该文件，内容为 Book_raw/<书> 绝对路径（单行），或重跑 00_ingest_bookocr.py。")
    raw = read_text(ptr).strip().splitlines()[0].strip()
    if not os.path.isdir(raw):
        fail(f"bookocr_dir.txt 指向的 raw 目录不存在：{raw}")

    # ---- 交付目录 + prefix ----
    deliv = os.path.abspath(args.deliv) if args.deliv else resolve_deliv(work, "")
    if not os.path.isdir(deliv):
        fail(f"交付目录不存在：{deliv}（可用 --deliv 显式指定）")
    prefix = find_prefix(deliv)
    if not prefix:
        fail(f"交付目录找不到五件（无法识别前缀）：{deliv}")

    # ---- 前置 1：五件齐 ----
    missing = [k for k, sfx in DELIVERY_SUFFIX.items()
               if not os.path.isfile(os.path.join(deliv, prefix + sfx))]
    if missing:
        fail(f"交付五件不齐，缺：{'、'.join(missing)}（{deliv}）")

    # ---- 前置 2：gate_report 通过 ----
    gate = os.path.join(work, "gate_report.md")
    if not os.path.isfile(gate):
        fail(f"缺少 {gate}——请先跑 check_book 终检并确认通过。")
    gate_txt = read_text(gate)
    m = re.search(r"结论[：:]\s*(\S+)", gate_txt)
    if not m or m.group(1) != "通过":
        fail(f"gate_report.md 结论不是「通过」（读到：{m.group(1) if m else '无'}），交付未验收不回收。")

    # ---- 前置 3：图引用断链=0 ----
    refs = collect_refs(deliv, prefix)
    broken = sorted(r for r in refs if not os.path.isfile(os.path.join(deliv, r.replace("/", os.sep))))
    if broken:
        fail(f"交付目录图引用断链 {len(broken)}/{len(refs)}，须先补图再回收。例：{broken[:5]}")

    # ---- 索引三处图 ----
    final_imgs = index_img_dir(deliv)
    work_imgs = index_img_dir(work)
    raw_imgs = index_img_dir(raw)

    # 场景判定：以 03 是否有图引用 + 交付目录是否有图实体为准
    has_fig = bool(refs) or bool(final_imgs)

    # 盘面自检：dry-run 与 apply 是两次独立扫描，两次之间的盘面变动只有本自检能看见
    warns = scan_selfcheck(final_imgs, work_imgs, raw_imgs) if has_fig else []
    if warns:
        print(f"[⚠️ 盘面自检] 交付侧 {len(final_imgs)} 个图，上游本次扫描不足：")
        for w in warns:
            print(f"    · {w}")
        print("    本脚本只记录实况，不推断删除时间与原因；如需追查请比对 00_ingest 日志。")

    # 构造删除计划：(位置, key, path, size, md5) 与保留清单 (key, 原因)
    to_del = []
    kept = []

    if has_fig:
        # 场景 A：md5 比对，只删 final 已有同名同字节的 work/raw 副本
        print(f"[场景 A] 有图交付：图引用 {len(refs)} 处，final 图实体 {len(final_imgs)} 个")
        for loc, idx in (("work", work_imgs), ("raw", raw_imgs)):
            for key, (path, dg, size) in idx.items():
                if key in final_imgs:
                    _, df, _ = final_imgs[key]
                    if dg == df:
                        to_del.append((loc, key, path, size, dg))
                    else:
                        kept.append((loc, key, f"final 同名但 md5 不同（疑似新版，先重跑 assemble 同步）"))
                else:
                    kept.append((loc, key, "final 无同名文件（唯一副本，保留）"))
        # final 独有的图（work/raw 都没有）——无需处理，仅统计
        upstream = set(work_imgs) | set(raw_imgs)
        final_only = [k for k in final_imgs if k not in upstream]
    else:
        # 场景 B：纯文本交付。只删 work/imgs（须 raw 有同名同字节），保留 raw
        print(f"[场景 B] 纯文本交付：03 无图引用，只回收 work 工作副本，保留 raw 素材")
        for key, (path, dg, size) in work_imgs.items():
            if key in raw_imgs and raw_imgs[key][1] == dg:
                to_del.append(("work", key, path, size, dg))
            elif key in raw_imgs:
                kept.append(("work", key, "raw 同名但 md5 不同（疑似新版，保留）"))
            else:
                kept.append(("work", key, "raw 无同名文件（work 独有，保留为唯一副本）"))
        final_only = []

    # ---- 汇总 ----
    n_work = sum(1 for x in to_del if x[0] == "work")
    n_raw = sum(1 for x in to_del if x[0] == "raw")
    b_work = sum(x[3] for x in to_del if x[0] == "work")
    b_raw = sum(x[3] for x in to_del if x[0] == "raw")
    n_final = len(final_imgs)

    def human(n):
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.1f}{unit}"
            n /= 1024

    print("\n" + "=" * 72)
    print(f"  交付目录 final/imgs·fig：{n_final} 个（保留，权威交付物）")
    print(f"  本次扫描上游：work {len(work_imgs)} 个 / raw {len(raw_imgs)} 个"
          f"（单次运行的盘面实况，非历史累计）")
    print(f"  计划删除 work：{n_work} 个（{human(b_work)}）")
    print(f"  计划删除 raw ：{n_raw} 个（{human(b_raw)}）")
    print(f"  保留（上游独有/疑似新版）：{len(kept)} 个")
    if final_only:
        print(f"  交付有、上游本次扫描已无：{len(final_only)} 个（原因未核实，见盘面自检）")
    if kept:
        print("  保留明细：")
        for loc, key, why in kept[:30]:
            print(f"    [{loc}] {key} —— {why}")
        if len(kept) > 30:
            print(f"    …（其余 {len(kept)-30} 个见 cleanup_log）")
    print("=" * 72)

    if not args.apply:
        print("\n[dry-run] 未删除任何文件。确认无误后加 --apply 执行并留档。")
        return

    # ---- 真删（os.remove 直删，不走回收站）----
    deleted = []
    for loc, key, path, size, dg in to_del:
        try:
            os.remove(path)
            deleted.append((loc, key, size, dg))
        except OSError as e:
            kept.append((loc, key, f"删除失败：{e}（保留）"))
    # 删空的 imgs/fig 目录
    for base in (work, raw):
        for sub in ("imgs", "fig"):
            d = os.path.join(base, sub)
            if os.path.isdir(d) and not os.listdir(d):
                try:
                    os.rmdir(d)
                except OSError:
                    pass

    d_work = sum(1 for x in deleted if x[0] == "work")
    d_raw = sum(1 for x in deleted if x[0] == "raw")
    db_work = sum(x[2] for x in deleted if x[0] == "work")
    db_raw = sum(x[2] for x in deleted if x[0] == "raw")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ---- 留档 ①：00_after.md + 当前交付 00（同一文本）----
    # 措辞纪律：只写本次扫描能看到的事实（「本次扫描已无」），不写「上游本就没有」——
    # 上游是否曾经有过，本脚本无从核实（实测就遇到过外部先删 49 张的情形）。
    summary_line = (f"- 图版回收：交付 imgs {n_final} 张（md5 校一致）；"
                    f"删 work {d_work} 张（{human(db_work)}）/ Book_raw {d_raw} 张（{human(db_raw)}）；"
                    f"{'交付有、上游本次扫描已无 ' + str(len(final_only)) + ' 张（已保留）；' if has_fig else '纯文本交付，raw 素材全保留；'}"
                    f"保留 {len(kept)} 个（上游独有/疑似新版）。详细清单见 work/cleanup_log.md。")
    section = f"\n## 图版回收（{stamp}）\n{summary_line}\n"
    if warns:
        section += "".join(f"- 盘面自检：{w}\n" for w in warns)

    # ①a 写 work/00_after.md（成书过程源头，下次 assemble 自动并入）
    after = os.path.join(work, "00_after.md")
    with open(after, "a", encoding="utf-8", newline="\n") as f:
        f.write(section)

    # ①b 追加到当前交付 00，并更新 04 指纹（00 改了）
    p00 = os.path.join(deliv, prefix + DELIVERY_SUFFIX["00"])
    p04 = os.path.join(deliv, prefix + DELIVERY_SUFFIX["04"])
    with open(p00, "a", encoding="utf-8", newline="\n") as f:
        f.write(section)
    new_fp = _sha256(p00)  # 字段名是 00=sha256:，必须用 sha256，不能用 md5
    if os.path.isfile(p04):
        t4 = read_text(p04)
        t4b = _FP00_RE.sub(f"00=sha256:{new_fp}", t4)
        if t4b != t4:
            with open(p04, "w", encoding="utf-8", newline="\n") as f:
                f.write(t4b)
            print("[✔] 已更新 04 §五 中 00 的交付指纹")

    # ---- 留档 ②：cleanup_log.md（机器可读完整清单）----
    log = ["# 图版回收留痕（cleanup_log）", "",
           f"- 时间：{stamp}",
           f"- work：{work}",
           f"- raw ：{raw}",
           f"- 交付：{deliv}",
           f"- 场景：{'A 有图交付（md5 比对）' if has_fig else 'B 纯文本交付（只删 work，保留 raw）'}",
           f"- 扫描快照：final {n_final} / work {len(work_imgs)} / raw {len(raw_imgs)} 个"
           f"（本脚本单次运行时的盘面实况；两次运行之间若有外部增删，不在此账内）",
           f"- 交付图实体：{n_final} 个；删 work {d_work} / 删 raw {d_raw}；保留 {len(kept)}", ""]
    if warns:
        log += ["## 盘面自检", ""]
        log += [f"- ⚠️ {w}" for w in warns]
        log += ["- 说明：以上为「交付有、本次扫描上游已无」，本脚本不推断何时、被谁删除。", ""]
    log += ["## 已删除（final 同名同字节副本）", ""]
    for loc, key, size, dg in deleted:
        log.append(f"- [{loc}] {key} | {size}B | md5={dg}")
    if not deleted:
        log.append("- （无）")
    log += ["", "## 保留明细", ""]
    if has_fig:
        for k in final_only:
            log.append(f"- [上游本次扫描已无] {k}（交付侧保留，权威副本）")
    for loc, key, why in kept:
        log.append(f"- [{loc}] {key} —— {why}")
    if has_fig and not kept and not final_only:
        log.append("- （无）")
    log.append("")
    with open(log_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(log))

    print(f"\n✅ 图版回收完成：删 work {d_work} 张（{human(db_work)}）、raw {d_raw} 张（{human(db_raw)}）。")
    print(f"   交付 final/imgs {n_final} 张为权威唯一副本，可随时回拷 work 重跑 assemble。")
    print(f"   留档：00_after.md / 00（已并入）/ {log_path}")


if __name__ == "__main__":
    main()
