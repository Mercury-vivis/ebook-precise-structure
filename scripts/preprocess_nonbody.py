#!/usr/bin/env python3
# preprocess_nonbody.py —— 非正文内容识别与标记（批注/注释/引用）
#
# 用途：在 Stage 1（提取）之后、Stage 2（结构清洗）之前，识别正文中混入的
#       非正文内容（夹批/侧批/眉批/脚注/引用等），用统一标记〖批〗...〖/批〗
#       包裹，使 03 全文中批注与正文可清晰区分。
#
# 支持两种输入模式：
#   1. EPUB 模式（--epub-dir）：扫描解包后的 HTML+CSS，按 CSS 类名/字体属性
#      识别批注 span，在 HTML 层包裹〖批〗后输出（供 01b_extract_epub.py 重新提取）。
#   2. PDF 格式模式（--format-jsonl）：读取 01a_raw.py --with-format 或
#      01a_ocr.py 产出的带格式 JSONL，按字体大小/颜色/坐标规则识别批注，
#      输出带〖批〗标记的纯文本。
#
# 标记体系（v1.32.0 起）：
#   〖批〗...〖/批〗  批注——页边小字（侧批）、页顶小字（眉批）、有色小字
#   〖注〗...〖/注〗  注文/译文等通栏小字（正文锚定下的非正文字号层）
#   其余类型（回前批/引用/题诗/凡例等）独立成段，不做特殊标记（易用性筛选）
# 正文锚定：全书通栏行（宽≥页宽×0.4）字号聚类，从大到小取第一个合格簇
# （行数≥2 或 字数占比≥5%）为中位锚——单页锚定在注占多数页原理性失效
# （正文段末短行被通栏过滤剔出、分布连续），全书层正文行绝对数够、双峰可分。
# 修 v1.31.0 全局中位数在注文占半书时落到注文层的盲区。
# 已知物理边界（不自动识别）：同视觉行大小字混排（嵌行夹批，OCR 检测层已融合）、
# 同字号整理本（校记/转写批注行内）——见 SKILL.md §3F。
#
# 易用性筛选原则：只做真正影响正文阅读的——仅行内批注需标识；独立成段或
# 已自然区分的不做标识，避免过度标记影响阅读。
#
# 用法：
#   # EPUB 模式：扫描解包目录，输出修改后的 HTML 到新目录
#   python preprocess_nonbody.py --epub-dir <解包目录> --out-dir <输出目录>
#
#   # EPUB 模式：直接输出带标记的纯文本（不重打包）
#   python preprocess_nonbody.py --epub-dir <解包目录> --text-out <输出.txt>
#
#   # PDF 格式模式：读取 JSONL，输出带标记文本
#   python preprocess_nonbody.py --format-jsonl <fulltext_raw.jsonl> --text-out <输出.txt>
#
#   # 自定义批注 CSS 类（覆盖默认规则）
#   python preprocess_nonbody.py --epub-dir <dir> --annotation-classes small1,small2,small3
#
# 依赖：beautifulsoup4（EPUB 模式）；标准库（PDF 模式）

import os
import re
import json
import argparse
from pathlib import Path

# ---- 解释器守卫（v1.30.0）：先切到 EBOOK_PY 指定的 venv，再 import 第三方库 ----
# 详见 _interp_guard.py。本脚本 bs4 是函数内 lazy import（EPUB 模式才用），
# 更须在模块加载时即完成切换——否则托管解释器（无 bs4）要到跑进 EPUB 模式
# 才 ImportError，守卫生效前提是「任何解释器唤起都落在 venv 依赖上」。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

# ── 默认批注 CSS 类关键词（EPUB 模式）──────────────────────────────────────
# 匹配规则：类名包含以下任一关键词即判定为批注 span
# 经验来源：红楼梦脂评汇校本（small1=红小楷1930处 / small2=小楷1961处 / small3=红极小楷1处）
DEFAULT_ANNOTATION_KEYWORDS = [
    "small",      # 小字（small1/small2/small3）
    "annotation", # 批注
    "comment",    # 评注
    "marginal",   # 旁注
    "interlinear",# 夹注
    "zhupi",      # 脂批（自定义类名）
    "note",       # 注（谨慎，可能匹配 footnote）
]

