#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""07e_figcrop_refine.py —— 整页扫描型 PDF「页内图幅」精修裁切（精度解，配合探测/判别器用）

定位链路
--------
  07_probe_page_figures.py  粗探「哪几页有图 + 大致矩形」（分块暗率连通域）
      ↓
  07e_figcrop_refine.py     用 OCR 文字层当掩膜，把「页面文字」与「图内标注」分开，
                         精修外接矩形 → 逐幅裁图（本工具）
      ↓
  07b_classify_figures.py / 07c_embed_figures.py   判线描/照片 + 嵌入 03

输入候选 JSON 支持两种：
  ① 本工具自己的格式：{"records":[{"k":1,"phys":55,"rect":[x0,y0,x1,y1]}, ...]}
     （`rect` 是**粗框/旧框**，仅用于挑最近的图题与确定分区；可留 null 走无图题兜底）
  ② `07_probe_page_figures.py` 的 probe_figures.json：自动取 records[].figures[].rect
     （页码字段 phys / page / p 自动识别；一页多图自动编号 k）

用法
----
  PY="<本机 venv python>"
  "$PY" scripts/07e_figcrop_refine.py --pdf <PDF> --in <候选.json> --out <目录>            # 只出 rect + 评估
  "$PY" scripts/07e_figcrop_refine.py --pdf <PDF> --in <候选.json> --out <目录> \
        --dpi 220 --cut-dir <fig目录> --prefix fig                        # 同时裁图
  "$PY" scripts/07e_figcrop_refine.py --pdf <PDF> --pages 55,56,58 --out <目录> --cut-dir <目录>

输出
----
  <out>/figcrop_refined.json   精修 rect + 原 rect + 评估（cut 切图墨 / tin 框内段落墨 / sin 框内标注墨）
  <out>/figcrop_report.md      人读表 + 合计与「比原框更差」清单
  <cut-dir>/<prefix>NN_pNNN.png  逐幅裁图（--cut-dir 时）

口径说明
--------
**cut（切图墨）** = 框外 0..16px 窄带内的「图墨」像素数。>0 表示框线切过图的内容（**少裁**），是主排序键。
**tin（框内段落墨）** = 框内「段落正文」的实墨像素数。>0 表示把正文裁了进来（**多裁**）。
**sin（框内标注墨）** = 框内「图内标注」的实墨像素数 —— **属图内容，正常应 >0**，不是缺陷。

