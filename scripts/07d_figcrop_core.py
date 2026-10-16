#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""07d_figcrop_core.py —— 整页扫描型 PDF「页内图幅精确裁切」核心判据与定位

问题
----
整页扫描型 PDF（每页一张全页位图、OCR 文字层叠于其上）里没有局部图对象，
图幅只能靠像素裁切；而「图线 vs 正文文字」在像素层不可分（正文笔画与图线同为暗像素）。
天真的连通域法必然把正文长行当成图墨 → 框被拉到整页，或反之把图切掉一角。

思路：**用 OCR 文字层当掩膜**
-----------------------------
文字层给出每行文字的外接框与字号。据此把「页面文字」与「图内标注」分开：

  硬文字 hard    行宽 >= 0.55 × 页最大行宽              → 正文长行、注
  段落行 body    存在 ±(0.6~1.6) 行高、x 重叠 >=50% 的邻行，且过「段落性门槛」
                 段落性门槛 = 行宽 >= 0.35 页宽，或 文本 >= 6 字且空格 <=1
                 （三条通路任一成立即为段落行：过门槛的邻行 / 邻居里有硬文字行的段尾短行 /
                   无邻行但文本 >= 8 字的跨页续行）
  标题行 title   字号 > 1.15 × 全页中位字号 且（行宽 >= 0.25 页宽 或 水平居中）
  图题   cap     以「图」结尾 或「图N…」开头 且行宽 < 0.5 页宽（长度 4–32）
  页边距 marg    行底 < 7% 页高 或 行顶 > 93% 页高

  遮蔽 text_all  = hard ∪ body ∪ title ∪ cap ∪ marg    → 从 ink 中剔除（页面文字）
  软标注 soft    = 其余（图内标注字母、子图号、页码、杂字）→ **不遮蔽**，属图内容
  图墨   ink     = 暗像素 且 不在 text_all 内 = 图线笔画 + 图内软标注

**关键坑（实测教训）**：图内标注列（子图字母 e/f/d/b、字母行「E G A H F」）竖向排成一列、
彼此 x 重叠 >=50%，会被「邻行」判据整列误判为段落行 —— 后果有两重：
① 指标虚高（把图内标注算成「框内正文墨」）；② 标注被遮蔽 → 净空自检看不见 → 框边把标注切掉。
故段落性门槛必须**同时**挡住「短标注」（字少）与「字母行」（字距拉开的空格多、行宽虚大）。

定位
----
  种子：与「图题 x ±45pt」竖带相交、且在分区（图题上 0.55 页高）内、且**非文字行状**
        （高 <= 1.5 行高 且 宽 >= 3 倍高）的 ink 连通域；无图题页取区内最大图块。
  生长：链式并入与当前框 bbox 距离 <= 35pt 的任一 ink 连通域（治复合图、宽图）。
  回纳：并入与框相交的**图内标注行**（软标注 + 大字号但不够宽的字母行）。
  净空自检：框外以「带厚 = 步进」逐带外扩，带内墨 >= MIN_BAND_PX 即扩，每侧累计 <= 30pt；
       用的是「只遮蔽段落/图题/页边、**保留窄标题与图内标注**」的墨图，被切掉的一角能拉回。
  硬约束：框底边不得越过图题上沿（图题永不入框）；页边 5% 带一律不追。
  收尾：四边各加 PAD_PT 余量。

