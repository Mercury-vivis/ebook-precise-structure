#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""07b_classify_figures.py —— 图版性质判别：线描地图/示意图 vs 实地照片（本库 v1）

背景
----
07_probe_page_figures.py 探出的候选图版页里，图题清一色以「图」结尾，
但其中一部分实为**实地照片**（如本书雁门关系列：关城、长城一角、碉堡、铁蒺藜）。
图题文本完全无法区分（都叫「XX图」），只能看**像素内容**。

判别原理（纯 Python，无 numpy/cv2）
--------------------------------
线描地图 / 示意图：白纸 + 墨线。特征——白像素占比高、灰度直方图双峰（熵低）、
  边缘锐利（相邻像素灰度跳变大）、有贯穿画面的长墨线（等高线/道路/框线）。
实地照片（网点印）：连续调灰、全幅纹理。特征——白像素占比低、熵高、
  边缘过渡软（中等梯度占比高）、几乎无长墨线（网点断裂）。

特征（对裁切 PNG 直接计算）：
  white   灰度>=220 像素占比
  ent     256 桶直方图香农熵 / 8（0~1）
  sharp   水平梯度 >=60 占比（锐边）
  mid     8<=梯度<60 占比（软过渡）
  lrun    含长暗游程的行占比（长墨线）。**阈值 = max(30px, 行宽 x 12%)**——30px 下限防止
          窄页上 12% 太小（整幅白纸边一行就能触发）；docstring 原先只写「>=12% 行宽」，窄页误判。

判定：
  photo  ：white < 0.45 或（ent >= 0.55 且 mid > sharp）
  line   ：其余（线描地图/示意图）

用法
----
  python scripts/07b_classify_figures.py --probe <probe_figures.json> --figdir <裁切图目录> --out <输出json>
  加 --check 输出人判真值对照（内置 16 张校准集）。

输出
----
  <out> JSON：每条记录每幅图的 verdict（photo / line）与特征值，供嵌入脚本取用。
"""
import argparse
import json
import math
import os
import sys

# ---- 解释器守卫（v1.30.0）：先切到 EBOOK_PY 指定的 venv，再 import 第三方库 ----
# 详见 _interp_guard.py；必须位于 import pymupdf 之前。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

import pymupdf

GRAD_SHARP = 60   # 锐边梯度阈值
GRAD_MID = 8      # 软过渡下限
WHITE_THR = 220   # 白像素阈值
WHITE_MIN = 0.45  # 白率低于此 → photo
ENT_THR = 0.55    # 熵高于此且软边占优 → photo
DARK = 128        # 长游程暗阈值
RUN_FRAC = 0.12   # 长游程 = 行宽 × 0.12

# 人判校准集：k=1 主图幅（2026-09-23 眼检 _figprobe_v1 裁切图）
GROUND_TRUTH = {
    "p0031_1": "line", "p0101_1": "line", "p0132_1": "line", "p0201_1": "line",
    "p0324_1": "line", "p0408_1": "line", "p0564_1": "line", "p0627_1": "line",
    "p0629_1": "line",
    "p0422_1": "photo", "p0429_1": "photo", "p0430_1": "photo", "p0431_1": "photo",
    "p0439_1": "photo", "p0446_1": "photo", "p0447_1": "photo",
}


def features(pm):
    s, w, h = pm.samples, pm.width, pm.height
    stride = pm.stride
    hist = [0] * 256
    total = 0
    for y in range(h):
        row = s[y * stride: y * stride + w]
        for v in row:
            hist[v] += 1
        total += w
    white = sum(hist[WHITE_THR:]) / total
    H = 0.0
    for c in hist:
        if c:
            p = c / total
            H -= p * math.log2(p)
    ent = H / 8.0
    sharp = mid = gt = 0
    for y in range(0, h, 2):          # 梯度隔行采样（省时，统计量稳定）
        row = s[y * stride: y * stride + w]
        prev = row[0]
        for x in range(1, w):
            v = row[x]
            d = v - prev
            if d < 0:
                d = -d
            if d >= GRAD_SHARP:
                sharp += 1
            elif d >= GRAD_MID:
                mid += 1
            gt += 1
            prev = v
    sharp_f = sharp / gt if gt else 0.0
    mid_f = mid / gt if gt else 0.0
    minrun = max(30, int(w * RUN_FRAC))
    runrows = 0
    for y in range(h):
        row = s[y * stride: y * stride + w]
        run = mx = 0
        for v in row:
            if v <= DARK:
                run += 1
                if run > mx:
                    mx = run
            else:
                run = 0
        if mx >= minrun:
            runrows += 1
    lrun = runrows / h
    return dict(white=round(white, 3), ent=round(ent, 3), sharp=round(sharp_f, 3),
                mid=round(mid_f, 3), lrun=round(lrun, 3))


def verdict(f):
    if f["white"] < WHITE_MIN:
        return "photo"
    if f["ent"] >= ENT_THR and f["mid"] > f["sharp"]:
        return "photo"
    return "line"


def load_gray(path):
    pm = pymupdf.Pixmap(path)
    if pm.colorspace and pm.colorspace.n != 1:
        pm = pymupdf.Pixmap(pymupdf.csGRAY, pm)
    return pm


def main():
    ap = argparse.ArgumentParser(description="图版性质判别：线描 vs 照片")
    ap.add_argument("--probe", required=True, help="probe_figures.json")
    ap.add_argument("--figdir", required=True, help="fig_pNNNN_k.png 所在目录")
    ap.add_argument("--out", required=True, help="输出 classify json 路径")
    ap.add_argument("--check", action="store_true", help="输出人判真值对照")
    args = ap.parse_args()

    probe = json.load(open(args.probe, encoding="utf-8"))
    out_recs = []
    n_line = n_photo = 0
    for rec in probe["records"]:
        figs = []
        for i, fg in enumerate(rec["figures"], 1):
            key = f"p{rec['phys']:04d}_{i}"
            path = os.path.join(args.figdir, f"fig_{key}.png")
            if not os.path.isfile(path):
                figs.append(dict(k=i, verdict="missing", note="无裁切图"))
                continue
            f = features(load_gray(path))
            v = verdict(f)
            figs.append(dict(k=i, verdict=v, features=f))
            if v == "line":
                n_line += 1
            else:
                n_photo += 1
        out_recs.append(dict(phys=rec["phys"], captions=[c["text"] for c in rec["captions"]],
                             note=rec["note"], figures=figs))
    json.dump(dict(source=args.probe, verdicts=out_recs),
              open(args.out, "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=2)

    print(f"共 {len(out_recs)} 页：line {n_line} 幅 / photo {n_photo} 幅 -> {args.out}")

    if args.check:
        ok = bad = 0
        print("\nkey        人工    机器    white  ent    sharp  mid    lrun")
        for rec in out_recs:
            for fg in rec["figures"]:
                key = f"p{rec['phys']:04d}_{fg['k']}"
                if key not in GROUND_TRUTH:
                    continue
                gt = GROUND_TRUTH[key]
                mv = fg["verdict"]
                mark = "✓" if gt == mv else "✗"
                if gt == mv:
                    ok += 1
                else:
                    bad += 1
                ft = fg.get("features", {})
                print(f"{key}  {gt:5s}  {mv:5s}  {mark}   "
                      f"{ft.get('white','-'):>5}  {ft.get('ent','-'):>5}  "
                      f"{ft.get('sharp','-'):>5}  {ft.get('mid','-'):>5}  {ft.get('lrun','-'):>5}")
        print(f"\n对照：一致 {ok} / 不一致 {bad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
