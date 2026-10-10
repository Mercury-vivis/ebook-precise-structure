#!/usr/bin/env python3
# 00b_audit_tables.py —— 表格审计（程序确定性 · 纯标准库 · 零 paddle 依赖）
#
# 用途：在 ingest（00_ingest_bookocr.py / 01b_struct.py）完成后、Stage 3 语义清洗前，
#       扫描 fulltext_struct.txt 中所有 <table ...>...</table> 块，逐表统计行列规模、
#       空 td 占比、img 单元格占比、colspan/rowspan 使用、表头文本，并按写死的规则
#       打分类/红旗标签，输出 table_audit.md 与 table_audit.json，作为 Stage 3 表格核对
#       （重建 HTML / 整图嵌入 / 跨页合并）的依据。
#
# 参数：
#   --work <工作目录>   从 <work>/fulltext_struct.txt 读取；与 --struct 二选一
#   --struct <路径>     直接指定 fulltext_struct.txt 路径（绕过 --work）
#   --out  <输出目录>   报告输出目录，缺省 = work 目录；产出 table_audit.md / table_audit.json
#   --imgs-dir <目录>   table 对照原图目录，缺省 = <work>/imgs；用于 glob 匹配
#                       p{phys}_table_*.jpg 给出逐表对照图路径
#
# 分类/红旗规则（写死、可复核）：
#   图谱表·建议整图嵌入   ：含 <img> 单元格占比 ≥ 50%
#   红旗·行列对齐疑似失败 ：空 td 占比 > 30%
#   红旗·表头空列        ：表头行（首个 <tr>）存在空 td
#   碎片表·待合并        ：相邻 phys（差值 ≤ 2）的两表，表头文本归一化（去空白/标点）
#                          后 difflib.SequenceMatcher 相似度 ≥ 0.8
#   提示·粘连/错位       ：单元格内出现「数字 空白 数字」（两个以上数字组仅以空白分隔、
#                          无中文/标点分隔符），或单元格含 ≥ 2 个「号」字
#
# 仅用标准库：re / os / sys / argparse / json / glob / difflib。
# 幂等可重跑（覆盖输出）；写盘统一 LF（newline="\n"）。

import re
import os
import sys
import json
import glob
import argparse
from difflib import SequenceMatcher

# 锚：<<<PAGE phys=N book=M>>>（book 可缺省，如 EPUB 支或 book=None）
# ANCHOR_RE 已收敛到 _common（2026-10-04 审查 P0-2），本文件不再内联。
from _common import ANCHOR_RE, read_text_cn  # 2026-10-04 审查 P2-15：read_text_cn 统一读取入口（utf-8-sig 容错）
# 表格：带属性（<table border="1">）也能命中，故用 \b
TABLE_RE = re.compile(r'<table\b[^>]*>(.*?)</table>', re.S | re.I)
ROW_RE = re.compile(r'<tr\b[^>]*>(.*?)</tr>', re.S | re.I)
# 单元格：<td ...>...</td> 或 <th ...>...</th>（闭合标签与开标签同名）
CELL_RE = re.compile(r'<(td|th)\b([^>]*)>(.*?)</\1>', re.S | re.I)
TAG_RE = re.compile(r'<[^>]+>')
# 表头归一化要剥掉的标点（中英文）
PUNCT_RE = re.compile(r'[\s\u3000，。、；：？！「」『』（）()\[\]【】《》〈〉·.,;:!?\-—_/\\|"\'*#]')

# ---- 表/图后碎片噪音检测 ----
# 规则定义取自 _fig_common（同目录，与 00c_strip_region_noise.py 同一真相源），不再内联复制：
# 页锚/marker/闭合标签/图题/句末标点/章节标题/短行阈值，任一侧改口径只改 _fig_common。
from _fig_common import (
    MARKER_RE as FRAG_MARKER_RE,
    CLOSE_WRAP_RE as FRAG_CLOSE_RE,
    CAPTION_DIV_RE as FRAG_CAPTION_DIV_RE,
    CAPTION_LINE_RE as FRAG_CAPTION_LINE_RE,
    SENTENCE_END_RE as FRAG_SENTENCE_END_RE,
    SECTION_RE as FRAG_SECTION_RE,
    SHORT_LINE_THRESHOLD as FRAG_SHORT_THRESHOLD,
    MIN_NONEMPTY_LINES as FRAG_MIN_LINES,
    AUDIT_SHORT_RATIO,  # P2-18（2026-10-04）：短行阈值单一真相源，00b 不再内联 0.90
)
# 阈值刻意沿用审计口径（AUDIT 0.90）而非剥离口径（STRIP 0.75）：
# 00b 只报不管剥，宁漏勿误报；00c 的剥离不可逆，宁宽勿误删。两者差异在 _fig_common 登记。
FRAG_SHORT_RATIO = AUDIT_SHORT_RATIO  # ← 2026-10-04 审查 P2-18：引用 _fig_common 单一真相源，不再内联 0.90
FRAG_WS_RE = re.compile(r"\s*")