# 排除关键词（避免误判）
EXCLUDE_KEYWORDS = [
    "footnote",   # 脚注（独立成段，不做行内标记）
    "endnote",    # 尾注
    "quote",      # 引用（独立成段）
    "caption",    # 图注
]

# ── PDF 格式识别规则（JSONL 模式，v1.32.0）────────────────────────────────
# 正文锚定：小字 = size < 该页正文锚 × 此比例
PDF_BODY_RATIO = 0.8
# 通栏行判定：bbox 宽 ≥ 页宽 × 此值（用于正文锚定的行过滤，滤标签/页码等窄块）
PDF_FULL_WIDTH_RATIO = 0.4
# 字号聚类：降序排序后相邻差 ≤ 前值 × 此比例 归同簇，最大字号合格簇即正文层。
# 0.05 实测定档：十一家注正文 35 vs 注文 27-29 间有 30-32 过渡行，0.08 会链式
# 合并两峰导致锚落到注文层（2026-10-16 实测复现后收紧）
PDF_CLUSTER_GAP = 0.05
# 颜色阈值：非黑色（RGB 任一通道偏离 > 阈值）判定为有色批注
PDF_COLOR_THRESHOLD = 50
# 坐标阈值：文本块左边界 < 页宽 × 比例 或 右边界 > 页宽 × (1-比例) 判定为页边批注
PDF_MARGIN_RATIO = 0.15
# 眉批判定：小字块顶部 y < 页高 × 此比例
PDF_TOP_RATIO = 0.08


def parse_css_classes(css_text):
    """从 CSS 文本中提取类名→样式属性映射（简化版，只取 font-size/color/font-family）。"""
    classes = {}
    # 匹配 .classname { ... }
    for m in re.finditer(r'\.([a-zA-Z0-9_-]+)\s*\{([^}]*)\}', css_text):
        cls = m.group(1)
        body = m.group(2)
        props = {}
        for prop in re.finditer(r'([a-z-]+)\s*:\s*([^;]+);', body):
            props[prop.group(1).strip()] = prop.group(2).strip()
        classes[cls] = props
    return classes


def is_annotation_class(cls_name, css_props=None, custom_keywords=None):
    """判断 CSS 类是否为批注类。"""
    keywords = custom_keywords or DEFAULT_ANNOTATION_KEYWORDS
    cls_lower = cls_name.lower()

    # 排除优先
    for exc in EXCLUDE_KEYWORDS:
        if exc in cls_lower:
            return False

    # 关键词匹配
    for kw in keywords:
        if kw in cls_lower:
            return True

    # CSS 属性兜底：字体小 + （有色或楷体）
    if css_props:
        size = css_props.get("font-size", "")
        color = css_props.get("color", "")
        family = css_props.get("font-family", "").lower()
        is_small = bool(re.search(r'(?:0?\.[0-9]+em|[0-9]+px)', size)) and \
                   not size.endswith("1em") and "12px" not in size and "14px" not in size
        is_colored = color and color not in ("#000", "#000000", "black", "inherit")
        is_kaiti = "kai" in family or "楷" in family
        if is_small and (is_colored or is_kaiti):
            return True

    return False


