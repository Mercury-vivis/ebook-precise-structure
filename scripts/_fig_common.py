#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# _fig_common.py —— 表/图 · 页锚 · 图片缺口 的共享口径（00b 审计 / 00c 剥离 / 00d 扫描 共用）
#
# 存在理由：00b / 00c / 00d 三脚本各自复制过同一套页锚正则、表图 marker、img 解析、
#           整页排除表与图片缺口判据。复制即漂移——任一侧改动另一处会静默不同步。
#           本文件是这些口径的**单一真相源**：要改口径只改这里，脚本内不再复制定义。
#
# 三脚本的分工（职责不同，因此本模块不合并它们的业务，只收编共享口径）：
#   00b_audit_tables.py    表格 + 碎片**审计**：只读报告，宁漏不误报（故阈值刻意严于 00c）
#   00c_strip_region_noise.py  表/图后碎片**剥离**：写回正文，不可逆（故阈值宽松，宁宽勿误删）
#   00d_scan_figure_gaps.py    图片缺口**扫描**：只读清单，供 08 回溯补图
#
# 两套阈值刻意不同，均在此显式登记（勿在脚本内另写字面量）：
#   STRIP_SHORT_RATIO = 0.75 —— 00c 剥离口径（v1.9 起从 0.90 硬门槛改为软容忍）
#   AUDIT_SHORT_RATIO = 0.90 —— 00b 审计口径（只报不管剥，保持旧硬门槛不动行为）
#
# 用法：与调用脚本同目录，直接 `from _fig_common import <符号>`（脚本运行即 sys.path 含自身目录）。
# 纯标准库，零依赖；只提供口径定义与纯函数，不含任何跨书状态。
#
# 版本双轨：本文件与各脚本注释里的 v1.x（如「v1.9 起」「v1.16.2 修复」）是**内部沿革标记**——
# 记录该处改动发生于哪次维护，不是脚本版本号。技能版本只守两处：SKILL.md frontmatter 的
# `version` 与 `_meta/SKILLS_REGISTRY.json` 的 `version`；升版本只改这两处，脚本头历史标记不动。

import os
import re

# ---- 解释器守卫（v1.30.0）：本模块被 import 即生效 ----
# 同 _common.py：EBOOK_PY 指向的 venv 与当前解释器不一致时 execv 切换重跑，
# 调用方（含 agent 平台）用哪个解释器唤起都不影响依赖落点。详见 _interp_guard.py。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

# ---- 页锚 / 表图 marker / 图题 ----
# 页锚（PDF 支 <<<PAGE phys=N book=M>>>；EPUB 支同式）
PAGE_RE = re.compile(r"<<<PAGE\s+phys=(\d+)\s+book=[^>]*>>>")

# 表/图结束标记：</table> 或 <img .../>
MARKER_RE = re.compile(r"</table>|<img[^>]*/?>", re.IGNORECASE)

# 闭合包装标签（table/img 外层的 </div></body></html>）
CLOSE_WRAP_RE = re.compile(r"(?:\s*</(?:div|body|html)>)+")

# 图题 div（<div...>图N…</div>），需保留不剥离
CAPTION_DIV_RE = re.compile(r"<div[^>]*>\s*图(?:版)?\d+.*?</div>", re.IGNORECASE | re.DOTALL)

# 图题行（图N …，单独一行；允许行首空白，须行末收束）
CAPTION_LINE_RE = re.compile(r"^\s*图(?:版)?\d+\s*[^\n]*$")

# 章节标题行（markdown 标题 或 「第X章/节」开头）
SECTION_RE = re.compile(r"^#{1,6}\s|^第[一二三四五六七八九十百千\d]+[章节]")

# 句末标点（中文句号、感叹号、问号、省略号）
SENTENCE_END_RE = re.compile(r"[。！？…]")

# <img ... src="..."> 中的 src
IMG_SRC_RE = re.compile(r'<img\b[^>]*?src=["\']([^"\']+)["\']', re.IGNORECASE)

# ---- 共享阈值 ----
SHORT_LINE_THRESHOLD = 15        # 「短行」字数上限（去空白后）
MIN_NONEMPTY_LINES = 3           # 碎片区最少非空行数（00b 审计 / 00c 剥离门控）
MIN_NONEMPTY = 5                 # 整页判据最少非空行数（C 类图片缺口）

