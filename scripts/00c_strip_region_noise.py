#!/usr/bin/env python3
# 00c_strip_region_noise.py —— 表/图后碎片噪音确定性剥离 v1.9.3（逐行分类 + 软容忍 + 部分剥离 + 整页排除 + img 断链保护 + REGION 标记感知）
#
# 用途：PaddleOCR（book_ocr.py）在输出 HTML <table> / <img> 的同时，会把表格单元格
#       内容和图片标注文字再以纯文本逐行输出在表/图之后，形成「断句碎片行」噪音。
#       本脚本确定性检测并剥离这些碎片，使 03 正文不含表格数据重复和图片标注乱码。
#
# v1.9 重构要点（从「整块硬门槛」改为「逐行分类 + 软容忍 + 部分剥离」）：
#   1. marker 后逐行分类：图题行/章节标题行 → 保留；漏剥页眉页脚/碎片图注行 → 剥离；
#      正文行 → 保留，且首次出现正文即停止碎片区（其后全部保留）。
#   2. 软指标：短行占比阈值 90%→75%；孤立句末标点行容忍（标点行占比<3% 且去标点后
#      仍<15字 → 算碎片，不再零容忍句末标点）；新增长图注识别（编号开头 / 坐标注记 /
#      器物名单字行）。
#   3. 章节标题守卫细化：漏剥页眉（页首重复「第X章」）剥离；真正章节标题 → 保留该行，
#      其后碎片继续剥离（不再整块否决）。
#   4. 00c 自行识别漏剥页眉/书眉（纯数字页码、重复书名），不再依赖 assemble 阶段。
#   5. 扩大扫描：对全部页检测；但无 table/img marker 的页只标记「疑似图片文字层」、
#      不自动剥离（无图可替代，剥离即丢信息）。
#   6. 部分剥离：marker 后前 N 行碎片 → 剥离，之后出现正文 → 保留正文及之后。
#
# v1.9.1 漏洞修复（在 detect_fragment 之前先做整页排除 + img 存在性前置检查）：
#   7. 整页排除表（命中即整页不剥离、不计入碎片页总数，单列「排除页」区段）：
#      ① Markdown 标题页：非空行中 `#` 开头行占比 ≥30%；
#      ② 目录页：含 目録/目录/目次/Contents 且非空行≥5、短行占比≥70%；
#      ③ 版权页：含 ISBN/版权所有/出版发行/出版社/定价/印张 中 ≥2 个关键词；
#      ④ 表格页：页内主体是 <table> 块且 marker 后碎片区全为表数据短行（留待 00b/
#         Stage3 重建表取单元格文本，00c 不剥）；若表后混有非表格纯文本碎片仍正常剥离。
#   8. img 存在性前置检查：含 <img src="imgs/xxx.jpg"> 的页，剥离前先查 work/imgs/、
#      再查 deliv/imgs/；任一引用图缺失 → 不剥离该页、单列「待补图页」（删字=永久丢失）。
#
# v1.9.3（REGION 标记感知，Phase A）：
#   9. book_ocr.py 在每页 <table> 块与 <img>(+图题) 外裹 <!--REGION:TABLE/FIGURE phys=N-->
#      注释（见 book_ocr.wrap_regions）。本脚本 detect_with_marker 把这些关闭注释并入
#      marker 集合，取「最靠后结束位置」作边界——比纯 </table>/<img> 正则更贴近引擎语义边界。
#   10. 向后兼容：无 REGION 注释的页（PDF/EPUB 路径，或旧 book_ocr 输出）自动退回 MARKER_RE
#       旧逻辑，行为不变（实证：许倬云《西周史》_OCR.pdf 新旧 00c 输出逐字节一致）。
#   11. apply 写回时用 REGION_COMMENT_RE 清掉全部 REGION 注释，不进成品 fulltext_struct.txt。
#
# 用法：
#   python 00c_strip_region_noise.py --work <工作目录> [--deliv <交付目录>] [--out <报告目录>] [--apply]
#   --work   含 fulltext_struct.txt 的目录
#   --deliv  交付目录（<书名>）；缺省按 work 路径推断（../../../<basename>）
#   --out    报告输出目录，缺省 work
#   --apply  缺省 dry-run（仅出报告不写回）；加 --apply 才剥离并写回 fulltext_struct.txt
#
# 在流程中的位置：00b_audit_tables.py 之后、Stage 3 语义清洗之前。
# 流程：ingest → 00b 表格审计 → 00c 碎片剥离(本脚本) → 00d 图片缺口扫描 → Stage3
#
# 保留：图题（<div...>图N…</div>）、章节标题行、含完整句的正文、caption。
# 幂等：剥离后再跑无碎片可检，自然幂等。
# --apply：被删内容逐块写入 region_noise_log.md 台账（phys+完整原文），可恢复。
# 全 LF、原子写（先 .tmp 再 replace）。