def detect_page_fragment(body):
    """检测页面 body 中表/图后的碎片噪音。返回 (frag_start, n_lines) 或 (None, 0)。"""
    markers = list(FRAG_MARKER_RE.finditer(body))
    if not markers:
        return None, 0
    pos = markers[-1].end()
    while pos < len(body):
        m = FRAG_CLOSE_RE.match(body, pos)
        if m:
            pos = m.end()
        else:
            break
    while pos < len(body):
        ws = FRAG_WS_RE.match(body, pos)
        if ws:
            pos = ws.end()
        if pos >= len(body):
            break
        m = FRAG_CAPTION_DIV_RE.match(body, pos)
        if m:
            pos = m.end()
            continue
        m = FRAG_CAPTION_LINE_RE.match(body, pos)
        if m and m.end() < len(body) and body[m.end()] == "\n":
            pos = m.end() + 1
            continue
        break
    after = body[pos:]
    stripped = after.lstrip("\n")
    if not stripped.strip():
        return None, 0
    lines = [l.strip() for l in stripped.split("\n")]
    nonempty = [l for l in lines if l]
    if len(nonempty) < FRAG_MIN_LINES:
        return None, 0
    if any(FRAG_SECTION_RE.search(l) for l in nonempty):
        return None, 0
    short = sum(1 for l in nonempty if len(l) < FRAG_SHORT_THRESHOLD)
    if short / len(nonempty) > FRAG_SHORT_RATIO and not any(FRAG_SENTENCE_END_RE.search(l) for l in nonempty):
        return pos, len(nonempty)
    return None, 0



def parse_attrs(attr_str):
    """从 <td ...> 属性串解析 (colspan, rowspan)，缺省 1。"""
    colspan = 1
    rowspan = 1
    m = re.search(r'colspan\s*=\s*["\']?(\d+)', attr_str, re.I)
    if m:
        colspan = max(1, int(m.group(1)))
    m = re.search(r'rowspan\s*=\s*["\']?(\d+)', attr_str, re.I)
    if m:
        rowspan = max(1, int(m.group(1)))
    return colspan, rowspan


def cell_text(inner):
    """单元格内部 → 纯文本（剥标签、折叠空白）。"""
    txt = TAG_RE.sub('', inner)
    txt = re.sub(r'\s+', ' ', txt).strip()
    return txt


def cell_has_img(inner):
    return bool(re.search(r'<img\b', inner, re.I))


def parse_table(table_html):
    """解析单个 <table> 块，返回 rows：每行为 [{'text','img','colspan','rowspan'}, ...]。"""
    rows = []
    for rm in ROW_RE.finditer(table_html):
        row_html = rm.group(1)
        cells = []
        for cm in CELL_RE.finditer(row_html):
            attr_str = cm.group(2)
            inner = cm.group(3)
            cs, rs = parse_attrs(attr_str)
            cells.append({
                'text': cell_text(inner),
                'img': cell_has_img(inner),
                'colspan': cs,
                'rowspan': rs,
            })
        if cells:
            rows.append(cells)
    return rows


def is_adhesion(text):
    """单元格内疑似多值粘连：两个以上数字组仅以空白分隔（无中文/标点夹隔）；或含 ≥2 个「号」。"""
    if text.count('号') >= 2:
        return True
    # 数字组位置
    runs = [m for m in re.finditer(r'\d+', text)]
    for i in range(len(runs) - 1):
        between = text[runs[i].end():runs[i + 1].start()]
        # 两段数字之间只允许空白，不允许任何非空白字符（中文/标点/字母都算粘连被破坏）
        if re.fullmatch(r'[ \t　]+', between):
            return True
    return False