「宁可少裁」兜底（用户裁定）
--------------------------
裁切的代价不对称：少裁（多留白）只是难看，多裁（切掉图角/标注）是错。故默认在准确的前提下
**偏保守**：净空自检阈值取低（MIN_BAND_PX=3）、外扩上限 30pt、并加 PAD_PT=2 余量。
"""
# ---- 解释器守卫（v1.30.0）：先切到 EBOOK_PY 指定的 venv，再 import 第三方库 ----
# 详见 _interp_guard.py；必须位于 import numpy 之前。本模块亦被 07e 以 importlib 加载，
# 彼时 07e 的守卫已完成切换，此处为幂等兜底。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

import numpy as np

# ── 判据参数 ────────────────────────────────────────────────
WIDTH_RATIO = 0.55        # 硬文字：行宽 >= 0.55 × 页最大行宽
PARA_W_RATIO = 0.35       # 段落性：行宽 >= 0.35 × 页宽
PARA_MIN_CHARS = 6        # 段落性：或文本长度 >= 6 字（且空格 <= 1）
TITLE_W_RATIO = 0.25      # 标题性：行宽 >= 0.25 × 页宽
TITLE_CENTER_FRAC = 0.08  # 标题性：或水平居中（|行中心-页中心| < 8% 页宽）
SIZE_RATIO = 1.15         # 标题字号 > 1.15 × 全页中位字号
MARGIN_TOP = 0.07
MARGIN_BOT = 0.93

# ── 定位参数 ────────────────────────────────────────────────
DPI = 200
SC = DPI / 72.0
DARK = 200
PAD_PT = 2.0              # 框外统一余量
TEXT_PAD_PT = 4.0         # 文字遮蔽框外扩（防笔画余墨）
MIN_AREA_PX = 100
MIN_EXTENT_PT = 5.0       # 连通域最小边长（滤 3×3pt 级噪点）
DENOISE_PX = 6
SEED_STRIP_PT = 45.0
GROW_PT = 35.0
MAX_ZONE_FRAC = 0.55
PROBE_PT = 2.0
MIN_BAND_PX = 3
EXPAND_CAP_PT = 30.0
EDGE_FRAC = 0.05

CAP_START = None  # 延迟编译，见下


def _cap_re():
    global CAP_START
    if CAP_START is None:
        import re
        CAP_START = re.compile(r"^图[〇一二三四五六七八九十百廿卅0-9０-９]")
    return CAP_START


# ── 文字层 ──────────────────────────────────────────────────
def page_lines_sz(pg):
    """[(bbox, text, size)]"""
    rows = []
    for blk in pg.get_text("dict")["blocks"]:
        for ln in blk.get("lines", []):
            b = list(ln["bbox"])
            t = "".join(s["text"] for s in ln.get("spans", [])).strip()
            if not t:
                continue
            sz = max([s["size"] for s in ln.get("spans", [])] or [0.0])
            rows.append((b, t, sz))
    return rows


def is_caption_text(t):
    if not t:
        return False
    n = len(t)
    if n < 4 or n > 32:
        return False
    return t.endswith("图") or bool(_cap_re().match(t))


def is_paragraph_like(b, t, pw):
    """段落性门槛：行够宽，或文本够长且无字距拉开的空格
       （把图内短标注、字母行「E G A H F」排除在「段落」之外）。"""
    w = b[2] - b[0]
    if w >= pw * PARA_W_RATIO:
        return True
    return len(t) >= PARA_MIN_CHARS and t.count(" ") <= 1


def is_title_line(b, sz, med_sz, pw, t=""):
    if not (med_sz > 0 and sz > med_sz * SIZE_RATIO):
        return False
    if (b[2] - b[0]) >= pw * TITLE_W_RATIO:
        return True
    cx = (b[0] + b[2]) / 2.0
    return abs(cx - pw / 2.0) < pw * TITLE_CENTER_FRAC


def median_size(pairs):
    szs = sorted([s for _, _, s in pairs if s > 0])
    return szs[len(szs) // 2] if szs else 0.0


def body_flags(lines, pw=None):
    """段落行判别（三通路）。返回 (flags, med_h)。pw=None 时退化为纯邻行判据。"""
    hs = sorted([b[3] - b[1] for b, t in lines])
    med_h = hs[len(hs) // 2] if hs else 18.0
    maxw = max([b[2] - b[0] for b, t in lines] or [1.0])
    hthr = maxw * WIDTH_RATIO
    hard = [(b[2] - b[0]) >= hthr for b, t in lines]
    flags = []
    for i, (b, t) in enumerate(lines):
        w = max(1e-6, b[2] - b[0])
        nbs = []
        for j, (b2, t2) in enumerate(lines):
            if i == j:
                continue
            dt = abs(b2[1] - b[1])
            if not (0.6 * med_h <= dt <= 1.6 * med_h):
                continue
            ov = min(b[2], b2[2]) - max(b[0], b2[0])
            if ov <= 0:
                continue
            if ov >= 0.5 * min(w, max(1e-6, b2[2] - b2[0])):
                nbs.append(j)
        if not nbs:
            flags.append(bool(pw is not None and len(t) >= PARA_MIN_CHARS + 2))
            continue
        if pw is None:
            flags.append(True)
        else:
            flags.append(is_paragraph_like(b, t, pw) or any(hard[j] for j in nbs))
    return flags, med_h


def classify(pairs, pw, H_pt):
    """→ (text_all, para, cap_boxes, marg_boxes, soft_info)

       text_all  : 应遮蔽（段落/标题/图题/页边距）→ 从 ink 剔除
       para      : 指标口径「页面正文」（硬文字 ∪ 段落行 ∪ 够宽的真标题）
       cap_boxes : 图题框
       marg_boxes: 页眉页脚框
       soft_info : [(bbox, text)] 可回纳进框的图内标注（软标注 + 大字号窄字母行）
    """
    lines = [(b, t) for b, t, _ in pairs]
    maxw = max([b[2] - b[0] for b, t in lines] or [1.0])
    thr = maxw * WIDTH_RATIO
    flags, _ = body_flags(lines, pw)
    med_sz = median_size(pairs)
    text_all, para, cap_boxes, marg_boxes, soft_info = [], [], [], [], []
    for (b, t, sz), f in zip(pairs, flags):
        w = b[2] - b[0]
        is_cap = is_caption_text(t) and w < pw * 0.5
        is_marg = b[3] < H_pt * MARGIN_TOP or b[1] > H_pt * MARGIN_BOT
        is_hard = w >= thr
        is_title = is_title_line(b, sz, med_sz, pw, t)
        if is_hard or is_title or f or is_cap or is_marg:
            text_all.append(b)
        # 指标：标题字号的行不算段落（可能是图内字母行/子图标注）
        if is_hard or (f and not is_title) or (is_title and w >= pw * PARA_W_RATIO):
            para.append(b)
        if is_cap:
            cap_boxes.append(b)
        if is_marg:
            marg_boxes.append(b)
        is_narrow_title = is_title and not (is_hard or w >= pw * TITLE_W_RATIO)
        if not (is_hard or is_title or f or is_cap or is_marg) or is_narrow_title:
            soft_info.append((b, t))
    return text_all, para, cap_boxes, marg_boxes, soft_info


def box_mask(boxes, W, H, sc, pad_pt):
    m = np.zeros((H, W), dtype=bool)
    pd = int(round(pad_pt * sc))
    for b in boxes:
        x0 = max(0, int(b[0] * sc) - pd); x1 = min(W, int(b[2] * sc) + pd)
        y0 = max(0, int(b[1] * sc) - pd); y1 = min(H, int(b[3] * sc) + pd)
        if x1 > x0 and y1 > y0:
            m[y0:y1, x0:x1] = True
    return m


def gray(pg, dpi):
    import pymupdf
    pm = pg.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return (np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.stride)[:, :pm.width].copy(),
            pm.width, pm.height)


# ── 定位 ────────────────────────────────────────────────────
def _cv():
    import cv2
    return cv2


def locate(ink, arr, W, H, cap_bbox, tm, tm_salv, soft_info, pw, ztop_pt, H_pt, ex, med_h_pt=18.0):
    """返回精修后的 rect(pt)；失败返回 None。
       cap_bbox=None → 无图题页兜底：以区内最大图块为种子，走同一套生长/回纳/净空自检。"""
    cv2 = _cv()
    ztop = int(ztop_pt * SC)
    zbot = int(min(H - 1, cap_bbox[1] * SC - 1)) if cap_bbox else H - 1
    if zbot <= ztop:
        return None
    hl = max(8.0, 1.5 * med_h_pt * SC)

    n, lab, st, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    comp, seed_ok = {}, set()
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < MIN_AREA_PX or max(w, h) / SC < MIN_EXTENT_PT:
            continue
        if y + h < ztop or y > zbot:
            continue
        comp[i] = (x / SC, y / SC, (x + w) / SC, (y + h) / SC)
        if not (h <= hl and w >= 3.0 * h):   # 文字行状不作种子（仍可被生长并入）
            seed_ok.add(i)

    sel = set()
    if cap_bbox is None:
        big = [i for i in seed_ok if st[i][4] >= MIN_AREA_PX * 4] or list(seed_ok) or list(comp)
        if not big:
            return None
        sel = {max(big, key=lambda i: st[i][4])}
    else:
        for strip in (SEED_STRIP_PT, 110.0):
            sx0, sx1 = cap_bbox[0] - strip, cap_bbox[2] + strip
            for i in seed_ok:
                b = comp[i]
                if b[2] >= sx0 and b[0] <= sx1:
                    sel.add(i)
            if sel:
                break
        if not sel:
            cand = seed_ok or set(comp)
            if not cand:
                return None
            sel = {max(cand, key=lambda i: st[i][4])}

    def union(ids):
        return (min(comp[i][0] for i in ids), min(comp[i][1] for i in ids),
                max(comp[i][2] for i in ids), max(comp[i][3] for i in ids))

    for _ in range(40):                       # 链式生长
        bx = union(sel)
        add = set()
        for i, b in comp.items():
            if i in sel:
                continue
            dx = max(bx[0] - b[2], b[0] - bx[2], 0.0)
            dy = max(bx[1] - b[3], b[1] - bx[3], 0.0)
            if (dx * dx + dy * dy) ** 0.5 <= GROW_PT:
                add.add(i)
        if not add:
            break
        sel |= add
    box = list(union(sel))

    for _ in range(3):                        # 图内标注回纳
        grown = False
        for (b, t) in soft_info:
            if cap_bbox is not None and abs(b[0] - cap_bbox[0]) < 1 and abs(b[1] - cap_bbox[1]) < 1:
                continue
            if b[2] >= box[0] - 3 and b[0] <= box[2] + 3 and b[3] >= box[1] - 3 and b[1] <= box[3] + 3:
                box = [min(box[0], b[0] - 1), min(box[1], b[1] - 1),
                       max(box[2], b[2] + 1), max(box[3], b[3] + 1)]
                grown = True
        if not grown:
            break

    # 净空自检墨图：只遮蔽段落/图题/页边，保留窄标题与图内标注
    capm = box_mask([cap_bbox], W, H, SC, 3) if cap_bbox else np.zeros((H, W), dtype=bool)
    ink_all = (arr < DARK) & (~tm_salv) & (~capm) & (~ex)
    nn, ll, ss, _ = cv2.connectedComponentsWithStats(ink_all.astype(np.uint8), connectivity=8)
    keep = np.zeros(nn, dtype=np.uint8)
    for i in range(1, nn):
        if ss[i][4] >= DENOISE_PX:
            keep[i] = 1
    ink_all = keep[ll] > 0

    x0, y0 = int(box[0] * SC), int(box[1] * SC)
    x1, y1 = int(box[2] * SC), int(box[3] * SC)
    probe = max(2, int(PROBE_PT * SC))
    stp = probe * 2                           # 步进 = 带厚，外扩路径无盲区
    cap_px = int(EXPAND_CAP_PT * SC)
    acc = {"l": 0, "r": 0, "u": 0, "d": 0}
    for _ in range(60):
        ext = False
        if y0 > 0 and acc["u"] < cap_px:
            if int(ink_all[max(0, y0 - stp):y0, max(0, x0):max(1, x1)].sum()) >= MIN_BAND_PX:
                y0 = max(0, y0 - stp); acc["u"] += stp; ext = True
        if y1 < H and acc["d"] < cap_px:
            if int(ink_all[y1:min(H, y1 + stp), max(0, x0):max(1, x1)].sum()) >= MIN_BAND_PX:
                y1 = min(H, y1 + stp); acc["d"] += stp; ext = True
        if x0 > 0 and acc["l"] < cap_px:
            if int(ink_all[max(0, y0):max(1, y1), max(0, x0 - stp):x0].sum()) >= MIN_BAND_PX:
                x0 = max(0, x0 - stp); acc["l"] += stp; ext = True
        if x1 < W and acc["r"] < cap_px:
            if int(ink_all[max(0, y0):max(1, y1), x1:min(W, x1 + stp)].sum()) >= MIN_BAND_PX:
                x1 = min(W, x1 + stp); acc["r"] += stp; ext = True
        if not ext:
            break

    pad = int(PAD_PT * SC)
    rx0, ry0, rx1, ry1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
    if cap_bbox is not None:                  # 硬约束：图题永不入框
        ry1 = min(ry1, int(cap_bbox[1] * SC) - 1)
    rect = (rx0 / SC, ry0 / SC, rx1 / SC, ry1 / SC)
    if (rect[2] - rect[0]) < 20 or (rect[3] - rect[1]) < 15:
        return None
    return rect


def refine(pg, rect_guess=None, pairs=None):
    """对单页精修一幅图。rect_guess 用于挑最近的图题与分区；None 则走无图题兜底。
       返回 dict(rect=新框, cap=图题框|None, 说明)。"""
    import pymupdf
    arr, W, H = gray(pg, DPI)
    pw, H_pt = pg.rect.width, pg.rect.height
    if pairs is None:
        pairs = page_lines_sz(pg)
    lines = [(b, t) for b, t, _ in pairs]
    text_all, para, caps, margs, soft = classify(pairs, pw, H_pt)
    tm = box_mask(text_all, W, H, SC, TEXT_PAD_PT)
    tm_salv = box_mask(para + caps + margs, W, H, SC, TEXT_PAD_PT)
    ex = np.zeros((H, W), dtype=bool)
    ex[:int(H_pt * EDGE_FRAC * SC), :] = True
    ex[int(H * (1 - EDGE_FRAC)):, :] = True
    ex[:, :int(pw * EDGE_FRAC * SC)] = True
    ex[:, int(W * (1 - EDGE_FRAC)):] = True
    ink = (arr < DARK) & (~tm) & (~ex)
    _, med_h_pt = body_flags(lines, pw)

    caps_all = sorted([(b, t) for b, t in lines
                       if is_caption_text(t) and (b[2] - b[0]) < pw * 0.5], key=lambda x: x[0][1])
    note = ""
    cap = None
    if caps_all and rect_guess is not None:
        ocy = (rect_guess[1] + rect_guess[3]) / 2.0
        cap = min(caps_all, key=lambda c: abs((c[0][1] + c[0][3]) / 2.0 - ocy))
    if cap is None:
        note = "无图题·最大图块"
        rect = locate(ink, arr, W, H, None, tm, tm_salv, soft, pw, 0.0, H_pt, ex, med_h_pt)
    else:
        cb = cap[0]
        up = [c[0][3] for c in caps_all if c[0][3] <= cb[1] + 1 and c is not cap]
        ztop = max(up) if up else 0.0
        ztop = max(ztop, cb[1] - H_pt * MAX_ZONE_FRAC)
        rect = locate(ink, arr, W, H, cb, tm, tm_salv, soft, pw, ztop, H_pt, ex, med_h_pt)
        note = "图题锚定"
    if rect is None:
        return dict(rect=None, cap=cap, note="定位失败·回退原框", fallback=True)
    return dict(rect=[round(v, 1) for v in rect], cap=(cap[0] if cap else None), note=note)


def evaluate(pg, rect, pairs=None):
    """评估一个框：cut 切图墨（框外 0..16px 带内图墨，>0 = 切到内容）、
       tin 框内段落墨（>0 = 把正文裁进来）、sin 框内标注墨（图内标注，参考）。"""
    arr, W, H = gray(pg, DPI)
    pw, H_pt = pg.rect.width, pg.rect.height
    if pairs is None:
        pairs = page_lines_sz(pg)
    text_all, para, caps, margs, soft = classify(pairs, pw, H_pt)
    tm = box_mask(text_all, W, H, SC, 0)
    pm = box_mask(para, W, H, SC, 0)
    sm = box_mask([b for b, t in soft], W, H, SC, 0)

    def _dil(m, k):
        out = m.copy()
        for dy in (-k, 0, k):
            for dx in (-k, 0, k):
                out |= np.roll(np.roll(m, dy, axis=0), dx, axis=1)
        return out
    tm, pm, sm = _dil(tm, 2), _dil(pm, 2), _dil(sm, 2)
    ex = np.zeros((H, W), dtype=bool)
    ex[:int(H_pt * EDGE_FRAC * SC), :] = True
    ex[int(H * (1 - EDGE_FRAC)):, :] = True
    ex[:, :int(pw * EDGE_FRAC * SC)] = True
    ex[:, int(W * (1 - EDGE_FRAC)):] = True
    dark = arr < DARK
    fg = dark & (~tm) & (~ex)
    x0, y0, x1, y1 = [int(round(v * SC)) for v in rect]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W, x1), min(H, y1)
    B = 16
    cut = (int(fg[max(0, y0 - B):y0, x0:x1].sum()) + int(fg[y1:min(H, y1 + B), x0:x1].sum())
           + int(fg[y0:y1, max(0, x0 - B):x0].sum()) + int(fg[y0:y1, x1:min(W, x1 + B)].sum()))
    tin = int((pm[y0:y1, x0:x1] & dark[y0:y1, x0:x1]).sum())
    sin = int((sm[y0:y1, x0:x1] & dark[y0:y1, x0:x1]).sum())
    return dict(cut=cut, tin=tin, sin=sin)