import os
import re
import sys
import argparse

# ---- 共享口径（单一真相源：同目录 _fig_common.py）----
# 页锚 / 表图 marker / 图题 / 句末标点 / img src 正则，整页排除表常量与短行阈值，
# 以及交付目录解析、img 可用性、C 类图片文字层判据，一律取自 _fig_common。
# 本脚本不再复制这些定义——复制即漂移（历史上 00b 就与 00c 的碎片阈值分叉过）。
from _fig_common import (
    PAGE_RE, MARKER_RE, CLOSE_WRAP_RE, CAPTION_DIV_RE, CAPTION_LINE_RE,
    SECTION_RE, SENTENCE_END_RE, IMG_SRC_RE,
    SHORT_LINE_THRESHOLD, MIN_NONEMPTY_LINES, MD_HEADING_RATIO,
    TOC_KEYWORDS, TOC_SHORT_RATIO, TOC_MIN_NONEMPTY,
    COPYRIGHT_KEYWORDS, COPYRIGHT_MIN_HITS,
    STRIP_SHORT_RATIO, is_excluded_nonfigure_page, scan_figure_gap_body,
    resolve_deliv, build_available_imgs, find_missing_imgs,
)

# 页锚 / 表图 marker / 闭合包装 / 图题 div / 纯图题行 / 章节标题 / 句末标点，
# 七项口径全部来自 _fig_common（见上方 import），本脚本不再本地定义——
# 本地再写一遍会覆盖 import、且日后改 _fig_common 时静默分叉（00b 已吃过这个亏）。

# 去标点后用于「是否仍成句」判定
PUNCT_STRIP_RE = re.compile(r"[。！？…，、；：·\s\.\,\;\:\?\!（）()【】\[\]《》〈〉「」“”\"']")

# 图注行：以编号/序号开头（1、1—、1-、①…⑳）
FIG_NOTE_START_RE = re.compile(r"^\d+\s*[—\-]?\s*\S|^[①-⑳]")
# 图注行：含坐标/括号注记（2:20、（上：…））
COORD_RE = re.compile(r"\d+\s*[:：]\s*\d+|（[^）]*[:：][^）]*）")
# 器物名独立成行（考古图版常见单字/两字器名）
ARTIFACT_RE = re.compile(r"^(鼎|簋|鬲|斝|觚|爵|甗|盉|匜|盘|簠|敦|豆|壶|卣|罍|瓿|钵|尊|彝|盘|匜)$")

# 纯数字页码行（1–4 位）
PURE_NUM_RE = re.compile(r"^\d{1,4}$")

# 正常文句标点（逗号/分号/顿号/句号），用于把长 OCR 垃圾行与真正文区分
PROSE_PUNCT_RE = re.compile(r"[，；、。]")

# ---- 本脚本独有判据（00c 专属，非共享口径）----
# 软指标：孤立句末标点行容忍占比
PUNCT_LINE_RATIO = 0.03
# ④ 表格页：<table> 块占整页长度比阈值（页内主体是表格）
TABLE_BLOCK_RATIO = 0.30

