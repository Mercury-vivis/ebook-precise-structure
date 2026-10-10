# -*- coding: utf-8 -*-
"""
06c_embed_epub_slice_figs.py —— 图版还原（EPUB 切片型/占位对齐通道）。

归属：ebook-precise-structure §6.4「图版嵌入工具链」——EPUB 支第二通道，
与 06b_embed_epub_figures.py（图号/tuti 通道）互补：
  - 06b：03 文本里存有「图N/地图N」caption 行，按图号体系回填（〈西周的灭亡〉型）。
  - 06c（本脚本）：切片型 EPUB——版面含海量小图元（脚注星号、生僻字 svg 字形图），
    03 里所有 <img> 都坍缩为同一占位符〔原书图字·图片，文字层不可提取〕，无图号可依。
    （中华书局 2025 版 EPUB 为典型：3101 个 <img> 只有 132 张真图版，其余为小图元。）

机制（占位对齐）：
  1. 按 spine 序解析 EPUB，逐单元数 <img>；与 fulltext_struct 各 SEC 锚区间的占位符数
     逐一核对——必须全等（k↔k 对齐前提），不等则硬失败（不静默错位）。
  2. 真图版判据：src 以 .jpg/.jpeg 结尾且 zip 内体积 ≥ --min-size（默认 30000B）。
     小图元（注标 png、字形 svg、小 jpg）不嵌入，占位符原样保留。
  3. 图题：取 img 前最近的 <p> 段落文本（剥标签/▼▲装饰），≤60 字取为题；
     「续表」沿用上一真题 + 「（续）」；无题回退「图版」。
  4. 图片复制到 <md 所在目录>/imgs/ 子文件夹（自动建目录），文件名
     fig_<phys>_<单元内序>.<ext>；md 内占位符原位替换为 markdown 嵌入行
     ![题](imgs/fig_....jpg)——图片一律归 imgs/，不与正文散放同层（2026-09-29 老陈裁定）。

用法：06c_embed_epub_slice_figs.py <epub> <md> [--min-size 30000] [--apply]
dry-run 只打印计划；--apply 落库（自动备份 *.bak-embed-<mdstem>-<时间戳>，LF 写盘）。
"""
import os, re, zipfile, sys, shutil, time

PLACEHOLDER = "〔原书图字·图片，文字层不可提取〕"
IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"[^>]*>', re.I)
P_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.I | re.S)
# ANCHOR_RE 统一收敛到 _common（2026-10-04 审查 P0-2）：原 book=\d+ 会在 book=None 时静默失配，现用宽松版
from _common import ANCHOR_RE
DECOR_RE = re.compile(r"^[▼▲▽△■□●○\s]+")
BAD_FN = re.compile(r'[\\/:*?"<>|\s]')


def parse_units(z):
    """spine 序 XHTML 单元列表（zip 内完整路径）"""
    opf_name = [n for n in z.namelist() if n.lower().endswith(".opf")][0]
    opf = z.read(opf_name).decode("utf-8")
    opf_base = os.path.dirname(opf_name)
    ih = {}
    for m in re.finditer(r"<item\b([^>]*)>", opf, re.I):
        a = dict(re.findall(r'(id|href)=["\']([^"\']*)["\']', m.group(1), re.I))
        if "id" in a and "href" in a:
            ih[a["id"]] = a["href"]
    ordered = []
    for ref in re.findall(r'<itemref[^>]*idref=["\']([^"\']+)["\']', opf):
        h = ih.get(ref)
        if h and h.endswith((".xhtml", ".html")):
            ordered.append(os.path.normpath(os.path.join(opf_base, h)).replace("\\", "/"))
    return ordered


