# -*- coding: utf-8 -*-
"""
06b_embed_epub_figures.py —— 图版还原（EPUB 路径）：把 EPUB 内嵌原图按图号/顺序嵌回 03 全文。

归属：ebook-precise-structure **§6.4「图版嵌入工具链」**——本文件是 EPUB 支**唯一真相源**
（各产出库的 _tools/ 只留指针，不再保留副本）。

与 06_embed_pdf_figures.py 的分工：
  - 06_embed_pdf_figures.py ：PDF 路径（从 PDF 页渲染提图，按 <<<PAGE>>> 锚定位插入）
  - 本脚本                  ：EPUB 路径（从 EPUB zip 解 <img>，按图号体系回填）

匹配体系（v1.9.6 重写；旧版三缺陷的根治）：
  ◆ 权威键是「图题」不是 img 顺序：出版社 EPUB 的图题结构 <p class="tuti"><b>图N　题</b></p>
    紧跟 <img>，题文本里的编号是原书印刷编号——跨 EPUB/03 两侧对齐以它为准。
  ◆ EPUB 侧注册（同号多候选按分值取高、同分后到覆盖——正文图在目次之后）：
    3 分：img 自带 id（figN / mapN / figureN）→ 系列+文档序号；题从 tuti 取
    2 分：无 id 但窗口内有 tuti 题 → 编号取自题文本
    1 分：纯窗口就近命中「图N / 地图N」（旧行为兜底；「地图N」独立系列，(?<!地)图 防串号）
  ◆ id 文档序号 ≠ 印刷编号时以题编号为准；题无编号（书前插页大图如「中国地形图」）
    走题名通道：在 03 找行首 == 题文本的正文行插嵌。
  ◆ 03 侧插入点（三级优先，根治「插图落目次」）：
    正文 caption 行（行首「图N　题」且不在目次区） > 正文引用（（见图N）） > 目次行兜底
    目次区检测：同一 SEC 锚区间内行首图号行 ≥ TOC_DENSE 判目次区（通用，不依赖书名）。

输出：
  - 把原图复制到 03 同目录（文件名 fig_<编号或题名>.jpeg）
  - 在 03 中选定的插入点后插入 ![图题](fig_....jpeg)
  - dry-run 仅打印计划；--apply 落库（保留 LF、自动备份 *.bak-embed-<mdstem>-<时间戳>）
"""
import os, re, zipfile, sys, shutil, time

IMG_RE = re.compile(r"<img\b([^>]*)>", re.I)
ATTR_RE = re.compile(r'(src|alt|id)=["\']([^"\']*)["\']', re.I)
# 图题权威结构：<p class="tuti">…</p>（题内可能夹生僻字图片等标签，抓整段再剥）
TUTI_RE = re.compile(r'class="tuti"[^>]*>\s*(.*?)</p>', re.I | re.S)
# 「图」前不能是「地」——「地图N」属地图系列，防串号
FIGNUM_RE = re.compile(r'(?<!地)图\s*([0-9]+(?:[-—–][0-9]+)*)')
MAPNUM_RE = re.compile(r'地图\s*([0-9]+(?:[-—–][0-9]+)*)')
ID_FIG_RE = re.compile(r'^(?:figure|fig)[-_]?(\d+(?:[-_]\d+)*)$', re.I)
ID_MAP_RE = re.compile(r'^(?:map)[-_]?(\d+(?:[-_]\d+)*)$', re.I)
# 03 行首图号行（caption 行 / 目次行共用形态）
LINE_FIG_RE = re.compile(r'^图\s*([0-9]+(?:[-—–][0-9]+)*)[　\s]')
LINE_MAP_RE = re.compile(r'^地图\s*([0-9]+(?:[-—–][0-9]+)*)[　\s]')
# 目次区判定：行首图号行近连续（相邻间隔 ≤3 行）成串且串长 ≥ TOC_RUN 才判目次区。
# （目次页几十行连排；正文 caption 行散布在长正文之间，串长只有 1–3，不会误判。）
TOC_RUN = 8

def norm_fig(fn):
    """图号归一：长/短破折号、下划线统一为短横 -"""
    if not fn:
        return fn
    return fn.replace("—", "-").replace("–", "-").replace("_", "-")

def num_from_title(title):
    """从题文本解析 (series, no)；无编号返回 (None, None)——书前插页大图情形。"""
    if not title:
        return None, None
    m = MAPNUM_RE.search(title)
    if m:
        return "map", norm_fig(m.group(1))
    m = FIGNUM_RE.search(title)
    if m:
        return "fig", norm_fig(m.group(1))
    return None, None