裁切代价不对称：少裁（多留白）只是难看，多裁（切掉图角/标注）是错。
故本工具默认在尽量准的前提下偏保守（净空自检阈值低、外扩上限 30pt、四边另加 2pt 余量）。
"""
import io
import sys
import os
import json
import argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _D)
import importlib.util
import pymupdf
# 07d 以数字开头、非合法模块名（技能侧「数字前缀 = 独立 CLI」约定所致），故按路径 importlib 加载
_spec = importlib.util.spec_from_file_location("figcrop_core", os.path.join(_D, "07d_figcrop_core.py"))
FC = importlib.util.module_from_spec(_spec)
sys.modules["figcrop_core"] = FC
_spec.loader.exec_module(FC)


def load_candidates(path):
    """→ [(k, phys, rect|None)]"""
    d = json.load(open(path, encoding="utf-8"))
    recs = d.get("records") if isinstance(d, dict) else d
    out = []
    for r in recs or []:
        ph = r.get("phys") or r.get("page") or r.get("p")
        if ph is None:
            continue
        css = r.get("figures")
        if css:                                  # probe_pdf_figures.json 形态
            for j, f in enumerate(css):
                out.append((r.get("k", len(out) + 1) if len(css) == 1 else len(out) + 1,
                            int(ph), f.get("rect")))
        else:
            out.append((r.get("k", len(out) + 1), int(ph), r.get("rect")))
    out.sort(key=lambda x: (x[1], x[0]))
    return [(i + 1, ph, rc) for i, (_, ph, rc) in enumerate(out)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--in", dest="inp", default=None)
    ap.add_argument("--pages", default=None, help="无 --in 时用：逗号分隔 phys 页列表")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dpi", type=int, default=220, help="裁图 dpi（默认 220）")
    ap.add_argument("--cut-dir", default=None, help="裁图落盘目录")
    ap.add_argument("--prefix", default="fig")
    ap.add_argument("--page-offset", type=int, default=0,
                    help="book 页码 = phys + offset（写入报告，便于对 03 锚）")
    args = ap.parse_args()

    if args.inp:
        cands = load_candidates(args.inp)
    elif args.pages:
        cands = [(i + 1, int(p), None) for i, p in enumerate(args.pages.split(",")) if p.strip()]
    else:
        print("需要 --in 或 --pages"); return 2

    os.makedirs(args.out, exist_ok=True)
    if args.cut_dir:
        os.makedirs(args.cut_dir, exist_ok=True)

    doc = pymupdf.open(args.pdf)
    out = []
    t_oc = t_nc = t_ot = t_nt = 0
    worse = []
    print(f"{'k':>3} {'phys':>5} | {'原切墨':>6} {'新切墨':>6} | {'原段墨':>6} {'新段墨':>6} | "
          f"{'新标墨':>6} | {'原框':>26} -> {'新框':<26}")
    for k, ph, rect_guess in cands:
        pg = doc[ph - 1]
        pairs = FC.page_lines_sz(pg)
        r = FC.refine(pg, rect_guess, pairs=pairs)
        if r.get("rect") is None:
            if rect_guess is None:
                print(f"{k:>3} {ph:>5} | 定位失败且无原框 → 跳过"); continue
            r["rect"] = [round(v, 1) for v in rect_guess]; r["note"] += "·用原框"
        ev_new = FC.evaluate(pg, r["rect"], pairs=pairs)
        ev_old = FC.evaluate(pg, rect_guess, pairs=pairs) if rect_guess else dict(cut=0, tin=0, sin=0)
        t_oc += ev_old["cut"]; t_nc += ev_new["cut"]; t_ot += ev_old["tin"]; t_nt += ev_new["tin"]
        flag = ""
        if rect_guess and ev_new["cut"] > max(30, ev_old["cut"] * 0.5):
            flag = " 新切墨更差"; worse.append((k, ph, ev_old["cut"], ev_new["cut"]))
        if ev_new["tin"] > 300:
            flag += " 带段落"
        print(f"{k:>3} {ph:>5} | {ev_old['cut']:>6} {ev_new['cut']:>6} | {ev_old['tin']:>6} {ev_new['tin']:>6} | "
              f"{ev_new['sin']:>6} | {str([round(v) for v in rect_guess]) if rect_guess else '—':>26} -> "
              f"{str([round(v) for v in r['rect']]):<26}{flag}")
        rec = dict(k=k, phys=ph, book=ph + args.page_offset, rect=r["rect"],
                   rect_guess=rect_guess, note=r["note"], eval=ev_new)
        out.append(rec)
        if args.cut_dir:
            fn = os.path.join(args.cut_dir, f"{args.prefix}{k:02d}_p{ph:03d}.png")
            pm = pg.get_pixmap(dpi=args.dpi, colorspace=pymupdf.csGRAY,
                               clip=pymupdf.Rect(*r["rect"]))
            pm.save(fn)
    doc.close()

    js = os.path.join(args.out, "figcrop_refined.json")
    json.dump(dict(pdf=args.pdf, dpi=FC.DPI, records=out),
              open(js, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"\n合计：切图墨 {t_oc} -> {t_nc}  ({(1 - t_nc / max(1, t_oc)) * 100:.1f}% 降)")
    print(f"      框内段落墨 {t_ot} -> {t_nt}  ({(1 - t_nt / max(1, t_ot)) * 100:.1f}% 降)")
    if worse:
        print("新切墨更差:", worse)
    print("写出 ->", js)
    return 0


if __name__ == "__main__":
    sys.exit(main())
