#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""07c_embed_figures.py —— 正文内嵌图版嵌入器（技能版，配合 07 探测 / 07b 判别）

与技能 06_embed_pdf_figures.py 的分工
------------------------------------
  06_embed_pdf_figures.py：书末**整页图版页**（图号「附图N」在文字层、整页即图）→ 整页提图。
  本脚本                 ：**正文内嵌图版**（图题在页面文字层、图幅只占页面一块、
                           需按 bbox 裁切、需旋转页转正、需按性质过滤照片）。

前置
----
  1. 07_probe_page_figures.py 产出 probe_figures.json + 裁切图（供 07b 用）；
  2. 07b_classify_figures.py  产出 figure_classify.json（每图幅 verdict：line=线描地图/示意图，photo=照片）。

本脚本做什么
------------
  对 verdict==line 且图幅面积 >= 页面 --min-area 比例的图幅：
    - 按bbox 从 PDF 渲染裁切（灰度 --dpi）；
    - 若该页注记判定「旋转90°」，裁切后顺时针转正（实测 p0324 验证：转正后图题落左下横排）；
    - 存 <out-dir>/fig_pNNNN_k.png；
    - 在 03 对应锚 <<<PAGE phys=N book=M>>> 之后插入 ![图题（原书页M）](fig/fig_pNNNN_k.png)。
  photo 与碎块（面积不足）一律不嵌——纯插入、零改写，散字保留。

  必须在 assemble 生成 03 之后跑；幂等（md 中已引用同名图即跳过）；LF 写盘，改前备份。

用法
----
  python scripts/07c_embed_figures.py --pdf <PDF> --md <03.md> \
      --probe <probe_figures.json> --classify <figure_classify.json> [--apply]
  不加 --apply 为 dry-run，只打印计划。
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

import pymupdf
from _common import ANCHOR_RE  # 2026-10-04 审查 P0-2：纯锚正则统一收敛到 _common（兼容 book=None）


def crop_render(page, rect_pt, dpi, rotated):
    """按 pt 矩形裁切渲染灰度图；rotated 时顺时针转正 90°。"""
    if not rotated:
        pm = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, clip=pymupdf.Rect(*rect_pt))
        return pm
    # 旋转页：渲染整页后按像素矩形裁切，再顺时针转正
    pm = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    sc = dpi / 72.0
    x0 = max(0, int(rect_pt[0] * sc))
    y0 = max(0, int(rect_pt[1] * sc))
    x1 = min(pm.width, int(round(rect_pt[2] * sc)))
    y1 = min(pm.height, int(round(rect_pt[3] * sc)))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    s, W, H, st = pm.samples, pm.width, pm.height, pm.stride
    # 顺时针：新图 W'=H, H'=W；new[y'][x'] = old[H-1-x'][y']，再套裁切窗：
    # 转正后新图坐标 = 转正前整页坐标经同一映射。为省内存先转正整页再切。
    cw = bytearray()
    for yp in range(W):
        cw += bytes(s[(H - 1 - xp) * st + yp] for xp in range(H))
    nW, nH = H, W
    buf = bytes(cw)
    # 在转正后的整页上裁切：转正前 (x,y) → 转正后 (nW-1-y, x)
    # 裁切窗（转正前 pt 矩形）四角映射后取外接矩形
    corners = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
    pts = [(nW - 1 - py, px) for (px, py) in corners]
    nx0 = max(0, min(p[0] for p in pts))
    nx1 = min(nW, max(p[0] for p in pts))
    ny0 = max(0, min(p[1] for p in pts))
    ny1 = min(nH, max(p[1] for p in pts))
    if nx1 - nx0 < 4 or ny1 - ny0 < 4:
        return None
    out = bytearray()
    for y in range(ny0, ny1):
        out += buf[y * nW + nx0: y * nW + nx1]
    npm = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, nx1 - nx0, ny1 - ny0))
    npm.samples_mv[:] = bytes(out)
    return npm