def parse_epub(epub):
    """按 spine 序解析 EPUB，返回 (zip, figs)。
    figs 元素: (series, no, src, title, full, score)；series ∈ {"fig","map",None}。
    无图号也无题的 img 也保留（series=None），供顺序兜底。"""
    z = zipfile.ZipFile(epub)
    opf_name = [n for n in z.namelist() if n.lower().endswith(".opf")][0]
    opf = z.read(opf_name).decode("utf-8")
    item_href = {}
    for m in re.finditer(r'<item\b([^>]*)>', opf, re.I):
        attrs = dict(re.findall(r'(id|href)=["\']([^"\']*)["\']', m.group(1), re.I))
        if 'id' in attrs and 'href' in attrs:
            item_href[attrs['id']] = attrs['href']
    spine = [re.search(r'idref=["\']([^"\']+)["\']', s).group(1)
             for s in re.findall(r'<itemref\b[^>]*>', opf)]
    ordered = [item_href[i] for i in spine if i in item_href]
    opf_base = os.path.dirname(opf_name)

    figs = []
    for href in ordered:
        full = os.path.normpath(os.path.join(opf_base, href)).replace("\\", "/").split("#")[0]
        if full not in z.namelist():
            continue
        html = z.read(full).decode("utf-8")
        for tag in IMG_RE.finditer(html):
            attrs = {m.group(1).lower(): m.group(2) for m in ATTR_RE.finditer(tag.group(1))}
            src = attrs.get("src", "")
            if not src:
                continue
            post = html[tag.end():tag.end()+600]
            pre = html[max(0, tag.start()-500):tag.start()]
            pre_start = max(0, tag.start() - 500)

            # 题文本：优先 tuti 结构（权威），否则窗口题号后文本
            title = ""
            tm = TUTI_RE.search(post)
            if tm:
                title = re.sub(r'<[^>]+>', ' ', tm.group(1))
                title = re.sub(r'\s+', ' ', title).strip()[:100]

            series, no, score = None, None, 0
            iid = attrs.get("id", "")
            m = ID_FIG_RE.match(iid)
            if m:
                series, no, score = "fig", norm_fig(m.group(1)), 3
            else:
                m = ID_MAP_RE.match(iid)
                if m:
                    series, no, score = "map", norm_fig(m.group(1)), 3
            if score == 3:
                # 题编号与 id 文档序号冲突时以题编号（印刷编号）为准
                ts, tn = num_from_title(title)
                if ts and tn and (ts, tn) != (series, no):
                    series, no = ts, tn
                elif ts and not no:
                    series, no = ts, tn
            if score == 0:
                # 无 id：tuti 题（2 分）或纯窗口（1 分）
                if tm:
                    series, no = num_from_title(title)
                    if series:
                        score = 2
                if score == 0:
                    cand = None  # (score, series, no, fm_end_global)
                    for fm in list(FIGNUM_RE.finditer(pre))[::-1]:
                        after_txt = re.sub(r'<[^>]+>', '', html[pre_start + fm.end():pre_start + fm.end() + 40])
                        sc = 2 if re.match(r'^[　\s]\S', after_txt) else 1
                        cand = (sc, "fig", norm_fig(fm.group(1)), pre_start + fm.end())
                        break  # 就近（pre 中最后一个）
                    if cand is None:
                        for fm in list(MAPNUM_RE.finditer(pre))[::-1]:
                            cand = (1, "map", norm_fig(fm.group(1)), pre_start + fm.end())
                            break
                    if cand is None:
                        fm = FIGNUM_RE.search(post)
                        if fm:
                            cand = (1, "fig", norm_fig(fm.group(1)), tag.end() + fm.end())
                        else:
                            fm = MAPNUM_RE.search(post)
                            if fm:
                                cand = (1, "map", norm_fig(fm.group(1)), tag.end() + fm.end())
                    if cand:
                        score, series, no, fm_end = cand
                        if not title:
                            after = html[fm_end:fm_end+200]
                            after = re.sub(r'<[^>]+>', ' ', after).split('\n')[0]
                            after = re.sub(r'\s+', ' ', after).strip().lstrip('—-– ')
                            if 'calibre' in after or 'id=' in after or '<' in after:
                                after = ""
                            title = after
            figs.append((series, no, src, title, full, score))
    return z, figs