# <table>...</table> 整块
TABLE_BLOCK_RE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
# 表数据短行：不含任何文句标点（句末/句内），用于判定「碎片区全是表数据」
TABLE_DATA_PUNCT_RE = re.compile(r"[。！？…，；：、]")

# 区域标记关闭注释（book_ocr 2026-09-27 注入）：REGION 边界优先于 MARKER_RE 旧逻辑；
# 无 REGION 时退回 </table>/<img> 旧边界（向后兼容，PDF/EPUB 路径不受影响）。
REGION_CLOSE_RE = re.compile(r"<!--/REGION:(?:TABLE|FIGURE)\b[^>]*-->")
# REGION 注释本体（开/闭），apply 写回时清掉（仅作 00c 边界信号，不进成品）
REGION_COMMENT_RE = re.compile(r"<!--/?REGION:[A-Z]+\b[^>]*-->\s*\n?")


def parse_pages(text):
    """把 fulltext_struct 拆为 [(phys, anchor, body)]，保留锚前文本。"""
    pages = []
    matches = list(PAGE_RE.finditer(text))
    for i, m in enumerate(matches):
        phys = int(m.group(1))
        anchor_end = m.end()
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[anchor_end:next_start].rstrip("\n")
        pages.append((phys, m.group(0), body))
    return pages, text[:matches[0].start()] if matches else ("", text)


def is_caption_line(s):
    return bool(CAPTION_LINE_RE.match(s))


def is_heading_line(s):
    return bool(SECTION_RE.match(s))


def is_figure_note(s):
    """长图注识别：编号开头 / 坐标注记 / 器物名独立成行 → 判图注。"""
    if FIG_NOTE_START_RE.match(s):
        return True
    if COORD_RE.search(s):
        return True
    if ARTIFACT_RE.match(s):
        return True
    return False


def is_body_line(s):
    """正文行：含句末标点且去标点后仍成句；或长行且带正常文句标点密度（防 OCR 长垃圾行误判）。"""
    if SENTENCE_END_RE.search(s):
        rest = PUNCT_STRIP_RE.sub("", s)
        if len(rest) >= 15:
            return True
    if len(s) >= 40 and len(PROSE_PUNCT_RE.findall(s)) >= 2:
        return True
    return False


def classify_region(body, pos):
    """对 marker 后逐行分类。

    返回 (kept_region, stripped_text, stats)。
      kept_region  = 保留下来的 region 文本（已剥掉碎片行）
      stripped_text= 被剥掉的完整原文（用于台账）
      stats        = dict(kept, stripped, short_ratio, n_punct, sample)
    """
    region = body[pos:]
    lines = region.split("\n")

    decisions = []  # (idx, action)  action in {keep, strip, blank}
    stop = False    # 首次正文出现后停止碎片区
    for idx, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            decisions.append((idx, "blank"))
            continue
        if stop:
            decisions.append((idx, "keep"))
            continue
        # 图题 div / 图题行 → 保留
        if CAPTION_DIV_RE.match(s) or is_caption_line(s):
            decisions.append((idx, "keep"))
            continue
        # 章节标题行 → 保留该行，其后碎片继续剥离
        if is_heading_line(s):
            decisions.append((idx, "keep"))
            continue
        # 长图注 → 剥离（即使较长也判图注）
        if is_figure_note(s):
            decisions.append((idx, "strip"))
            continue
        # 漏剥页眉/页脚：纯数字页码行 → 剥离
        if PURE_NUM_RE.match(s):
            decisions.append((idx, "strip"))
            continue
        # 正文行 → 保留并停止碎片区
        if is_body_line(s):
            decisions.append((idx, "keep"))
            stop = True
            continue
        # 其余短行 → 碎片候选
        decisions.append((idx, "strip"))

    strip_lines = [lines[i] for i, a in decisions if a == "strip"]
    kept_lines = [lines[i] for i, a in decisions if a in ("keep", "blank")]
    strip_nonempty = [l.strip() for l in strip_lines if l.strip()]

    # 软指标门控
    n = len(strip_nonempty)
    passed = False
    short_ratio = 0.0
    n_punct = 0
    if n >= MIN_NONEMPTY_LINES:
        short = sum(1 for l in strip_nonempty if len(l) < SHORT_LINE_THRESHOLD)
        short_ratio = short / n
        n_punct = sum(1 for l in strip_nonempty if SENTENCE_END_RE.search(l))
        punct_ratio = n_punct / n
        # 孤立句末标点容忍：标点行占比<3% 且每行去标点后仍<15字 → 算碎片
        isolated_ok = all(
            len(PUNCT_STRIP_RE.sub("", l)) < SHORT_LINE_THRESHOLD
            for l in strip_nonempty if SENTENCE_END_RE.search(l)
        )
        if short_ratio >= STRIP_SHORT_RATIO and (
            n_punct == 0 or (punct_ratio < PUNCT_LINE_RATIO and isolated_ok)
        ):
            passed = True

    if not passed:
        return region, "", {"kept": len([1 for _, a in decisions if a == "keep"]),
                           "stripped": 0, "short_ratio": short_ratio,
                           "n_punct": n_punct, "sample": ""}

    kept_region = "\n".join(kept_lines).rstrip("\n")
    stripped_text = "\n".join(l for l in strip_lines if l.strip())
    sample = " / ".join(strip_nonempty[:3])[:60]
    stats = {"kept": len([1 for _, a in decisions if a == "keep"]),
             "stripped": n, "short_ratio": short_ratio,
             "n_punct": n_punct, "sample": sample}
    return kept_region, stripped_text, stats