def analyze_table(rows):
    """计算单表指标。"""
    if not rows:
        return None

    total_cells = 0
    empty_cells = 0
    img_cells = 0
    colspans = 0
    rowspans = 0
    cell_lens = []
    adhesion_cells = 0

    max_cols = 0
    for row in rows:
        row_cols = 0
        for cell in row:
            cs = cell['colspan']
            rs = cell['rowspan']
            colspans += max(0, cs - 1)
            rowspans += max(0, rs - 1)
            row_cols += cs
            total_cells += 1
            t = cell['text']
            cell_lens.append(len(t))
            if not t:
                empty_cells += 1
            if cell['img']:
                img_cells += 1
            if is_adhesion(t):
                adhesion_cells += 1
        max_cols = max(max_cols, row_cols)

    # 表头行（首行）空 td 数 + 表头各 td 文本
    header_cells = rows[0]
    header_empty = sum(1 for c in header_cells if not c['text'])
    header_texts = [c['text'] for c in header_cells]

    avg_len = sum(cell_lens) / len(cell_lens) if cell_lens else 0
    empty_ratio = empty_cells / total_cells if total_cells else 0
    img_ratio = img_cells / total_cells if total_cells else 0

    return {
        'total_rows': len(rows),
        'data_rows': max(0, len(rows) - 1),
        'logic_cols': max_cols,
        'total_cells': total_cells,
        'empty_cells': empty_cells,
        'empty_ratio': round(empty_ratio, 3),
        'img_cells': img_cells,
        'img_ratio': round(img_ratio, 3),
        'colspans': colspans,
        'rowspans': rowspans,
        'avg_cell_chars': round(avg_len, 1),
        'header_empty': header_empty,
        'header_texts': header_texts,
        'adhesion_cells': adhesion_cells,
    }


def norm_header(texts):
    """表头归一化：拼接 + 去空白/标点，供 SequenceMatcher 比较。"""
    s = ''.join(texts)
    return PUNCT_RE.sub('', s)


def classify(metrics):
    """打分类/红旗标签（顺序固定）。"""
    tags = []
    if metrics['img_ratio'] >= 0.50:
        tags.append('图谱表·建议整图嵌入')
    if metrics['empty_ratio'] > 0.30:
        tags.append(f"红旗·行列对齐疑似失败(空td {metrics['empty_ratio']*100:.0f}%)")
    if metrics['header_empty'] > 0:
        tags.append(f"红旗·表头空列(空td {metrics['header_empty']})")
    if metrics['adhesion_cells'] > 0:
        tags.append(f"提示·粘连/错位({metrics['adhesion_cells']}格)")
    return tags


def find_fragments(tables):
    """相邻 phys（差值≤2）且表头归一化后相似度≥0.8 的表 → 碎片表·待合并。
    返回需打碎片标签的表索引集合。"""
    frag_idx = set()
    for i in range(len(tables)):
        h1 = norm_header(tables[i]['metrics']['header_texts'])
        if not h1:
            continue
        for j in range(i + 1, len(tables)):
            dp = tables[j]['phys'] - tables[i]['phys']
            if dp > 2:
                break  # tables 按 phys 升序，再往后更远
            h2 = norm_header(tables[j]['metrics']['header_texts'])
            if not h2:
                continue
            if SequenceMatcher(None, h1, h2).ratio() >= 0.8:
                frag_idx.add(i)
                frag_idx.add(j)
    return frag_idx


def find_orig_images(phys, imgs_dir):
    """glob 匹配 imgs/p{phys}_table_* 原图。"""
    if not imgs_dir or not os.path.isdir(imgs_dir):
        return []
    pat = os.path.join(imgs_dir, f'p{phys}_table_*')
    return sorted(glob.glob(pat))