def build_registry(figs):
    """注册表：{(series, no): (src, title, full, score)}——分高者胜，同分后到覆盖（正文图在目次后）。
    另返回 title_only：有题无编号项列表 [(src, title, full)]（书前插页大图），走题名通道。"""
    reg = {}
    title_only = []
    for series, no, src, title, full, score in figs:
        if series is None:
            continue
        # 题权威：题文本存在且题中无编号 → 仅当 id 权威项（score=3）才转题名通道
        # （如书前插页大图）；无 id 的窗口/tuti 项（目次缩略图等）题无编号即噪声，丢弃
        if title and num_from_title(title) == (None, None):
            if score == 3:
                title_only.append((src, title, full))
            continue
        if not no:
            if title:
                title_only.append((src, title, full))
            continue
        key = (series, no)
        old = reg.get(key)
        if old is None or score >= old[3]:
            reg[key] = (src, title or (old[1] if old else ""), full, score)
    return reg, title_only

REF_RE = re.compile(r'[（(]\s*见?\s*(?<!地)图\s*([0-9]+(?:[-—–][0-9]+)*)\s*[）)]')
REF_MAP_RE = re.compile(r'[（(]\s*见?\s*地图\s*([0-9]+(?:[-—–][0-9]+)*)\s*[）)]')

def analyze_md(text):
    """分析 03 文本，返回 (insert_points, toc_ranges, line_infos)。
    insert_points: [(priority, pos, series, no)]；priority: 0=正文 caption 行 1=正文引用 2=目次行
    line_infos: [(pos_lineend, stripped_text, in_toc)] 全部非空行——题名通道用。"""
    lines = text.split("\n")
    offs = [0]
    for l in lines[:-1]:
        offs.append(offs[-1] + len(l) + 1)
    # 行首图号行
    figlines = {}
    for i, l in enumerate(lines):
        m = LINE_FIG_RE.match(l)
        series = "fig"
        if not m:
            m = LINE_MAP_RE.match(l)
            series = "map"
        if m:
            figlines[i] = (series, norm_fig(m.group(1)))
    # 行首图号行 → 近连续串（间隔 ≤3 行）→ 长串判目次区
    fig_idx = sorted(i for i in figlines)
    toc_line_set = set()
    run = [fig_idx[0]] if fig_idx else []
    for i in fig_idx[1:]:
        if i - run[-1] <= 3:
            run.append(i)
        else:
            if len(run) >= TOC_RUN:
                toc_line_set.update(run)
            run = [i]
    if len(run) >= TOC_RUN:
        toc_line_set.update(run)

    def in_toc(pos):
        return any(a <= pos <= b for a, b in toc_ranges)

    toc_ranges = []
    run2 = None
    for i in fig_idx:
        if i in toc_line_set:
            end_off = offs[i] + len(lines[i])
            if run2 is None:
                run2 = [offs[i], end_off]
            else:
                run2[1] = end_off
        elif run2 is not None:
            toc_ranges.append(tuple(run2)); run2 = None
    if run2 is not None:
        toc_ranges.append(tuple(run2))

    insert_points = []
    for i, (series, no) in figlines.items():
        pri = 2 if in_toc(offs[i]) else 0
        insert_points.append((pri, offs[i] + len(lines[i]), series, no))
    for m in REF_MAP_RE.finditer(text):
        insert_points.append((1, m.end(), "map", norm_fig(m.group(1))))
    for m in REF_RE.finditer(text):
        insert_points.append((1, m.end(), "fig", norm_fig(m.group(1))))
    insert_points.sort()
    # 题名通道素材：非空行（行尾偏移, 去空白文本, 是否目次区）
    line_infos = [(offs[i] + len(lines[i]), lines[i].strip(), in_toc(offs[i]))
                  for i in range(len(lines)) if lines[i].strip()]
    return insert_points, toc_ranges, line_infos