def detect_with_marker(body):
    """对含 table/img 的页：定位最后一个 marker，跳过闭合标签，逐行分类。

    2026-09-27：marker 集合并入 REGION 关闭注释（book_ocr 注入）。REGION 边界优先
    （取所有 marker 中最靠后的结束位置）；无 REGION 时退回 </table>/<img> 旧边界。
    向后兼容：PDF/EPUB 输入无 REGION、亦无 table/img，本函数自然返回 None（no-op）。

    返回 (marker_pos, kept_region, stripped_text, stats) 或 (None, None, None, None)。
    """
    markers = list(MARKER_RE.finditer(body)) + list(REGION_CLOSE_RE.finditer(body))
    if not markers:
        return None, None, None, None
    markers.sort(key=lambda m: m.start())
    pos = markers[-1].end()
    # 跳过闭合包装标签与空白
    while pos < len(body):
        m = CLOSE_WRAP_RE.match(body, pos)
        if m:
            pos = m.end()
        else:
            break
    kept_region, stripped_text, stats = classify_region(body, pos)
    if not stripped_text:
        return None, None, None, None
    return pos, kept_region, stripped_text, stats


def scan_no_marker(body):
    """对无 table/img 的页：只标记「疑似图片文字层」，不剥离。

    判据取自 _fig_common.scan_figure_gap_body（与 00d 的 C 类同源）：
    非空行≥MIN_NONEMPTY、短行(<SHORT_LINE_THRESHOLD字)≥C_SHORT_RATIO、
    句末标点行≤max(1, n×C_PUNCT_RATIO)、且非标题/目录/版权排除页。
    返回 stats dict 或 None。
    """
    return scan_figure_gap_body(body)