def main():
    ap = argparse.ArgumentParser(
        description='表格审计：扫描 fulltext_struct.txt 中所有 HTML 表格并分类/打红旗')
    ap.add_argument('--work', default='', help='工作目录（含 fulltext_struct.txt）')
    ap.add_argument('--struct', default='', help='直接指定 fulltext_struct.txt 路径（与 --work 二选一）')
    ap.add_argument('--out', default='', help='报告输出目录，缺省 = work 目录')
    ap.add_argument('--imgs-dir', default='', help='table 对照原图目录，缺省 = <work>/imgs')
    args = ap.parse_args()

    # 解析 struct 路径与 work
    if args.struct:
        struct_path = args.struct
        work = os.path.dirname(os.path.abspath(struct_path))
    elif args.work:
        work = args.work
        struct_path = os.path.join(work, 'fulltext_struct.txt')
    else:
        sys.exit('[错误] 必须指定 --work <工作目录> 或 --struct <fulltext_struct.txt 路径>')

    if not os.path.isfile(struct_path):
        sys.exit(f'[错误] 找不到 struct 文件：{struct_path}')

    out_dir = args.out or work
    os.makedirs(out_dir, exist_ok=True)
    imgs_dir = args.imgs_dir or os.path.join(work, 'imgs')

    text = read_text_cn(struct_path)  # 2026-10-04 审查 P1-6/10：裸读转统一入口（utf-8-sig 容错）

    # 预扫全部锚点（按位置），供每表回查所属 phys/book
    anchors = [(m.start(), int(m.group(1)),
                (m.group(2) if m.group(2) else None))
               for m in ANCHOR_RE.finditer(text)]

    def locate_anchor(pos):
        """返回表起点 pos 之前最近锚的 (phys, book)；无锚则 (0, None)。"""
        phys, book = 0, None
        for apos, p, b in anchors:
            if apos < pos:
                phys, book = p, b
            else:
                break
        return phys, book

    # 扫描所有表格块
    table_matches = list(TABLE_RE.finditer(text))
    tables = []
    for m in table_matches:
        phys, book = locate_anchor(m.start())
        rows = parse_table(m.group(0))
        metrics = analyze_table(rows)
        if metrics is None:
            continue
        tables.append({
            'phys': phys,
            'book': book,
            'metrics': metrics,
            'header_texts': metrics['header_texts'],
        })

    # 页面级碎片检测：解析所有页，对含 table/img 的页跑 detect_page_fragment
    page_frag = {}  # phys -> n_lines
    page_bodies = {}
    for am in ANCHOR_RE.finditer(text):
        p = int(am.group(1))
        body_start = am.end()
        # 找下一个锚
        next_m = ANCHOR_RE.search(text, am.end())
        body_end = next_m.start() if next_m else len(text)
        body = text[body_start:body_end].rstrip("\n")
        page_bodies[p] = body
        if "<table" in body.lower() or "<img" in body.lower():
            _, n = detect_page_fragment(body)
            if n > 0:
                page_frag[p] = n

    # 碎片检测（在分类前算，标签并入）
    frag_idx = find_fragments(tables)
    for i, t in enumerate(tables):
        tags = classify(t['metrics'])
        if i in frag_idx:
            tags.append('碎片表·待合并')
        if t['phys'] in page_frag:
            tags.append(f"表/图后碎片噪音({page_frag[t['phys']]}行,待00c剥离)")
        t['tags'] = tags

    # 统计各分类计数
    def has_tag(t, key):
        return any(key in tag for tag in t['tags'])
    n_atlas = sum(1 for t in tables if has_tag(t, '图谱表'))
    n_align = sum(1 for t in tables if has_tag(t, '行列对齐'))
    n_hdr = sum(1 for t in tables if has_tag(t, '表头空列'))
    n_frag = sum(1 for t in tables if has_tag(t, '碎片表'))
    n_adhs = sum(1 for t in tables if has_tag(t, '粘连'))
    n_noise = sum(1 for t in tables if has_tag(t, '碎片噪音'))
    n_noise_pages = len(page_frag)
    n_clean = sum(1 for t in tables if not t['tags'])

    # ---- table_audit.json ----
    json_path = os.path.join(out_dir, 'table_audit.json')
    json_data = {
        'source': struct_path,
        'imgs_dir': imgs_dir,
        'total_tables': len(tables),
        'summary': {
            'atlas_img_over_50pct': n_atlas,
            'redflag_align_fail': n_align,
            'redflag_header_empty': n_hdr,
            'fragment_pending_merge': n_frag,
            'hint_adhesion': n_adhs,
            'region_noise_pages': n_noise_pages,
            'region_noise_tables': n_noise,
            'clean_no_flag': n_clean,
        },
        'tables': [
            {
                'phys': t['phys'],
                'book': t['book'],
                'rows': t['metrics']['total_rows'],
                'logic_cols': t['metrics']['logic_cols'],
                'empty_cells': t['metrics']['empty_cells'],
                'empty_ratio': t['metrics']['empty_ratio'],
                'img_cells': t['metrics']['img_cells'],
                'img_ratio': t['metrics']['img_ratio'],
                'colspans': t['metrics']['colspans'],
                'rowspans': t['metrics']['rowspans'],
                'avg_cell_chars': t['metrics']['avg_cell_chars'],
                'header_texts': t['header_texts'],
                'tags': t['tags'],
                'orig_images': [os.path.basename(p) for p in find_orig_images(t['phys'], imgs_dir)],
            }
            for t in tables
        ],
    }
    with open(json_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(json_data, ensure_ascii=False, indent=2) + '\n')

    # ---- table_audit.md ----
    md = []
    md.append('# 表格审计报告')
    md.append('')
    md.append(f'> 来源：`{struct_path}`')
    md.append(f'> 对照原图目录：`{imgs_dir}`')
    md.append(f'> 检测到 **{len(tables)}** 个 HTML 表格块')
    md.append('')
    md.append('## 一、汇总')
    md.append('')
    md.append('| 指标 | 数量 |')
    md.append('|------|------|')
    md.append(f'| 表格总数 | {len(tables)} |')
    md.append(f'| 图谱表（img格≥50%，建议整图嵌入） | {n_atlas} |')
    md.append(f'| 红旗·行列对齐疑似失败（空td>30%） | {n_align} |')
    md.append(f'| 红旗·表头空列 | {n_hdr} |')
    md.append(f'| 碎片表·待合并（相邻phys表头相似） | {n_frag} |')
    md.append(f'| 提示·粘连/错位 | {n_adhs} |')
    md.append(f'| 表/图后碎片噪音（{n_noise_pages}页，待00c剥离） | {n_noise} |')
    md.append(f'| 无红旗（保留HTML抽检） | {n_clean} |')
    md.append('')
    md.append('## 二、逐表明细')
    md.append('')
    md.append('| phys | book | 规模(行×列) | 空td(比) | img格(比) | 分类/红旗 | 表头文本 | 对照原图 |')
    md.append('|------|------|-----------|----------|-----------|-----------|----------|----------|')
    for t in tables:
        m = t['metrics']
        tags_str = '；'.join(t['tags']) if t['tags'] else '—'
        hdr = ' / '.join(x for x in t['header_texts']) if t['header_texts'] else '—'
        hdr = hdr.replace('|', '\\|')
        imgs = find_orig_images(t['phys'], imgs_dir)
        img_cell = '；'.join(f'`{os.path.basename(p)}`' for p in imgs) if imgs else '⚠️无table原图'
        book = t['book'] if t['book'] is not None else 'None'
        md.append(
            f'| {t["phys"]} | {book} | {m["total_rows"]}×{m["logic_cols"]} '
            f'| {m["empty_cells"]}({m["empty_ratio"]*100:.0f}%) '
            f'| {m["img_cells"]}({m["img_ratio"]*100:.0f}%) '
            f'| {tags_str} | {hdr} | {img_cell} |'
        )
    md.append('')
    md.append('## 三、处置建议（按 §3.7 表格处置决策树）')
    md.append('')
    md.append('- **图谱表**：删碎片 HTML，整表图片嵌入（`![表N](imgs/p{phys}_table_N.jpg)`），不转 HTML。')
    md.append('- **红旗·行列对齐 / 表头空列**：对照 `imgs/p{phys}_table_*.jpg` 原图，LLM 重建 HTML；原图不清则整图嵌入 +〔存疑〕。')
    md.append('- **碎片表·待合并**：跨页合并为连续表（一份表头），或分页保留但去残行。')
    md.append('- **提示·粘连/错位**：按原图拆分数字格（如舰员列 `120 98` → 两列）。')
    md.append('- **无红旗**：保留 HTML，抽检关键数字格与原图一致。')
    md.append('- **表/图后碎片噪音**：跑 `00c_strip_region_noise.py --apply` 确定性剥离（有 region_noise_log 台账可恢复）。')
    md.append('')
    md.append('> 本报告由 `00b_audit_tables.py` 自动生成（纯标准库，零 paddle 依赖）；幂等可重跑。')

    md_path = os.path.join(out_dir, 'table_audit.md')
    with open(md_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(md) + '\n')

    # ---- stdout 摘要 ----
    print(f'表格审计完成：{len(tables)} 个表  (来源 {struct_path})')
    print(f'  图谱表(整图嵌入)      : {n_atlas}')
    print(f'  红旗·行列对齐失败     : {n_align}')
    print(f'  红旗·表头空列         : {n_hdr}')
    print(f'  碎片表·待合并         : {n_frag}')
    print(f'  提示·粘连/错位        : {n_adhs}')
    print(f'  表/图后碎片噪音        : {n_noise_pages}页/{n_noise}表')
    print(f'  无红旗(保留抽检)      : {n_clean}')
    print(f'报告: {md_path}')
    print(f'数据: {json_path}')


if __name__ == '__main__':
    main()
