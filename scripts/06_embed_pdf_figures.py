# -*- coding: utf-8 -*-
"""
06_embed_pdf_figures.py —— 图版还原（PDF 路径）：把 OCR-PDF 的图版页（地图/作战示意图）
原图提取出来，降采样后落到 03 同层 fig/ 子目录，并在 03 对应锚 <<<PAGE phys=N book=M>>> 之后插入图片引用。

归属：ebook-precise-structure **§6.4「图版嵌入工具链」**——本文件是 PDF 支**唯一真相源**
（产出库的 _tools/ 只留指针，不再保留副本）。

与 06b_embed_epub_figures.py 的分工：
  - 06b_embed_epub_figures.py ：EPUB 路径（从 EPUB zip 解 <img>，按「见图X-X」引用回填）
  - 本脚本                    ：PDF 路径（从 PDF 页渲染提图，按 <<<PAGE>>> 锚定位插入）

适用：扫描版 PDF 的图版页——这类页文字层只有地图标注的散字碎片（无占位符标记），
      属技能 §3.9「图版型（地图/图表）→ md 内嵌原图」。
原则：**纯插入、零改写**——不删不改原页任何散字，只在锚后插图片引用行（散字保留供检索）。

⚠️ 触发时机：必须在 assemble 生成 03 **之后**跑（assemble 整体重生成会把 03 重置为无图版）；
   顺序固定为 **assemble → 嵌图**，每次重跑 assemble 都要重嵌（本脚本幂等，重嵌安全）。

用法：
  python 06_embed_pdf_figures.py --pdf <PDF> --md <03.md> --pages 516-524 \
      [--out-dir <目录>] [--width 1300] [--quality 85] [--num-label 附图] [--apply]

  --pages   图版页物理页号，支持 "516-524" 或 "516,520,524"（必填；整页图版自动扫描见 §6.4 的 07_ 链）
  --out-dir 图片落地目录，默认 = <--md 所在目录>/fig（2026-10-19 收口：图归 fig/ 子目录，
            断链核验 check_book 认 fig/ 前缀；显式传参可覆盖）
  --width   输出图宽（px），默认 1300；--quality JPEG 质量，默认 85
  --num-label 图号前缀，默认「附图」（用于生成「附图1 成皋之战示意图」题注）
  不加 --apply 为 dry-run，只打印计划。

输出：
  - <out-dir>/fig_<num>.jpeg（默认 <03 所在目录>/fig/）
  - 在 md 每个图版页锚后插入 ![<num-label><num> <图题>](fig/fig_<num>.jpeg)
  - --apply 时先备份 md 为 *.bak-embed-figures（LF 写盘，幂等：新形态或存量裸名形态任一已嵌入即跳过）
"""
import os, re, argparse, time
from _common import ANCHOR_RE  # 2026-10-04 审查 P0-2：纯锚正则统一收敛到 _common（兼容 book=None）


def parse_pages(spec):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def split_blocks(md_text):
    """返回 [(phys, book, start_idx, end_idx, body_start_idx)]，body 从锚行之后开始。"""
    blocks = []
    ms = list(ANCHOR_RE.finditer(md_text))
    for i, m in enumerate(ms):
        start = m.end()
        end = ms[i + 1].start() if i + 1 < len(ms) else len(md_text)
        blocks.append((int(m.group(1)), m.group(2), m.start(), m.end(), start, end))
    return blocks


