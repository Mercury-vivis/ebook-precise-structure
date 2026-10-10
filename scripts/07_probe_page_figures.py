#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""07_probe_page_figures.py —— 扫描版 PDF「内页图版」探测器（技能版 · ebook-precise-structure §6.4）

用途
----
对**整页扫描型** OCR-PDF（每页一张全页扫描图、OCR 文字层叠在其上），
定位书内图版（照片 / 线描地图 / 图表），产出：
  1) 候选图版页清单（页码、图题文本、图题行 bbox）
  2) 每页图幅外接矩形（分块连通域，支持一页多图）
  3) 按矩形裁剪的 PNG（供人视觉复核）
  4) JSON 清单 + Markdown 报告

为什么不能用常规办法
--------------------
- `page.get_image_info()`：整页扫描型每页只 1 张图、bbox 即整页 → 无法区分图文。
- `page.get_drawings()`：扫描件图框在像素里，矢量层恒为空。
- 「页面积占比≥35% 即图幅」：666 页全命中，判据失效。
- 「低文字密度」：文章尾页同样低密度（假阳性）。

本脚本采用的判据
----------------
A. 页级（文字层，廉价）：**图题行**（两形态并取，见 `_is_caption_line`）——
   A) 以「图」结尾（…示意图/地形图），长度 6–32、不含句末标点；
   B) 以「图+数字」开头（「图2接敌运动」「图3：德军对弗隆岱（Flanders）的反击作战」），
      长度 4–32、剔正文引用（「图1所示」「图2中」「图(1)…」）；
   共用：行宽 < 页宽×0.6、非「数字.」目录条目、非「地图…」插图目录分组标题、先剥尾部混排页码。
   **图题源可插拔**：`--text-source pdf|ocr|auto`——自带文字层好的书用 `pdf`（图题带 bbox）；
   自带层劣质（须外部重 OCR）的书用 `--ocr-text <结构化文本>`（图题无 bbox，仍可用于筛页/报告）；
   `auto`（默认）在提供 `--ocr-text` 时**优先 ocr**、缺则回退 pdf（重 OCR 过 ⇒ 自带层不可靠）。
B. 图幅级（像素层，仅对候选页）：分块暗率连通域，取面积前 K 个连通区为图幅外接矩形。
B'. **无图题回退**（`--dense-fallback auto|on|off`，默认 auto）：对未命中图题的页做低 dpi 大块图区
   扫描（最大连通区面积占比 ≥`--dense-min-area` 且 平均暗率 ≥`--dense-min-dens`）→ 补出
   「图题嵌在图内/无图题」的图版页（标注 `caption_source=none`）。**auto 仅在「图题命中页数
   < `--dense-trigger`（默认 3）」时自动启用**——图题完好的书不会被回退页淹没。
C. 图题方向：**文字层不可判**（竖排/横排图题均被 OCR 压成约 20×20pt 方框）。
   本脚本只给「图题行 bbox 宽高比」作参考量，并显式标注 unreliable；
   最终方向须人看图；核验用 `--overlay` 的叠框整页图，人工矩形经 `--rect-json` 回灌。
D. **人工矩形**（`--rect-json`）：自动探不到的图，人工给页号 + 矩形（可选图题文本）直接采用。
E. **低置信待核清册**：无图幅／回退命中／方向 unknown／近整页／图题多于图幅 → `probe_review.md`。

用法
----
  python scripts/07_probe_page_figures.py --pdf <PDF路径> --out <输出目录>
  python scripts/07_probe_page_figures.py --pdf X.pdf --out D --dpi 200 --limit 3
  python scripts/07_probe_page_figures.py --pdf X.pdf --out D --anchor-md <03文件>
  python scripts/07_probe_page_figures.py --pdf X.pdf --out D --text-source ocr --ocr-text <struct.txt>
  python scripts/07_probe_page_figures.py --pdf X.pdf --out D --dense-fallback off --rect-json <rects.json>

输出
----
  <out>/probe_figures.json    机器可读清单（含 caption_source / dense）
  <out>/probe_report.md       人读报告
  <out>/probe_review.md       低置信待人工核验清单
  <out>/fig_pNNNN_k.png       图幅裁剪图（k 从 1 起，按面积降序）
  <out>/full_pNNNN.png        整页图（供看版式）