def pick_plan(insert_points, reg, title_only, figs, line_infos):
    """编号通道：每个 (series,no) 取最优插入点（priority 最小、同优先级取首个）；
    题名通道：无编号注册项按「行首 == 题文本」匹配正文行（排除目次区）。
    查不到图走「去末段回退 → 顺序兜底」。"""
    best = {}
    for pri, pos, series, no in insert_points:
        key = (series, no)
        if key not in best or pri < best[key][0]:
            best[key] = (pri, pos)
    plan = []
    used_src = set()
    embedded = set()

    def resolve(series, no):
        hit = reg.get((series, no))
        if hit:
            return hit
        parts = re.split(r'[-]', no)
        for i in range(len(parts)-1, 0, -1):
            hit = reg.get((series, "-".join(parts[:i])))
            if hit:
                return hit
        return None

    seq_pool = [f for f in figs if f[0] is None]
    seq_idx = 0
    for (series, no), (pri, pos) in sorted(best.items(), key=lambda kv: kv[1][1]):
        if (series, no) in embedded:
            continue
        hit = resolve(series, no)
        if not hit and seq_idx < len(seq_pool):
            f = seq_pool[seq_idx]; seq_idx += 1
            hit = (f[2], f[3], f[4], f[5])
        if not hit:
            print("  !! 无图可嵌: %s%s" % ("" if series == "fig" else "地图", no))
            continue
        src, title, full, score = hit
        ins = src not in used_src
        if ins:
            used_src.add(src)
        embedded.add((series, no))
        plan.append((series, no, src, title, full, pos, ins))

    # 题名通道（无编号项，如书前插页大图）
    for src, title, full in title_only:
        if src in used_src or len(title.strip()) < 3:
            continue
        key_txt = title.strip()
        cand = None  # (pri, pos)
        for pos, txt, intoc in line_infos:
            if txt == key_txt or txt.startswith(key_txt + "　") or txt.startswith(key_txt + "（"):
                pri = 2 if intoc else 0
                if cand is None or pri < cand[0]:
                    cand = (pri, pos)
        if cand is None:
            print("  !! 题名无落点（不嵌）: %s" % key_txt)
            continue
        used_src.add(src)
        plan.append((None, None, src, title, full, cand[1], True))

    plan.sort(key=lambda p: p[5])
    return plan

def main():
    epub = sys.argv[1]
    md = sys.argv[2]
    apply_ = "--apply" in sys.argv

    z, figs = parse_epub(epub)
    reg, title_only = build_registry(figs)
    n_id = sum(1 for v in reg.values() if v[3] == 3)
    n_cap = sum(1 for v in reg.values() if v[3] == 2)
    print("EPUB 解析 <img>: %d（注册 %d：id 权威 %d / tuti 题 %d / 纯窗口 %d；题名通道 %d）"
          % (len(figs), len(reg), n_id, n_cap, len(reg)-n_id-n_cap, len(title_only)))

    raw = open(md, "rb").read()
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")

    insert_points, toc_ranges, line_infos = analyze_md(text)
    print("03 插入点候选: %d（目次区 %d 段）" % (len(insert_points), len(toc_ranges)))
    plan = pick_plan(insert_points, reg, title_only, figs, line_infos)

    n_ins = len([p for p in plan if p[6]])
    n_xref = len(plan) - n_ins
    print("计划嵌入图数:", n_ins, " (交叉引用不重复插图:", n_xref, ")")
    for series, no, src, title, full, pos, ins in plan:
        tag = "嵌" if ins else "引"
        label = ("地图%s" if series == "map" else "图%s") % no if no else (title or "?")
        print("  [%s] %s -> %s  | 题: %s" % (tag, label, os.path.basename(src), (title or "")[:30]))

    if not apply_:
        print("\n[DRY-RUN] 未落库。加 --apply 执行。")
        return

    def out_name_of(series, no, title):
        if no:
            base = ("图%s" if series == "fig" else "地图%s") % no
        else:
            base = re.sub(r'[\\/:*?"<>|\s]', "", title or "fig")
        return "fig_%s" % base

    # 复制图片
    src_to_out = {}
    md_dir = os.path.dirname(md)
    for series, no, src, title, full, pos, ins in plan:
        if not ins:
            continue
        img_full_path = os.path.normpath(os.path.join(os.path.dirname(full), src)).replace("\\", "/")
        ext = os.path.splitext(src)[1].lower() or ".jpeg"
        name = out_name_of(series, no, title) + ext
        _img_path = os.path.join(md_dir, name)
        with open(_img_path, "wb") as _f:
            _f.write(z.read(img_full_path))
        src_to_out[src] = name

    # 插入（从后往前，避免偏移失效）
    for series, no, src, title, full, pos, ins in sorted([p for p in plan if p[6]],
                                                         key=lambda p: p[5], reverse=True):
        label = ("地图%s" if series == "map" else "图%s") % no if no else (title or "图")
        alt = label if not title or title.startswith(label) else "%s %s" % (label, title)
        out_text = "\n\n![%s](%s)" % (alt, src_to_out[src])
        text = text[:pos] + out_text + text[pos:]
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
    print("\n[APPLIED] 已嵌入 %d 张图（%d 处交叉引用不重复插图）；备份: %s"
          % (len(src_to_out), n_xref, backup))

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: 06b_embed_epub_figures.py <epub> <md> [--apply]")
        sys.exit(1)
    main()