# 00c 剥离口径：碎片区短行占比门槛（v1.9 起为软容忍；旧 0.90 硬门槛会漏剥）
STRIP_SHORT_RATIO = 0.75
# 00b 审计口径：碎片页短行占比门槛（只报不管剥，沿用旧硬门槛，刻意严于剥离口径）
AUDIT_SHORT_RATIO = 0.90
# C 类（疑似图片文字层）判据
C_SHORT_RATIO = 0.80
C_PUNCT_RATIO = 0.02

# ---- 整页排除表（00c 剥离 / 00d 扫描 同口径：标题 / 目录 / 版权页不是图片缺口或碎片）----
MD_HEADING_RATIO = 0.30          # 非空行中 # 开头行占比
TOC_KEYWORDS = ("目録", "目录", "目次", "Contents")
TOC_MIN_NONEMPTY = 5
TOC_SHORT_RATIO = 0.70
COPYRIGHT_KEYWORDS = ("ISBN", "版权所有", "出版发行", "出版社", "定价", "印张")
COPYRIGHT_MIN_HITS = 2


def resolve_deliv(work, deliv_arg):
    """解析交付目录：--deliv 显式指定优先；否则按 `.work` 标记向上定位交付根。

    工作目录形如 `<交付根>/.work/books/<书名>`，对应交付目录为 `<交付根>/<书名>`。
    按 `.work` 标记逐级上找而非按固定层数上跳，工作目录层级调整时不会静默解析错位；
    未找到标记（非标准布局）时退回上跳三层。
    """
    if deliv_arg:
        return deliv_arg
    parent = os.path.basename(work.rstrip("\\/"))
    cur = os.path.abspath(work)
    while True:
        cur, tail = os.path.split(cur)
        if not tail:
            break
        if tail == ".work":
            return os.path.join(cur, parent)
    return os.path.normpath(os.path.join(work, "..", "..", "..", parent))


def build_available_imgs(work, deliv):
    """收集 work/imgs 与 deliv/imgs 下所有文件名集合。"""
    avail = set()
    for d in (os.path.join(work, "imgs"), os.path.join(deliv, "imgs")):
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if os.path.isfile(os.path.join(d, fn)):
                    avail.add(fn)
    return avail


def find_missing_imgs(body, available):
    """返回页内 <img src> 引用中、work/deliv imgs 都不存在的文件名列表。"""
    missing = []
    for src in IMG_SRC_RE.findall(body):
        fn = os.path.basename(src.replace("\\", "/"))
        if fn and fn not in available:
            missing.append(fn)
    return sorted(set(missing))


def is_excluded_nonfigure_page(body):
    """整页是否为「非图片缺口 / 非碎片」的特殊页（标题 / 目录 / 版权）。

    00c 与 00d 同口径：这类页整页性质特殊，跳过后交人工或 Stage3，不误送 08 补图。
    """
    nonempty = [l.strip() for l in body.split("\n") if l.strip()]
    n = len(nonempty)
    if n == 0:
        return False
    # ① markdown 标题页
    if sum(1 for l in nonempty if l.startswith("#")) / n >= MD_HEADING_RATIO:
        return True
    # ② 目录页
    if any(kw in body for kw in TOC_KEYWORDS):
        short = sum(1 for l in nonempty if len(l) < SHORT_LINE_THRESHOLD)
        if n >= TOC_MIN_NONEMPTY and short / n >= TOC_SHORT_RATIO:
            return True
    # ③ 版权页
    if sum(1 for kw in COPYRIGHT_KEYWORDS if kw in body) >= COPYRIGHT_MIN_HITS:
        return True
    return False


def scan_figure_gap_body(body):
    """单页 body 的 C 类判据：无 table/img marker 的短行散字 = 疑似整图页文字层。

    返回 stats dict 或 None。00c 用它「只标记不剥离」（无图可替代，剥离即丢信息），
    00d 用它生成「C 类图片缺口」清单——判据同源，避免两侧阈值各写各的。
    """
    if MARKER_RE.search(body):
        return None
    if is_excluded_nonfigure_page(body):
        return None
    lines = [l.strip() for l in body.split("\n")]
    nonempty = [l for l in lines if l]
    n = len(nonempty)
    if n < MIN_NONEMPTY:
        return None
    short = sum(1 for l in nonempty if len(l) < SHORT_LINE_THRESHOLD)
    short_ratio = short / n
    n_punct = sum(1 for l in nonempty if SENTENCE_END_RE.search(l))
    if short_ratio >= C_SHORT_RATIO and n_punct <= max(1, n * C_PUNCT_RATIO):
        return {"n": n, "short_ratio": round(short_ratio, 3),
                "n_punct": n_punct, "sample": " / ".join(nonempty[:3])[:60]}
    return None