def main():
    ap = argparse.ArgumentParser(description="正文内嵌图版嵌入器")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--md", required=True, help="03 文件")
    ap.add_argument("--probe", required=True, help="probe_figures.json")
    ap.add_argument("--classify", required=True, help="figure_classify.json")
    ap.add_argument("--out-dir", default=None, help="图片目录，默认 <md目录>/fig")
    ap.add_argument("--dpi", type=int, default=220)
    ap.add_argument("--min-area", type=float, default=0.05,
                    help="图幅面积占页面比例下限，低于此视为碎块不嵌（默认 0.05）")
    ap.add_argument("--max-area", type=float, default=0.90,
                    help="图幅面积占页面比例上限，高于此视为整页扫描框不嵌（默认 0.90）")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    probe = json.load(open(args.probe, encoding="utf-8"))
    cls = json.load(open(args.classify, encoding="utf-8"))
    cls_map = {r["phys"]: {f["k"]: f["verdict"] for f in r["figures"]} for r in cls["verdicts"]}

    md_path = args.md
    out_dir = args.out_dir or os.path.join(os.path.dirname(md_path), "fig")
    text = open(md_path, "rb").read().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    anchors = list(ANCHOR_RE.finditer(text))
    by_phys = {}
    for m in anchors:
        by_phys[int(m.group(1))] = (m.group(2).strip(), m.end())

    doc = pymupdf.open(args.pdf)
    plan = []
    for rec in probe["records"]:
        ph = rec["phys"]
        if ph not in by_phys:
            print(f"  !! md 无锚 phys={ph}，跳过")
            continue
        book, _ = by_phys[ph]
        page = doc[ph - 1]
        parea = page.rect.width * page.rect.height
        rotated = rec.get("note", {}).get("hint") == "旋转90°"
        caps = [c["text"] for c in rec["captions"]]
        kept = []
        for k, fg in enumerate(rec["figures"], 1):
            if cls_map.get(ph, {}).get(k) != "line":
                continue
            r = fg["rect"]
            frac = (r[2] - r[0]) * (r[3] - r[1]) / parea
            if frac >= args.max_area:
                continue          # 整页扫描框，非图版
            if frac < args.min_area:
                continue          # 碎块（图题黑框、正文行等）
            fname = f"fig_p{ph:04d}_{k}.png"
            ref = f"fig/{fname}"
            if ref in text:
                kept.append((fname, None, "已嵌入"))
                continue
            caption = caps[0] if len(caps) == 1 else "／".join(caps)
            pageinfo = f"原书页{book}" if book and book != "None" else f"物理页{ph}"
            tag = f"![{caption}（{pageinfo}）]({ref})"
            kept.append((fname, tag, "", fg["rect"]))
        if kept:
            plan.append(dict(phys=ph, rotated=rotated, items=kept))

    n_emb = sum(1 for p in plan for it in p["items"] if it[1])
    print(f"计划嵌入 {n_emb} 幅图，涉及 {len(plan)} 页：\n")
    for p in plan:
        rot = " [旋转页→转正]" if p["rotated"] else ""
        for fname, tag, note, _r in p["items"]:
            print(f"  phys={p['phys']:<4d}{rot}  {fname}  {note or tag[:52]}")

    if not args.apply:
        print("\n[DRY-RUN] 未落库。加 --apply 执行。")
        return 0

    os.makedirs(out_dir, exist_ok=True)
    ins = []
    made = 0
    for p in plan:
        ph = p["phys"]
        page = doc[ph - 1]
        rotated = p["rotated"]
        for fname, tag, _note, rect in p["items"]:
            if not tag:
                continue
            dst = os.path.join(out_dir, fname)
            pm = crop_render(page, rect, args.dpi, rotated)
            if pm is None:
                print(f"  !! phys={ph} {fname} 裁切失败，跳过")
                continue
            pm.save(dst)
            made += 1
            _book, aend = by_phys[ph]
            ins.append((aend, tag))
    # 按位置倒序插入
    new = text
    for pos, tag in sorted(ins, key=lambda x: -x[0]):
        new = new[:pos] + "\n\n" + tag + new[pos:]
    from _common import backup_target, set_backup_root, safe_write  # 备份落点统一 + 原子写
    # 备份落点优先级：EBOOK_BAK_DIR 环境变量 > <md 同级>/_bak（末选会污染交付目录，
    # 交付目录应只含六件，见 SKILL §2，故显式告警并给出改法）。
    if os.environ.get("EBOOK_BAK_DIR"):
        set_backup_root(os.environ["EBOOK_BAK_DIR"])
    else:
        set_backup_root(os.path.join(os.path.dirname(os.path.abspath(md_path)), "_bak"))
        print("   ⚠️ 未设 EBOOK_BAK_DIR：备份将落 <md_path 同级>/_bak/（交付目录内）。"
              "建议先 export EBOOK_BAK_DIR=<work>/_bak，保持交付目录只含六件。")
    bak = backup_target(md_path, suffix="bak-embed-figures")  # 每次 apply 前时间戳快照，避免覆盖旧备份
    shutil.copy2(md_path, bak)
    print("  备份 ->", bak)
    safe_write(md_path, new, backup=False)  # 备份已由上方 backup_target 显式完成
    print(f"\n写出图片 {made} 幅 -> {out_dir}")
    print(f"注入引用 {len(ins)} 处，md 已 LF 写回。")
    doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