def scan_unit(z, full, min_size):
    """扫一个单元：返回 (img总数, bigs)；bigs 元素 (img全单元序, src, ip, caption)。
    逐 img 记录其前最近 <p> 段文本；「续表」沿用上一真题。"""
    html = z.read(full).decode("utf-8", "ignore")
    paras = []  # (结束偏移, 段内纯文本)
    for pm in P_RE.finditer(html):
        tx = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", pm.group(1)))
        paras.append((pm.end(), tx))

    imgs = []
    last_cap = ""
    for m in IMG_RE.finditer(html):
        src = m.group(1)
        ip = os.path.normpath(os.path.join(os.path.dirname(full), src)).replace("\\", "/")
        try:
            sz = z.getinfo(ip).file_size
        except KeyError:
            sz = 0
        # 图题：img 前最近完整 <p> 的文本
        cap = ""
        for pe, tx in paras:
            if pe > m.start():
                break
            if tx:
                t = DECOR_RE.sub("", tx)
                if t == "续表":
                    cap = "续表"
                elif len(t) <= 60 and not re.search(r"[。；;.！?？]$", t):
                    cap = t
                else:
                    cap = ""
        if cap == "续表" and last_cap:
            cap = last_cap + "（续）"
        elif cap:
            last_cap = cap
        is_big = src.lower().endswith((".jpg", ".jpeg")) and sz >= min_size
        imgs.append((src, ip, sz, is_big, cap))
    return imgs


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    epub, md = args[0], args[1]
    min_size = 30000
    if "--min-size" in sys.argv:
        min_size = int(sys.argv[sys.argv.index("--min-size") + 1])
    apply_ = "--apply" in sys.argv

    z = zipfile.ZipFile(epub)
    units = parse_units(z)

    # ---- EPUB 侧：逐单元扫描（一次扫描，后续复用）----
    unit_imgs, unit_scans = [], []
    n_big = 0
    for ui, full in enumerate(units):
        imgs = scan_unit(z, full, min_size)
        unit_imgs.append(len(imgs))
        unit_scans.append(imgs)
        n_big += sum(1 for x in imgs if x[3])

    # ---- 文本侧：占位符偏移与 SEC 锚 ----
    raw = open(md, "rb").read().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    ph_offs = [m.start() for m in re.finditer(re.escape(PLACEHOLDER), raw)]
    anchors = [(m.start(), int(m.group(1))) for m in ANCHOR_RE.finditer(raw)]

    # ---- 对齐核验（k↔k 前提，硬失败）----
    if len(ph_offs) != sum(unit_imgs):
        sys.exit("硬失败：占位符 %d ≠ img 总数 %d，k↔k 对齐不成立，拒嵌。"
                 % (len(ph_offs), sum(unit_imgs)))
    if anchors:
        if len(anchors) != len(units):
            sys.exit("硬失败：SEC 锚数 %d ≠ spine 单元数 %d，拒嵌。" % (len(anchors), len(units)))
        bounds = [a[0] for a in anchors] + [len(raw)]
        for ui in range(len(units)):
            ph = sum(1 for o in ph_offs if bounds[ui] <= o < bounds[ui + 1])
            if ph != unit_imgs[ui]:
                sys.exit("硬失败：单元 %d（%s）占位符 %d ≠ img %d，拒嵌。"
                         % (ui + 1, os.path.basename(units[ui]), ph, unit_imgs[ui]))
    # k↔k：全序第 g 个 img ↔ 全序第 g 个占位符
    ph_of_img = []
    k = 0
    for ui, n in enumerate(unit_imgs):
        for _ in range(n):
            ph_of_img.append(ph_offs[k]); k += 1

    # ---- 计划 ----
    g = 0
    plan = []  # (ph_off, src, ip, caption, phys, 单元内序)
    phys_of_unit = {a[1]: i + 1 for i, a in enumerate(anchors)}
    for ui in range(len(units)):
        seq = 0
        for (src, ip, sz, is_big, cap) in unit_scans[ui]:
            ph = ph_of_img[g]; g += 1
            if is_big:
                seq += 1
                plan.append((ph, src, ip, cap, phys_of_unit.get(ui + 1, ui + 1), seq))

    print("EPUB 单元 %d｜img 总数 %d｜占位符 %d｜真图版（jpg≥%dB）%d"
          % (len(units), sum(unit_imgs), len(ph_offs), min_size, n_big))
    assert n_big == len(plan)
    for ph, src, ip, cap, phys, seq in plan[:12]:
        print("  [嵌] SEC%-3d #%02d %-18s %6.0fKB | %s"
              % (phys, seq, os.path.basename(src), sz_of(z, ip) / 1024, (cap or "图版")[:30]))
    if len(plan) > 12:
        print("  … 其余 %d 张略" % (len(plan) - 12))

    if not apply_:
        print("\n[DRY-RUN] 未落库。加 --apply 执行。")
        return

    # ---- 落库 ----
    md_dir = os.path.dirname(md)
    img_dir = os.path.join(md_dir, "imgs")  # 图片一律归 imgs/ 子文件夹（2026-09-29 裁定）
    os.makedirs(img_dir, exist_ok=True)
    recs = []
    for ph, src, ip, cap, phys, seq in plan:
        ext = os.path.splitext(src)[1].lower() or ".jpg"
        name = "fig_%03d_%02d%s" % (phys, seq, ext)
        _img_path = os.path.join(img_dir, name)
        with open(_img_path, "wb") as _f:
            _f.write(z.read(ip))
        recs.append((ph, name, cap or "图版"))
    newtext = raw
    for ph, name, alt in sorted(recs, key=lambda r: r[0], reverse=True):
        newtext = newtext[:ph] + "\n\n![%s](imgs/%s)\n" % (alt, name) + newtext[ph + len(PLACEHOLDER):]
    from _common import backup_target, set_backup_root, safe_write  # 备份落点统一 + 原子写
    # 备份落点优先级：EBOOK_BAK_DIR 环境变量 > <md 同级>/_bak（末选会污染交付目录，
    # 交付目录应只含六件，见 SKILL §2，故显式告警并给出改法）。
    if os.environ.get("EBOOK_BAK_DIR"):
        set_backup_root(os.environ["EBOOK_BAK_DIR"])
    else:
        set_backup_root(os.path.join(os.path.dirname(os.path.abspath(md)), "_bak"))
        print("   ⚠️ 未设 EBOOK_BAK_DIR：备份将落 <md 同级>/_bak/（交付目录内）。"
              "建议先 export EBOOK_BAK_DIR=<work>/_bak，保持交付目录只含六件。")
    backup = backup_target(md, suffix="bak-embed")  # 每次 apply 前时间戳快照，避免覆盖旧备份
    shutil.copy2(md, backup)
    safe_write(md, new, backup=False)  # 备份已由上方 backup_target 显式完成
    print("\n[APPLIED] 已嵌入 %d 张真图版（图复制至 %s）；占位符保留 %d 个（小图元）；备份: %s"
          % (len(recs), img_dir, len(ph_offs) - len(recs), backup))


def sz_of(z, ip):
    try:
        return z.getinfo(ip).file_size
    except KeyError:
        return 0


if __name__ == "__main__":
    main()