def process_epub_html(html_path, annotation_classes, css_props_map):
    """处理单个 HTML 文件：将批注 span 内容用〖批〗包裹。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise SystemExit("EPUB 模式需要 beautifulsoup4：pip install beautifulsoup4")

    with open(html_path, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    count = 0
    # 遍历所有带 class 的 span（批注通常是 span）
    for span in soup.find_all("span", class_=True):
        classes = span.get("class", [])
        for cls in classes:
            if cls in annotation_classes:
                # 包裹内容：在 span 文本前后插入标记
                # 保留 span 标签结构，只改文本内容
                text = span.get_text()
                if text.strip():
                    span.string = f"〖批〗{text}〖/批〗"
                    count += 1
                break

    return str(soup), count


def process_epub_mode(epub_dir, out_dir=None, text_out=None, custom_classes=None):
    """EPUB 模式主流程。"""
    epub_path = Path(epub_dir)
    if not epub_path.is_dir():
        raise SystemExit(f"EPUB 解包目录不存在：{epub_dir}")

    # 1. 收集所有 CSS
    css_props_map = {}
    css_files = list(epub_path.rglob("*.css"))
    for cssf in css_files:
        text = cssf.read_text(encoding="utf-8", errors="ignore")
        css_props_map.update(parse_css_classes(text))

    # 2. 判定批注类
    if custom_classes:
        annotation_classes = set(custom_classes.split(","))
    else:
        annotation_classes = set()
        for cls, props in css_props_map.items():
            if is_annotation_class(cls, props):
                annotation_classes.add(cls)

    print(f"[epub] 扫描 CSS：{len(css_files)} 个文件，{len(css_props_map)} 个类")
    print(f"[epub] 判定批注类：{sorted(annotation_classes)}")

    # 3. 处理 HTML
    html_files = sorted(epub_path.rglob("*.html")) + sorted(epub_path.rglob("*.xhtml"))
    total_count = 0

    if out_dir:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

    all_texts = []
    for hf in html_files:
        html, count = process_epub_html(hf, annotation_classes, css_props_map)
        total_count += count
        rel = hf.relative_to(epub_path)

        if out_dir:
            dst = out_path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(html, encoding="utf-8")

        if text_out:
            # 提取纯文本（带标记）
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")
                all_texts.append(soup.get_text())
            except ImportError:
                pass

    print(f"[epub] 处理 HTML：{len(html_files)} 个文件，包裹批注 {total_count} 处")

    if text_out:
        Path(text_out).write_text("\n".join(all_texts), encoding="utf-8")
        print(f"[epub] 带标记文本已输出：{text_out}")

    if out_dir:
        print(f"[epub] 修改后 HTML 已输出：{out_dir}")

    return total_count


def cluster_anchor(pairs):
    """正文锚定纯函数（v1.32.0）：字号降序聚类，从大到小取第一个合格簇的中位。

    pairs: [(size, weight)]，weight=该行文本长度（字数）。相邻差 ≤ 前值×
    PDF_CLUSTER_GAP 归同簇；从最大字号簇起取第一个「行数 ≥2 或 字数占比
    ≥5%」的簇——只排除单行孤立装饰大字（篇名/回目/窄标签漏滤时），不排
    除正文本身行少的页（注占半书的书正文行数天然是少数，锚必须取大字号
    层：错也是漏标边界行，取小则零识别）。全不合格时取字数最大簇兜底。
    返回 float 或 None（空列表）。
    """
    if not pairs:
        return None
    items = sorted(((float(s), float(w)) for s, w in pairs), reverse=True)
    total_w = sum(w for _, w in items) or 1.0
    clusters = [[items[0]]]
    for s, w in items[1:]:
        if clusters[-1][-1][0] - s <= clusters[-1][-1][0] * PDF_CLUSTER_GAP:
            clusters[-1].append((s, w))
        else:
            clusters.append([(s, w)])
    for cl in clusters:
        if len(cl) >= 2 or sum(w for _, w in cl) >= total_w * 0.05:
            mids = sorted(s for s, _ in cl)
            return mids[len(mids) // 2]
    big = max(clusters, key=lambda cl: sum(w for _, w in cl))
    mids = sorted(s for s, _ in big)
    return mids[len(mids) // 2]


def classify_nonbody(size, color, bbox, page_w, page_h, anchor,
                     body_x1=None, body_x2=None, body_y_top=None, body_y_bot=None):
    """单行分类（v1.32.0 纯函数）。

    返回 ""（正文）、"pi"（〖批〗）、"zhu"（〖注〗）。
    size 无效或 anchor 缺失 → ""（不臆测）；bbox 无效时小字仍标〖注〗（字号信号
    足以判非正文，位置未知不臆测页边）。
    body_*：全书通栏行分位数估计的版心四界——真侧批/页码在版心**外**（x 外或
    y 外=页眉/页脚带）；段末短行贴版心内缘、书眉在版心上带外，都不该按固定
    页宽/页高比例判（2026-10-16 szyz 段末行、laozi 书眉 y1=173>8%页高 两例
    实测修）。缺省回退页宽/页高比例法。
    """
    if anchor is None:
        return ""
    try:
        s = float(size)
    except (TypeError, ValueError):
        return ""
    if not (s > 0) or s >= anchor * PDF_BODY_RATIO:
        return ""
    # 有色小字（原生 PDF 文字层路径的朱批等）→ 批
    if isinstance(color, (int, float)) and color > PDF_COLOR_THRESHOLD:
        return "pi"
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            x1, y1, x2, y2 = (float(v) for v in bbox[:4])
        except (TypeError, ValueError):
            return "zhu"
        # x 版心界（优先）或页宽比例（回退）
        if body_x1 is not None and body_x2 is not None and body_x2 > body_x1:
            eps = (body_x2 - body_x1) * 0.03
            at_edge = x1 < body_x1 - eps or x2 > body_x2 + eps
        else:
            at_edge = (x1 < page_w * PDF_MARGIN_RATIO
                       or x2 > page_w * (1 - PDF_MARGIN_RATIO))
        # y 版心界（优先）或页高比例（回退）
        if body_y_top is not None and body_y_bot is not None and body_y_bot > body_y_top:
            in_body_y = body_y_top <= y1 and y2 <= body_y_bot
        else:
            in_body_y = not (y1 < page_h * PDF_TOP_RATIO)
        if at_edge or not in_body_y:
            return "pi"           # 版心外小字（侧批/页眉/页脚/页码）
        return "zhu"              # 版心内小字（注文、译文、夹批独立行）
    return "zhu"


def process_pdf_jsonl(jsonl_path, text_out):
    """PDF 格式模式：读取带格式 JSONL（行级或块级粒度均可），按页正文锚定识别
    批注〖批〗与注文〖注〗，输出带标记文本。"""
    if not os.path.isfile(jsonl_path):
        raise SystemExit(f"JSONL 文件不存在：{jsonl_path}")

    # 读取所有行/块（保持输入序）
    blocks = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                blocks.append(json.loads(line))

    if not blocks:
        raise SystemExit("JSONL 为空")

    # 按 phys 分组（组内保持输入序）
    pages = {}
    for b in blocks:
        pages.setdefault(b.get("phys"), []).append(b)

    # 全局回退页宽（某页全无有效 bbox 时用）
    global_w = 1.0
    for b in blocks:
        bbox = b.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            try:
                global_w = max(global_w, float(bbox[2]))
            except (TypeError, ValueError):
                pass

    def _page_valid(pbs):
        """页内有效行：[(block, bbox 或 None)]。"""
        valid = []
        for b in pbs:
            if not str(b.get("text", "")).strip():
                continue
            bbox = b.get("bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                try:
                    valid.append((b, [float(v) for v in bbox[:4]]))
                    continue
                except (TypeError, ValueError):
                    pass
            valid.append((b, None))
        return valid

    # ── 全书正文锚（2026-10-16 实测改定）────────────────────────────────
    # 单页锚定在注占多数的页原理性失效：正文段末短行被通栏过滤剔除后页内
    # 正文簇可能只剩 1 行，且正文/注文字号分布连续（行高噪声 + 行内混排），
    # 页内聚类锚会落到注文层（十一家注 phys=1 实测锚 29、零识别）。全书层
    # 面正文行绝对数足够，双峰可分——故全书通栏行聚类一次定锚，各页共用
    # （同书正文字号恒定；v1.31.0 的全书中位数方向正确，此处换聚类+合格簇）。
    all_valid = [(b, bb) for pbs in pages.values() for b, bb in _page_valid(pbs)]
    all_full = [(b, bb) for b, bb in all_valid if bb]

    def _pct(vals, p):
        """分位数（p∈[0,1]），空序列返回 None。"""
        if not vals:
            return None
        sv = sorted(vals)
        return sv[min(int(p * (len(sv) - 1) + 0.5), len(sv) - 1)]

    # 版心四界：全书通栏行分位数估计（P2/P98 横界防单行异常；P5/P95 纵界）。
    # 真侧批/页码在版心外；段末短行贴版心内缘不算页边；书眉/页脚带=版心 y 界外
    # （2026-10-16 szyz 段末行误判、laozi 书眉 y1=173>8%页高 两例实测后改定）
    if all_full:
        body_x1 = _pct([bb[0] for _, bb in all_full], 0.02)
        body_x2 = _pct([bb[2] for _, bb in all_full], 0.98)
        body_y_top = _pct([bb[1] for _, bb in all_full], 0.05)
        body_y_bot = _pct([bb[3] for _, bb in all_full], 0.95)
    else:
        body_x1 = body_x2 = body_y_top = body_y_bot = None
    if all_full:
        ref_w = max(bb[2] for _, bb in all_full)
        full_pairs = [(float(b["size"]), len(str(b.get("text", "")).strip()))
                      for b, bb in all_full
                      if isinstance(b.get("size"), (int, float))
                      and (bb[2] - bb[0]) >= ref_w * PDF_FULL_WIDTH_RATIO]
        anchor = cluster_anchor(full_pairs) if full_pairs else None
        if anchor is None:
            pairs = [(float(b["size"]), len(str(b.get("text", "")).strip()))
                     for b, _ in all_valid if isinstance(b.get("size"), (int, float))]
            anchor = cluster_anchor(pairs)
    else:
        anchor = None
    if anchor is None:
        print("[pdf] ⚠ 全书无有效字号，未做识别")
    else:
        print(f"[pdf] 全书正文锚：{anchor:.1f}，小字阈值：{anchor * PDF_BODY_RATIO:.1f}")

    output_lines = []
    pi_count = zhu_count = 0
    current_phys = None

    for phys, pbs in pages.items():
        if current_phys is not None:
            output_lines.append("")
        output_lines.append(f"<<<PAGE phys={phys}>>>")
        current_phys = phys

        # 本页页宽/页高（按页计算，修 v1.31.0 全局页宽）
        valid = _page_valid(pbs)
        page_w = max((bb[2] for _, bb in valid if bb), default=global_w)
        page_h = max((bb[3] for _, bb in valid if bb), default=1.0)

        for b, bb in valid:
            text = str(b.get("text", "")).strip()
            if not text:
                continue
            kind = classify_nonbody(b.get("size"), b.get("color"), bb,
                                    page_w, page_h, anchor,
                                    body_x1=body_x1, body_x2=body_x2,
                                    body_y_top=body_y_top, body_y_bot=body_y_bot)
            if kind == "pi":
                output_lines.append(f"〖批〗{text}〖/批〗")
                pi_count += 1
            elif kind == "zhu":
                output_lines.append(f"〖注〗{text}〖/注〗")
                zhu_count += 1
            else:
                output_lines.append(text)

    print(f"[pdf] 文本行：{len(blocks)}，识别批注〖批〗：{pi_count} 处、注文〖注〗：{zhu_count} 处")

    Path(text_out).write_text("\n".join(output_lines), encoding="utf-8")
    print(f"[pdf] 带标记文本已输出：{text_out}")
    return pi_count + zhu_count


def main():
    ap = argparse.ArgumentParser(
        description="非正文内容识别与标记（批注/注释/引用），v1.32.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
标记体系：
  〖批〗...〖/批〗  批注——页边小字（侧批）、页顶小字（眉批）、有色小字
  〖注〗...〖/注〗  注文/译文等通栏小字（全书正文锚定下的非正文字号层）

物理边界（不自动识别）：同视觉行大小字混排（嵌行夹批）、同字号整理本。

易用性筛选：仅行内批注/注文做标记；回前批/引用/题诗/凡例等独立成段不做标记。

示例：
  python preprocess_nonbody.py --epub-dir ./_epub_extract --out-dir ./_epub_marked
  python preprocess_nonbody.py --epub-dir ./_epub_extract --text-out fulltext_annotated.txt
  python preprocess_nonbody.py --format-jsonl fulltext_raw.jsonl --text-out fulltext_annotated.txt
        """)
    ap.add_argument("--epub-dir", help="EPUB 解包目录（含 HTML+CSS）")
    ap.add_argument("--format-jsonl", help="PDF 带格式 JSONL（01a_raw.py --with-format 产出，或 00_ingest_bookocr.py --format-jsonl 转出的 fulltext_raw.jsonl）")
    ap.add_argument("--out-dir", help="EPUB 模式：修改后 HTML 输出目录")
    ap.add_argument("--text-out", help="输出带标记的纯文本文件")
    ap.add_argument("--annotation-classes", default=None,
                    help="自定义批注 CSS 类名（逗号分隔，覆盖默认规则）")
    args = ap.parse_args()

    if not args.epub_dir and not args.format_jsonl:
        ap.error("必须指定 --epub-dir 或 --format-jsonl 之一")

    if args.epub_dir:
        process_epub_mode(
            args.epub_dir,
            out_dir=args.out_dir,
            text_out=args.text_out,
            custom_classes=args.annotation_classes,
        )
    elif args.format_jsonl:
        if not args.text_out:
            ap.error("--format-jsonl 模式必须指定 --text-out")
        process_pdf_jsonl(args.format_jsonl, args.text_out)


if __name__ == "__main__":
    main()