依赖：仅 pymupdf + 标准库（无 numpy/PIL/opencv）。
"""
import argparse
import json
import os
import re
import statistics
import sys
import time

import pymupdf

# ---------- 图题行判据 ----------
CAP_TAIL = ("图", "圖", "圗", "圈")   # 形态A：图题以「图」结尾（…示意图/地形图），把「图」当尾词
CAP_START = re.compile(r"^[图圈圖圗][〇一二三四五六七八九十百廿卅0-9０-９]")  # 形态B：以「图+数字」开头
SENT_CHARS = set("。，,；;：:！？!?")   # 句末标点：出现即判为正文行（顿号「、」不算）
TOC_PREFIX = re.compile(r"^\s*[0-9０-９]+\s*[.、,，。]")  # 「12.刘宋时期…」目录条目
HAN = re.compile(r"[\u4e00-\u9fff]")
TAIL_PAGENUM = re.compile(r"[\s.。·‧]*\d{1,4}\s*$")   # 图题尾部混排的页脚页码（OCR 把图题与页码并到一行）
FIG_REF_HEAD = set("所表见说显给列如即中里内字")          # 正文图号引用：「图1所示」「图2中」…
FIG_REF_WORDS = ("所示", "表示", "说明", "显示", "给出", "列出", "如下", "即为", "参见", "详见")


def normalize_caption(tt):
    """图题文本规范化：剥掉 OCR 混排到图题尾部的页脚页码。"""
    return TAIL_PAGENUM.sub("", nz(tt))


def _is_caption_line(tt, min_len=6, max_len=32):
    """图题行判定：两形态并取（供 PDF 文字层与外部 OCR 文本两路共用）。
    A) 以「图」结尾（“…示意图/地形图”，把“图”当尾词）：长度 min_len–max_len、不含句末标点。
    B) 以「图+数字」开头（“图2接敌运动”、“图3：德军对弗隆岱（Flanders）的反击作战”）：长度 4–max_len。
    共用过滤：非汉字行、目录条目（“12.”前缀）、插图目录分组标题（“地图…”）、
    尾部混排页码（先剥）、正文图号引用（“图1所示/图2中…”）、含“图(”的多分句引用。"""
    tt = normalize_caption(tt)
    if not tt or not HAN.search(tt) or TOC_PREFIX.match(tt) or tt.startswith("地图"):
        return False
    L = len(tt)
    m = CAP_START.match(tt)
    if m:                                        # 以「图+数字」开头 → 只走形态B（自带反引用判据，更严）
        if not (4 <= L <= max_len):              # 长度<4 滤“图1/图2”图例标签
            return False
        if "图(" in tt or "图（" in tt:           # 正文“图(1)表示…”多分句引用
            return False
        if tt[m.end():m.end() + 1] in FIG_REF_HEAD:   # “图1所示/图2中…”正文引用
            return False
        if any(w in tt for w in FIG_REF_WORDS):
            return False
        return True
    if tt.endswith(CAP_TAIL):                    # 形态A：以「图/圖/圗/圈」结尾（开头无图号）
        if not (min_len <= L <= max_len):
            return False
        if any(c in SENT_CHARS for c in tt):     # 不允许句末标点
            return False
        return True
    return False

# ---------- 像素层参数 ----------
BLOCK = 8        # 分块边长（像素）
DARK_THR = 180   # 灰度暗阈值
BLOCK_FRAC = 0.03  # 块内暗像素占比超过此值 → 该块为「暗块」
MIN_AREA_FRAC = 0.002  # 连通区最小面积（占总块数）


def nz(s):
    return re.sub(r"\s+", "", s or "")


def find_captions(page, page_w, min_len=6, max_len=32, width_frac=0.6, toc_max=3):
    """在**PDF 自带文字层**找图题行。返回 (caps, toc_lines)：命中图题行则 caps 非空；
    同页命中数 >= toc_max 视为插图目录页，caps 为空、toc_lines 为命中行（供报告列出）。"""
    out = []
    d = page.get_text("dict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for l in b.get("lines", []):
            raw = "".join(s["text"] for s in l.get("spans", []))
            tt = normalize_caption(raw)
            x0, y0, x1, y1 = l["bbox"]
            w, h = x1 - x0, y1 - y0
            if w > page_w * width_frac:      # 整行宽 = 正文行
                continue
            if not _is_caption_line(tt, min_len, max_len):
                continue
            out.append(dict(text=tt, bbox=[round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
                            w=round(w, 1), h=round(h, 1)))
    # 目录页判据：同页图题行过多（插图目录页会列几十条，且分组标题无序号前缀）
    if len(out) >= toc_max:
        return [], out
    return out, []


# ---------- 外部 OCR 文本（图题源可插拔） ----------
def parse_ocr_pages(path):
    """解析外部 OCR 结构化文本 → ({phys: [行文本, ...]}, {phys: book})。
    锚格式兼容本库体例 `<<<PAGE phys=N book=M>>>`；无锚的行归到 phys=0（全局桶）。"""
    pages, books, cur = {}, {}, 0
    pat = re.compile(r"<<<PAGE\s+phys=(\d+)(?:\s+book=([^\s>]+))?")
    with open(path, encoding="utf-8", errors="strict") as f:  # P1-12（2026-10-04）：strict 暴露真实解码损失，不再静默掩错
        for line in f:
            m = pat.search(line)
            if m:
                cur = int(m.group(1))
                pages.setdefault(cur, [])
                if m.group(2):
                    books[cur] = m.group(2).strip()
                continue
            t = line.strip()
            if t:
                pages.setdefault(cur, []).append(t)
    return pages, books


def captions_from_ocr(pages, phys, min_len=6, max_len=32, toc_max=3):
    """从外部 OCR 文本的某一页行里找图题（**无 bbox**，仅文本；宽度过滤不适用）。"""
    out = []
    for t in pages.get(phys, []):
        tt = normalize_caption(t)
        if not _is_caption_line(tt, min_len, max_len):
            continue
        out.append(dict(text=tt, bbox=None, w=None, h=None))
    if len(out) >= toc_max:                  # 一页图题过多 → 疑插图目录页，排除
        return [], out
    return out, []


# ---------- 无图题回退（低分辨率大块图区） ----------
def dense_figure(page, dpi=50, block=6, dark=DARK_THR,
                 min_area=0.12, min_dens=0.25, edge_frac=0.05):
    """低 dpi 分块暗率连通域 → 找「大块图区」。用于**无图题页**的图版回退探测。
    返回 (rect_pt, area_frac, density) 或 None。
    area_frac = 最大连通区块数 / 全页块数；density = 该区各块暗率的均值。
    照片/密集线图区面积大且密度高；纯文字页的「大块区」稀疏、密度低。"""
    pm = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    s, w, h, st = pm.samples, pm.width, pm.height, pm.stride
    nx, ny = w // block, h // block
    if nx < 3 or ny < 3:
        return None
    thr = bytes(1 if i < dark else 0 for i in range(256))
    dens = [0.0] * (nx * ny)
    blot = bytearray(nx * ny)
    x_lo, x_hi = int(nx * edge_frac), max(int(nx * (1 - edge_frac)), 1)
    y_lo, y_hi = int(ny * edge_frac), max(int(ny * (1 - edge_frac)), 1)
    for by in range(ny):
        for bx in range(nx):
            base = by * block * st + bx * block
            cnt = 0
            for r in range(block):
                off = base + r * st
                cnt += int.from_bytes(s[off:off + block].translate(thr), "big").bit_count()
            d = cnt / float(block * block)
            dens[by * nx + bx] = d
            if x_lo <= bx < x_hi and y_lo <= by < y_hi and d >= 0.05:   # 有墨的块
                blot[by * nx + bx] = 1
    seen = bytearray(nx * ny)
    best = None
    for y0 in range(ny):
        for x0 in range(nx):
            i0 = y0 * nx + x0
            if not blot[i0] or seen[i0]:
                continue
            stk = [i0]
            seen[i0] = 1
            n = 0
            mnx = mxx = x0
            mny = mxy = y0
            dsum = 0.0
            while stk:
                i = stk.pop()
                n += 1
                x, y = i % nx, i // nx
                dsum += dens[i]
                if x < mnx: mnx = x
                if x > mxx: mxx = x
                if y < mny: mny = y
                if y > mxy: mxy = y
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    X, Y = x + dx, y + dy
                    if 0 <= X < nx and 0 <= Y < ny:
                        j = Y * nx + X
                        if blot[j] and not seen[j]:
                            seen[j] = 1
                            stk.append(j)
            af = n / float(nx * ny)
            if best is None or af > best[1]:
                best = ((mnx, mny, mxx, mxy), af, dsum / max(1, n))
    if best is None:
        return None
    box, af, dd = best
    if af < min_area or dd < min_dens:
        return None
    sc = 72.0 / dpi
    rect = [round(box[0] * block * sc, 1), round(box[1] * block * sc, 1),
            round((box[2] + 1) * block * sc, 1), round((box[3] + 1) * block * sc, 1)]
    return rect, round(af, 3), round(dd, 3)


# ---------- 人工矩形 ----------
def load_rect_json(path):
    """人工指定矩形 JSON → {phys: [dict(rect=..., caption=...), ...]}。
    格式：{"pages":[{"phys":27,"rect":[x0,y0,x1,y1],"caption":"图1：…"}]}
    或直接 [{"phys":..,"rect":..}, ...]。用于自动探不到的图。"""
    d = json.load(open(path, encoding="utf-8"))
    items = d.get("pages") if isinstance(d, dict) else d
    out = {}
    for it in items or []:
        ph = it.get("phys") or it.get("page") or it.get("p")
        rc = it.get("rect")
        if ph is None or not rc:
            continue
        out.setdefault(int(ph), []).append(
            dict(rect=[float(v) for v in rc], caption=it.get("caption") or it.get("text") or ""))
    return out


# ---------- 低置信判定 ----------
def review_flags(rec, page_w, page_h):
    """返回需人工核验的标记列表（空 = 高置信）。"""
    fl = []
    if not rec["figures"]:
        fl.append("无图幅")
    elif rec["caption_source"] == "none":
        fl.append("回退命中(无图题)")
    if rec["note"]["hint"] == "unknown":
        fl.append("方向unknown")
    if len(rec["captions"]) > len(rec["figures"]):
        fl.append("图题多于图幅")
    if rec["figures"]:
        fg = rec["figures"][0]
        area = (fg["rect"][2] - fg["rect"][0]) * (fg["rect"][3] - fg["rect"][1])
        if page_w and page_h and area / (page_w * page_h) > 0.90:
            fl.append("近整页(疑)")
    return fl


def page_stats(page):
    """页级文字统计：汉字数 / 文本块数 / 平均块字数 / 最长块 / 文本覆盖率"""
    d = page.get_text("dict")
    blocks = [b for b in d.get("blocks", []) if b.get("type") == 0]
    txt = page.get_text("text")
    nhan = sum(1 for c in txt if "\u4e00" <= c <= "\u9fff")
    lens = []
    for b in blocks:
        t = nz("".join("".join(s["text"] for s in l.get("spans", [])) for l in b.get("lines", [])))
        if t:
            lens.append(len(t))
    avg = round(sum(lens) / len(lens), 1) if lens else 0.0
    return dict(han=nhan, blocks=len(blocks), avg_block=avg,
                max_block=(max(lens) if lens else 0))


def render_gray(page, dpi):
    return page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)


def region_boxes(pm, block=BLOCK, thr=DARK_THR, frac=BLOCK_FRAC):
    """分块暗率连通域 → 面积降序的外接矩形（像素坐标）。"""
    s, w, h = pm.samples, pm.width, pm.height
    tr = bytes(1 if i < thr else 0 for i in range(256))
    nx, ny = w // block, h // block
    if nx < 2 or ny < 2:
        return []
    dark = bytearray(nx * ny)
    for by in range(ny):
        base = by * block * w
        for bx in range(nx):
            cnt = 0
            for r in range(block):
                off = base + r * w + bx * block
                seg = s[off:off + block]
                cnt += int.from_bytes(seg.translate(tr), "big").bit_count()
            if cnt > block * block * frac:
                dark[by * nx + bx] = 1
    seen = bytearray(nx * ny)
    regions = []
    for y0 in range(ny):
        for x0 in range(nx):
            i0 = y0 * nx + x0
            if not dark[i0] or seen[i0]:
                continue
            st = [i0]
            seen[i0] = 1
            n = 0
            mnx = mxx = x0
            mny = mxy = y0
            while st:
                i = st.pop()
                n += 1
                x, y = i % nx, i // nx
                if x < mnx: mnx = x
                if x > mxx: mxx = x
                if y < mny: mny = y
                if y > mxy: mxy = y
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    X, Y = x + dx, y + dy
                    if 0 <= X < nx and 0 <= Y < ny:
                        j = Y * nx + X
                        if dark[j] and not seen[j]:
                            seen[j] = 1
                            st.append(j)
            regions.append((n, mnx, mny, mxx, mxy))
    regions.sort(key=lambda r: -r[0])
    min_cells = max(1, int(nx * ny * MIN_AREA_FRAC))
    return [r for r in regions if r[0] >= min_cells]


def note_stats(page, min_len=2, max_len=9, min_samples=8):
    """地图注记文字块的宽高比统计 —— 判定地图是否「旋转 90° 放置」。

    原理：地名注记是横排小字。地图正向时注记块宽扁（w/h≈2.5–3.0）；
    整图旋转 90° 后注记随之竖排，块变方或窄高（w/h≈1.0）。
    实测《河山集·四集》：正向页 2.60–2.89，旋转页 p324 = 1.03（唯一异类）。
    """
    d = page.get_text("dict")
    rats = []
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for l in b.get("lines", []):
            t = nz("".join(s["text"] for s in l.get("spans", [])))
            if not (min_len <= len(t) <= max_len):
                continue
            if TOC_PREFIX.match(t) or not HAN.search(t):
                continue
            x0, y0, x1, y1 = l["bbox"]
            w, h = x1 - x0, y1 - y0
            if w <= 0 or h <= 0:
                continue
            rats.append(w / h)
    if len(rats) < min_samples:
        return dict(n=len(rats), median=None, hint="unknown")
    med = round(statistics.median(rats), 2)
    hint = "正向" if med >= 1.8 else ("旋转90°" if med <= 1.3 else "unknown")
    return dict(n=len(rats), median=med, hint=hint)


def content_orientation(rect, rot_hint):
    """按「是否旋转」把图幅外接矩形的横竖换算成地图内容方向。"""
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    if h <= 0:
        return "?", "?"
    r = w / h
    raw = "横" if r >= 1.25 else ("竖" if r <= 0.8 else "近方")
    if rot_hint == "旋转90°":
        return ("竖" if raw == "横" else ("横" if raw == "竖" else "近方")), raw
    return raw, raw


def overlay_page(page, figs, caps, out_path, dpi=100):
    """在整页上画出图幅框（红）与图题框（蓝），供一眼核验 bbox 是否套准。"""
    shape = page.new_shape()
    for f in figs:
        shape.draw_rect(pymupdf.Rect(*f["rect"]))
        shape.finish(color=(1, 0, 0), width=1.5)
    for c in caps:
        if not c.get("bbox"):          # OCR 源无坐标 → 不画
            continue
        shape.draw_rect(pymupdf.Rect(*c["bbox"]))
        shape.finish(color=(0, 0, 1), width=1.0)
    shape.commit()
    page.get_pixmap(dpi=dpi).save(out_path)


def px_to_pt(box_px, block, dpi):
    sc = 72.0 / dpi
    n, x0, y0, x1, y1 = box_px
    return dict(cells=n,
                rect=[round(x0 * block * sc, 1), round(y0 * block * sc, 1),
                      round((x1 + 1) * block * sc, 1), round((y1 + 1) * block * sc, 1)])


def aspect_tag(rect):
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    if h <= 0:
        return "?", 0.0
    r = round(w / h, 2)
    tag = "横" if r >= 1.25 else ("竖" if r <= 0.8 else "近方")
    return tag, r


def caption_dir_hint(cap):
    """图题方向参考量（不可靠，仅供交叉参考）。OCR 源无 bbox → unknown。"""
    if not cap.get("h") or not cap.get("w"):
        return "unknown", 0.0
    r = round(cap["w"] / cap["h"], 2)
    if r >= 2.5:
        return "横排?", r
    if r <= 0.8:
        return "竖排?", r
    return "unknown", r


def parse_anchor_map(md_path):
    """从 03 文件解析 phys→book 映射。"""
    pat = re.compile(r"<<<PAGE phys=(\d+)\s+book=([^>]+?)>>>")
    m = {}
    try:
        with open(md_path, encoding="utf-8") as f:
            for line in f:
                for g in pat.finditer(line):
                    m[int(g.group(1))] = g.group(2).strip()
    except OSError:
        return {}
    return m


def main():
    ap = argparse.ArgumentParser(description="扫描版 PDF 内页图版探测器")
    ap.add_argument("--pdf", required=True, help="PDF 路径")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--dpi", type=int, default=150, help="渲染 dpi（默认 150）")
    ap.add_argument("--cut-dpi", type=int, default=200, help="裁剪图 dpi（默认 200）")
    ap.add_argument("--pages", default="", help="页范围，如 1-50,120,300-")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个候选页（调试）")
    ap.add_argument("--top-regions", type=int, default=3, help="每页最多保留的连通区数（默认 3）")
    ap.add_argument("--anchor-md", default="", help="03 文件，用于 phys→book 映射")
    ap.add_argument("--toc-max-lines", type=int, default=3,
                    help="同页图题行 ≥ 此值即判为插图目录页并排除（默认 3）")
    ap.add_argument("--overlay", action="store_true",
                    help="另存叠加框整页图 overlay_pNNNN.png（红=图幅、蓝=图题），供核验 bbox")
    ap.add_argument("--text-source", choices=["auto", "pdf", "ocr"], default="auto",
                    help="图题来源：pdf=自带文字层（有 bbox）；ocr=外部 OCR 文本（无 bbox）；"
                         "auto=提供 --ocr-text 时优先 ocr、缺则回退 pdf（默认 auto）")
    ap.add_argument("--ocr-text", default="",
                    help="外部 OCR 结构化文本（带 <<<PAGE phys=N [book=M]>>> 锚），供 --text-source ocr/auto")
    ap.add_argument("--dense-fallback", choices=["auto", "on", "off"], default="auto",
                    help="无图题页用大块图区回退探测：auto=图题命中过少时自动启用（默认）/ on / off")
    ap.add_argument("--dense-trigger", type=int, default=3,
                    help="auto 模式：图题命中页数 < 此值则启用回退（默认 3）")
    ap.add_argument("--dense-dpi", type=int, default=50, help="回退探测渲染 dpi（默认 50）")
    ap.add_argument("--dense-min-area", type=float, default=0.12,
                    help="回退命中：最大连通区面积占比下限（默认 0.12）")
    ap.add_argument("--dense-min-dens", type=float, default=0.25,
                    help="回退命中：区内平均暗率下限（默认 0.25）")
    ap.add_argument("--rect-json", default="", help="人工矩形 JSON（自动探不到的图）")
    args = ap.parse_args()

    if not os.path.isfile(args.pdf):
        print("PDF 不存在:", args.pdf)
        return 2
    if args.ocr_text and not os.path.isfile(args.ocr_text):
        print("OCR 文本不存在:", args.ocr_text)
        return 2
    if args.rect_json and not os.path.isfile(args.rect_json):
        print("矩形 JSON 不存在:", args.rect_json)
        return 2
    os.makedirs(args.out, exist_ok=True)

    rng = set()
    if args.pages:
        for part in args.pages.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                a = int(a) if a else 1
                b = int(b) if b else 10 ** 9
                rng.update(range(a, b + 1))
            else:
                rng.add(int(part))

    bookmap = parse_anchor_map(args.anchor_md) if args.anchor_md else {}
    ocr_pages, ocr_books = parse_ocr_pages(args.ocr_text) if args.ocr_text else ({}, {})
    rects_manual = load_rect_json(args.rect_json) if args.rect_json else {}

    doc = pymupdf.open(args.pdf)
    t0 = time.time()
    page_w = doc[0].rect.width

    # ---- 第一遍：图题筛候选页（源可插拔）----
    cands = []
    toc_pages = []
    n_pdf = n_ocr = 0
    for i, page in enumerate(doc, 1):
        if rng and i not in rng:
            continue
        caps, tocl, src = [], [], ""
        # auto：提供了外部 OCR 文本时**优先 ocr**（说明做过重 OCR、自带层不可靠），缺则回退 pdf；
        # 无外部文本则用 pdf。text_source=ocr/pdf 时各自强制单源。
        if args.text_source == "ocr" or (args.text_source == "auto" and ocr_pages):
            caps, tocl = captions_from_ocr(ocr_pages, i, toc_max=args.toc_max_lines)
            if caps:
                src = "ocr"
        if not caps and not tocl and args.text_source != "ocr":
            caps, tocl = find_captions(page, page.rect.width, toc_max=args.toc_max_lines)
            if caps:
                src = "pdf"
        if tocl:
            toc_pages.append(dict(phys=i, n=len(tocl)))
            continue
        if not caps:
            continue
        n_pdf += (src == "pdf")
        n_ocr += (src == "ocr")
        cands.append(dict(phys=i, caps=caps, stats=page_stats(page), caption_source=src))
    print(f"[1/2] 图题筛页完成：候选 {len(cands)} 页、{sum(len(c['caps']) for c in cands)} 条图题行"
          f"（PDF 源 {n_pdf} 页 / OCR 源 {n_ocr} 页）；判为插图目录页 {len(toc_pages)} 页  ({time.time()-t0:.1f}s)")

    # ---- 第一遍半：无图题回退（低 dpi 大块图区）----
    dense_on = (args.dense_fallback == "on") or \
               (args.dense_fallback == "auto" and len(cands) < args.dense_trigger)
    if dense_on:
        have = set(c["phys"] for c in cands) | set(rects_manual)
        t1 = time.time()
        n_dense = 0
        for i, page in enumerate(doc, 1):
            if rng and i not in rng:
                continue
            if i in have:
                continue
            dc = dense_figure(page, dpi=args.dense_dpi,
                              min_area=args.dense_min_area, min_dens=args.dense_min_dens)
            if dc:
                rect, af, dd = dc
                cands.append(dict(phys=i, caps=[], stats=page_stats(page),
                                  caption_source="none",
                                  dense=dict(rect=rect, area=af, density=dd)))
                n_dense += 1
        print(f"[1b] 无图题回退已启用（模式 {args.dense_fallback}，图题命中 {len(cands)-n_dense} 页"
              f"＜阈值 {args.dense_trigger}）：新增 {n_dense} 页  ({time.time()-t1:.1f}s)")
    else:
        why = "模式 off" if args.dense_fallback == "off" else \
            f"模式 auto，图题命中 {len(cands)} 页 ≥ 触发阈值 {args.dense_trigger}"
        print(f"[1b] 无图题回退未启用（{why}；如需强制请加 --dense-fallback on）")

    # 人工矩形页并入
    for ph in sorted(rects_manual):
        if any(c["phys"] == ph for c in cands):
            continue
        cands.append(dict(phys=ph, caps=[], stats=page_stats(doc[ph - 1]),
                          caption_source="manual", manual=rects_manual[ph]))

    cands.sort(key=lambda c: c["phys"])
    if args.limit:
        cands = cands[:args.limit]

    # ---- 第二遍：像素层定位图幅 ----
    results = []
    for n, c in enumerate(cands, 1):
        page = doc[c["phys"] - 1]
        manual = rects_manual.get(c["phys"])
        figs = []
        if manual:                                   # 人工矩形优先
            for m in manual:
                rect = [round(v, 1) for v in m["rect"]]
                tag, r = aspect_tag(rect)
                figs.append(dict(cells=0, rect=rect, aspect=tag, wh=r))
        else:
            pm = render_gray(page, args.dpi)
            for bx in region_boxes(pm)[:args.top_regions]:
                info = px_to_pt(bx, BLOCK, args.dpi)
                tag, r = aspect_tag(info["rect"])
                info["aspect"] = tag
                info["wh"] = r
                figs.append(info)
        if not figs and c.get("dense"):               # 回退：用大块图区 rect
            rect = c["dense"]["rect"]
            tag, r = aspect_tag(rect)
            figs.append(dict(cells=0, rect=rect, aspect=tag, wh=r))
        for k, f in enumerate(figs, 1):
            cpm = page.get_pixmap(dpi=args.cut_dpi, colorspace=pymupdf.csGRAY,
                                  clip=pymupdf.Rect(*f["rect"]))
            cpm.save(os.path.join(args.out, f"fig_p{c['phys']:04d}_{k}.png"))
        page.get_pixmap(dpi=args.dpi).save(os.path.join(args.out, f"full_p{c['phys']:04d}.png"))
        note = note_stats(page)
        for f in figs:
            f["content_dir"], f["aspect_raw"] = content_orientation(f["rect"], note["hint"])
        if args.overlay:
            overlay_page(page, figs, c["caps"],
                         os.path.join(args.out, f"overlay_p{c['phys']:04d}.png"), dpi=args.dpi)
        capdirs = [caption_dir_hint(cp)[0] for cp in c["caps"]]
        rec = dict(phys=c["phys"], book=(ocr_books.get(c["phys"]) or bookmap.get(c["phys"], "")),
                   captions=c["caps"], caption_dir_hint=capdirs, note=note,
                   stats=c["stats"], figures=figs,
                   caption_source=c.get("caption_source", "pdf"))
        if c.get("dense"):
            rec["dense"] = c["dense"]
        results.append(rec)
        print(f"    [{n}/{len(cands)}] p{c['phys']:4d} 图题{len(c['caps'])}({rec['caption_source']}) "
              f"图幅{len(figs)} {[f['aspect'] for f in figs]} 注记中位={note['median']} "
              f"方向={note['hint']}  ({time.time()-t0:.1f}s)")

    # ---- 输出 ----
    js = os.path.join(args.out, "probe_figures.json")
    with open(js, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dict(pdf=args.pdf, dpi=args.dpi, pages_scanned=doc.page_count,
                       text_source=args.text_source, ocr_text=args.ocr_text,
                       dense_fallback=dense_on,
                       candidates=len(results), toc_pages=toc_pages, records=results),
                  f, ensure_ascii=False, indent=2)

    pw, ph = doc[0].rect.width, doc[0].rect.height
    risk = [(r, review_flags(r, pw, ph)) for r in results]
    risk = [(r, fl) for r, fl in risk if fl]

    md = os.path.join(args.out, "probe_report.md")
    lines = []
    A = lines.append
    A("# 内页图版探测报告")
    A("")
    A(f"- 源 PDF：`{args.pdf}`")
    A(f"- 总页数：{doc.page_count}　候选图版页：**{len(results)}**　渲染 dpi：{args.dpi}")
    A(f"- 图题源：`{args.text_source}`"
      + (f"（外部 OCR 文本：`{args.ocr_text}`）" if args.ocr_text else "")
      + "；逐页来源见「来源」列（pdf=自带文字层有坐标 / ocr=外部文本无坐标 / none=无图题回退 / manual=人工矩形）")
    A("- 图题判据（两形态并取）：**A** 以「图」结尾、长度 6–32、不含句末标点；"
      "**B** 以「图+数字」开头、长度 4–32、剔正文引用（图1所示/图(1)…）；"
      f"共用：行宽<页宽×0.6、非目录条目、非「地图…」分组标题；同页 ≥{args.toc_max_lines} 条判目录页并排除")
    A(f"- 图幅：分块 {BLOCK}px 暗率连通域，取面积前 {args.top_regions} 个连通区"
      + (f"；无图题回退**已启用**（低 dpi 大块图区：面积 ≥{args.dense_min_area} 且 密度 ≥{args.dense_min_dens}）"
         if dense_on else "；无图题回退未启用（图题命中充足）"))
    A("- 地图朝向：注记小字块宽高比中位数（≥1.8 正向 / ≤1.3 疑旋转 90° / 样本<8 记 unknown）")
    A("- **「内容向」= 把地图转正后的图版方向**（即本库口径的「横版/竖版」）；旋转页已按 90° 换轴。"
      "图题在页面上的位置（左上/左下/右上/右下）须人看图——OCR 把竖排/横排图题都压成约 20×20pt 方框")
    if toc_pages:
        A(f"- 判为插图目录页（同页图题行 ≥{args.toc_max_lines} 条，已排除）："
          + "、".join(f"phys {t['phys']}（{t['n']} 条）" for t in toc_pages))
    A("")
    A("> **图题方向须人看图**：竖排与横排图题均被 OCR 压成约 20×20pt 方框，文字层不可判；"
      "表中 `图题向` 列仅为 bbox 宽高比参考量（OCR 源无坐标 → `—`）。"
      "**低置信页（无图幅／回退命中／方向 unknown／近整页／图题多于图幅）见 `probe_review.md`。**")
    A("")
    A("| phys | book | 来源 | 图题 | 图题框 w×h | 图题向 | 注记中位 | 地图向 | 图幅(pt) | 框向 | 内容向 | 汉字 | 块 |")
    A("|---:|---|---|---|---|---|---:|---|---|---|---|---:|---:|")
    for r in results:
        caps = " ／ ".join(c["text"] for c in r["captions"]) or "—"
        capbox = " ／ ".join((f"{c['w']}×{c['h']}" if c.get("w") is not None else "—")
                            for c in r["captions"]) or "—"
        capdir = " ／ ".join(r["caption_dir_hint"]) or "—"
        note = r["note"]
        nmed = "—" if note["median"] is None else f"{note['median']}（n={note['n']}）"
        fg = r["figures"][0] if r["figures"] else None
        frect = f"{fg['rect'][0]:.0f},{fg['rect'][1]:.0f},{fg['rect'][2]:.0f},{fg['rect'][3]:.0f}" if fg else "—"
        fdir = (f"{fg['aspect']}（+{len(r['figures'])-1}）" if fg and len(r["figures"]) > 1
                else (fg["aspect"] if fg else "—"))
        cdir = (f"{fg['content_dir']}" + ("（已换轴）" if fg and fg["content_dir"] != fg["aspect_raw"] else "")) if fg else "—"
        st = r["stats"]
        A(f"| {r['phys']} | {r['book']} | {r['caption_source']} | {caps} | {capbox} | {capdir} | {nmed} | {note['hint']} | "
          f"{frect} | {fdir} | {cdir} | {st['han']} | {st['blocks']} |")
    A("")
    A("## 复核用法")
    A("")
    A("1. 看 `full_pNNNN.png` 判版式（图题位置、横竖排、是否真图版）；")
    A("2. 看 `fig_pNNNN_k.png` 判裁切质量（是否含图框、有无截断）；")
    A("3. 结论写回 `probe_figures.json` 各记录的 `review` 字段（`real|false|shape_dir`）；")
    A("4. 待人工核验清单见 `probe_review.md`；自动探不到的图用 `--rect-json` 人工补。")
    A("")
    with open(md, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))

    # ---- 低置信待核清册 ----
    rv = os.path.join(args.out, "probe_review.md")
    L2 = []
    B = L2.append
    B("# 待人工核验清单（低置信）")
    B("")
    B(f"- 源 PDF：`{args.pdf}`　候选页 {len(results)}　待核 **{len(risk)}** 页")
    B("")
    if risk:
        B("| phys | book | 来源 | 图题 | 图幅数 | 标记 |")
        B("|---:|---|---|---|---:|---|")
        for r, fl in risk:
            caps = " ／ ".join(c["text"] for c in r["captions"]) or "—"
            B(f"| {r['phys']} | {r['book']} | {r['caption_source']} | {caps} | {len(r['figures'])} | {'、'.join(fl)} |")
    else:
        B("（无）")
    B("")
    with open(rv, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L2))

    doc.close()
    print(f"[2/2] 完成：{len(results)} 页候选，输出至 {args.out}  ({time.time()-t0:.1f}s)")
    print(f"      报告 {md}")
    print(f"      清单 {js}")
    print(f"      待核 {rv}（{len(risk)} 页）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