def guess_caption(body, num_label):
    """从页块 body 里取图号与图题：图号=「附图N」；图题=首个非空且非图号行。"""
    lines = [l.strip() for l in body.split("\n")]
    num = None
    m = re.search(r"%s\s*([0-9]+)" % re.escape(num_label), body)
    if m:
        num = m.group(1)
    title = ""
    for l in lines:
        if not l:
            continue
        if re.fullmatch(r"%s\s*[0-9]+" % re.escape(num_label), l):
            continue
        if l.startswith("![") and "](" in l:
            continue  # 已插入的图片引用行不是图题（防幂等重跑时 caption 被引用行污染）
        if len(l) <= 40 and not l.endswith("。"):
            title = l
            break
    return num, title


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--md", required=True)
    ap.add_argument("--pages", required=True, help='"516-524" 或 "516,520"')
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--width", type=int, default=1300)
    ap.add_argument("--quality", type=int, default=85)
    ap.add_argument("--num-label", default="附图")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    import pymupdf

    md_path = args.md
    out_dir = args.out_dir or os.path.join(os.path.dirname(md_path), "fig")  # 图归 03 同层 fig/ 子目录（2026-10-19 收口，断链核验认 fig/ 前缀）
    pages = parse_pages(args.pages)

    raw = open(md_path, "rb").read()
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    blocks = split_blocks(text)
    by_phys = {b[0]: b for b in blocks}

    doc = pymupdf.open(args.pdf)
    plan = []
    for ph in pages:
        if ph not in by_phys:
            print("  !! md 中无锚 phys=%d，跳过" % ph)
            continue
        phys, book, s0, s1, body_s, body_e = by_phys[ph]
        body = text[body_s:body_e]
        num, title = guess_caption(body, args.num_label)
        if not num:
            print("  !! phys=%d 未找到「%sN」图号，跳过" % (ph, args.num_label))
            continue
        fname = "fig_%s.jpeg" % num
        caption = "%s%s %s" % (args.num_label, num, title) if title else "%s%s" % (args.num_label, num)
        tag = "![%s](fig/%s)" % (caption, fname)  # 引用带 fig/ 前缀（2026-10-19 收口）
        # 幂等：按图号判（抗 caption 污染）——新形态（fig/ 前缀）或存量旧形态（裸名）
        # 任一已在正文即判已嵌入，不重复插图
        if ("](fig/%s)" % fname) in body or ("](%s)" % fname) in body:
            plan.append((ph, num, title, fname, None, "（已嵌入·跳过）"))
            continue
        plan.append((ph, num, title, fname, tag, ""))

    print("计划处理 %d 个图版页：\n" % len(plan))
    for ph, num, title, fname, tag, note in plan:
        print("  phys=%-4d 图号=%s%-2s 图题=%-22s -> %s  %s"
              % (ph, args.num_label, num, title[:20], fname, note))

    if not args.apply:
        print("\n[DRY-RUN] 未落库。加 --apply 执行。")
        return

    # 1) 提图
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for ph, num, title, fname, tag, note in plan:
        if tag is None:
            continue
        pg = doc[ph - 1]
        scale = args.width / pg.rect.width
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                            colorspace=pymupdf.csRGB, alpha=False)
        data = pix.tobytes("jpeg", jpg_quality=args.quality)
        dst = os.path.join(out_dir, fname)
        with open(dst, "wb") as f:
            f.write(data)
        made.append((fname, pix.width, pix.height, len(data)))
    for fname, w, h, n in made:
        print("  写出 %s  %dx%d  %d KB" % (fname, w, h, n // 1024))

    # 2) 备份 + 注入（按锚位置倒序插入，避免位移）
    from _common import backup_before_embed, safe_write  # 备份落点统一（_common.backup_before_embed）+ 原子写
    bak = backup_before_embed(md_path, suffix="bak-embed-figures")
    ins = []
    for ph, num, title, fname, tag, note in plan:
        if tag is None:
            continue
        ins.append((by_phys[ph][3], tag))  # 锚行结束位置
    new = text
    for pos, tag in sorted(ins, reverse=True):
        new = new[:pos] + "\n\n" + tag + new[pos:]
    safe_write(md_path, new, backup=False)  # 备份已由上方 backup_target 显式完成
    print("\n已注入 %d 处图片引用；md 已按 LF 写回。" % len(ins))


if __name__ == "__main__":
    main()