# ---- v1.9.1 整页排除表（types ①②③）----
def classify_page_exclusion(body):
    """在任何碎片判定之前，先判整页是否属于排除类型。

    返回 (排除类型, 说明) 或 None。types ①②③ 为整页否决（与有无 marker 无关）。
    ①②③ 的常量与判据同 00d 共用 _fig_common.is_excluded_nonfigure_page，
    此处只是把「命中原因」取出来写进审计表。
    """
    lines = [l.strip() for l in body.split("\n")]
    nonempty = [l for l in lines if l]
    n = len(nonempty)
    if n == 0 or not is_excluded_nonfigure_page(body):
        return None

    # ① Markdown 标题页：非空行中 # 开头行占比 ≥MD_HEADING_RATIO
    n_hash = sum(1 for l in nonempty if l.startswith("#"))
    if n_hash / n >= MD_HEADING_RATIO:
        return ("markdown标题页", f"#开头行 {n_hash}/{n}={n_hash/n*100:.0f}%")

    # ② 目录页：含目录关键词 + 非空行≥TOC_MIN_NONEMPTY + 短行占比≥TOC_SHORT_RATIO
    if any(kw in body for kw in TOC_KEYWORDS):
        short = sum(1 for l in nonempty if len(l) < SHORT_LINE_THRESHOLD)
        if n >= TOC_MIN_NONEMPTY and short / n >= TOC_SHORT_RATIO:
            hit = [kw for kw in TOC_KEYWORDS if kw in body]
            return ("目录页", f"命中{'/'.join(hit)}，短行比{short/n*100:.0f}%")

    # ③ 版权页：含 ≥COPYRIGHT_MIN_HITS 个版权关键词
    hits = [kw for kw in COPYRIGHT_KEYWORDS if kw in body]
    if len(hits) >= COPYRIGHT_MIN_HITS:
        return ("版权页", f"命中{len(hits)}个关键词：{'/'.join(hits)}")

    return None


def is_table_data_line(s):
    """表数据短行：短且不含任何文句标点（数字/器物名/坐标注记之类）。"""
    s = s.strip()
    if not s or len(s) >= SHORT_LINE_THRESHOLD:
        return False
    if TABLE_DATA_PUNCT_RE.search(s):
        return False
    return True


def looks_like_whole_table(body, stripped_text):
    """④ 表格页判定：页内主体是 <table> 块，且 marker 后碎片区全为表数据短行。

    这类页的「碎片」其实是表格单元格文本的纯文本重复——Stage3 重建表需要它，
    00c 不剥。若碎片区混有非表格纯文本碎片，本判定不成立，正常剥离。
    """
    m = TABLE_BLOCK_RE.search(body)
    if not m:
        return False
    body_len = len(body.strip())
    if body_len == 0:
        return False
    if len(m.group(0)) / body_len < TABLE_BLOCK_RATIO:
        return False
    frag_lines = [l.strip() for l in stripped_text.split("\n") if l.strip()]
    if len(frag_lines) < MIN_NONEMPTY_LINES:
        return False
    if all(is_table_data_line(l) for l in frag_lines):
        return True
    return False


# ---- v1.9.1 img 存在性前置检查 ----
# resolve_deliv / build_available_imgs / find_missing_imgs 已上移 _fig_common（与 00d 同源）。


def main():
    ap = argparse.ArgumentParser(description="表/图后碎片噪音确定性剥离 v1.9.3（逐行分类+部分剥离+整页排除+img断链保护+REGION标记感知）")
    ap.add_argument("--work", required=True, help="工作目录（含 fulltext_struct.txt）")
    ap.add_argument("--struct", default="", help="直接指定 struct 文件路径（优先于 --work）")
    ap.add_argument("--deliv", default="", help="交付目录（<书名>）；缺省按 work 路径推断")
    ap.add_argument("--out", default="", help="报告输出目录，缺省 work")
    ap.add_argument("--apply", action="store_true", help="实际剥离并写回（缺省仅 dry-run 出报告）")
    args = ap.parse_args()

    struct_path = args.struct or os.path.join(args.work, "fulltext_struct.txt")
    if not os.path.exists(struct_path):
        sys.exit(f"ERROR: 找不到 {struct_path}")

    out_dir = args.out or args.work
    os.makedirs(out_dir, exist_ok=True)

    deliv = resolve_deliv(args.work, args.deliv)
    available_imgs = build_available_imgs(args.work, deliv)

    with open(struct_path, encoding="utf-8") as f:
        text = f.read()

    pages, preamble = parse_pages(text)
    if not pages:
        print("未检测到页锚，退出。")
        return

    stripped_pages = []   # [(phys, marker_pos, kept_region, stripped_text, stats)]
    suspected = []        # 无 marker 的疑似图片文字层 [(phys, stats)]
    excluded_pages = []   # 整页排除 [(phys, 类型, 说明)]
    pending_figs = []     # img 断链待补图 [(phys, [缺失img])]
    pages_with_marker = 0

    for phys, anchor, body in pages:
        # ①②③ 整页排除表：先于一切碎片判定（与有无 marker 无关）
        ex = classify_page_exclusion(body)
        if ex:
            excluded_pages.append((phys, ex[0], ex[1]))
            continue

        has_marker = ("<table" in body.lower()) or ("<img" in body.lower())
        if has_marker:
            pages_with_marker += 1
            # img 存在性前置检查：任一引用图缺失 → 不剥（删字=永久丢失）
            missing = find_missing_imgs(body, available_imgs)
            if missing:
                pending_figs.append((phys, missing))
                continue
            pos, kept_region, stripped_text, stats = detect_with_marker(body)
            if pos is not None:
                # ④ 表格页：碎片全为表数据短行且页主体是表格 → 留待重建，不剥
                if looks_like_whole_table(body, stripped_text):
                    excluded_pages.append((phys, "表格页",
                                           f"表块占主体，碎片{stats['stripped']}行全为表数据短行，留待00b/重建"))
                    continue
                stripped_pages.append((phys, pos, kept_region, stripped_text, stats))
        else:
            s = scan_no_marker(body)
            if s:
                suspected.append((phys, s))

    # ---- 审计报告 ----
    audit = [
        "# 表/图后碎片噪音审计报告（v1.9.3 逐行分类 + 部分剥离 + 整页排除 + img断链保护 + REGION标记感知）",
        "",
        f"> 来源：`{struct_path}`",
        f"> 交付目录：`{deliv}`（work/deliv imgs 可用文件 {len(available_imgs)} 个）",
        f"> 总页数：{len(pages)}；含 table/img 页：{pages_with_marker}；命中剥离页：**{len(stripped_pages)}**；"
        f"整页排除：{len(excluded_pages)}；待补图页：{len(pending_figs)}；"
        f"疑似图片文字层（无 marker，仅标记）：{len(suspected)}",
        f"> 规则：短行(<{SHORT_LINE_THRESHOLD}字)占比≥{STRIP_SHORT_RATIO*100:.0f}%；"
        f"孤立句末标点行容忍（占比<{PUNCT_LINE_RATIO*100:.0f}% 且去标点后<{SHORT_LINE_THRESHOLD}字）；"
        f"图注/纯数字页码剥离；正文出现即停；整页排除表（markdown标题/目录/版权/表格页）；"
        f"img 引用缺失则不剥（待补图）。",
        "",
        "## 逐页明细（含 marker，命中剥离）",
        "",
        "| phys | 剥离行数 | 保留行数 | 短行比 | 句末标点数 | 样例 |",
        "|------|----------|----------|--------|------------|------|",
    ]
    for phys, pos, kept_region, stripped_text, stats in stripped_pages:
        audit.append(
            f"| {phys} | {stats['stripped']} | {stats['kept']} | "
            f"{stats['short_ratio']*100:.0f}% | {stats['n_punct']} | {stats['sample']} |"
        )

    # 整页排除区段（不计入碎片页总数）
    audit += [
        "",
        "## 整页排除页（不剥离、不计入碎片页总数）",
        "",
        "> markdown标题页/目录页/版权页/表格页：整页性质特殊，00c 不动，交人工/Stage3。",
        "",
        "| phys | 排除类型 | 说明 |",
        "|------|----------|------|",
    ]
    for phys, etype, detail in excluded_pages:
        audit.append(f"| {phys} | {etype} | {detail} |")

    # 待补图页区段（img 引用断链，不剥）
    audit += [
        "",
        "## 待补图页（img 引用缺失，整页不剥离）",
        "",
        "> 页内 <img> 引用的文件在 work/imgs 与 deliv/imgs 均不存在——删字=永久丢失，故不剥离；"
        "交 00d 列出 → 08 回溯补图后重跑本脚本。",
        "",
        "| phys | 缺失 img |",
        "|------|----------|",
    ]
    for phys, missing in pending_figs:
        audit.append(f"| {phys} | {', '.join(missing)} |")

    audit += [
        "",
        "## 疑似图片文字层（无 table/img marker，仅标记不剥离）",
        "",
        "> 无图可替代，剥离即丢信息；交 00d 扫描 → 08 回溯补图处理。",
        "",
        "| phys | 非空行数 | 短行比 | 句末标点数 | 样例 |",
        "|------|----------|--------|------------|------|",
    ]
    for phys, s in suspected:
        audit.append(
            f"| {phys} | {s['n']} | {s['short_ratio']*100:.0f}% | {s['n_punct']} | {s['sample']} |"
        )

    audit.append("")
    if args.apply:
        audit.append("> **模式：--apply（已剥离）**，被删内容见 region_noise_log.md")
    else:
        audit.append("> **模式：dry-run（未写回）**，加 --apply 执行剥离")

    audit_path = os.path.join(out_dir, "region_noise_audit.md")
    with open(audit_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(audit) + "\n")

    print(f"审计完成：{len(pages)} 页，含表/图 {pages_with_marker} 页，"
          f"命中剥离 {len(stripped_pages)} 页，整页排除 {len(excluded_pages)} 页，"
          f"待补图 {len(pending_figs)} 页，疑似图片文字层 {len(suspected)} 页")
    print(f"报告：{audit_path}")

    if not stripped_pages:
        print("无碎片可剥离。")
        return

    if not args.apply:
        print("（dry-run，未写回。加 --apply 执行剥离）")
        for phys, pos, kept_region, stripped_text, stats in stripped_pages[:10]:
            print(f"  phys={phys}: 剥{stats['stripped']}行 留{stats['kept']}行 "
                  f"短行比{stats['short_ratio']*100:.0f}% 样例={stats['sample']}")
        return

    # ---- --apply：写台账 + 原子写回 ----
    frag_by_phys = {phys: (pos, kept_region, stripped_text)
                    for phys, pos, kept_region, stripped_text, _ in stripped_pages}

    log = [
        "# 表/图后碎片噪音剥离台账（可恢复 · v1.9.3）",
        "",
        f"> 来源：`{struct_path}`",
        f"> 剥离页数：{len(stripped_pages)}",
        f"> 规则：短行≥{STRIP_SHORT_RATIO*100:.0f}% + 孤立标点容忍 + 逐行分类部分剥离；整页排除/待补图页不剥",
        "",
    ]

    new_pages = []
    total_removed = 0
    for phys, anchor, body in pages:
        if phys in frag_by_phys:
            pos, kept_region, stripped_text = frag_by_phys[phys]
            new_body = body[:pos].rstrip("\n") + "\n" + kept_region
            total_removed += len(body) - len(new_body)
            log.append(f"## phys={phys}（剥离 {len(stripped_text)} 字符）")
            log.append("")
            log.append("```")
            log.append(stripped_text.rstrip())
            log.append("```")
            log.append("")
            new_pages.append((phys, anchor, new_body))
        else:
            new_pages.append((phys, anchor, body))

    log_path = os.path.join(out_dir, "region_noise_log.md")
    with open(log_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(log) + "\n")

    new_text = preamble
    for phys, anchor, body in new_pages:
        new_text += anchor + "\n" + body + "\n"
    new_text = new_text.rstrip("\n") + "\n"
    # 2026-09-27：清掉 REGION 注释（仅作本脚本边界信号，不进成品 fulltext_struct.txt）
    new_text = REGION_COMMENT_RE.sub("", new_text)

    tmp_path = struct_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_text)
    os.replace(tmp_path, struct_path)

    print(f"已剥离 {len(stripped_pages)} 页碎片，共 {total_removed} 字符")
    print(f"台账：{log_path}")
    print(f"已写回：{struct_path}")


if __name__ == "__main__":
    main()
