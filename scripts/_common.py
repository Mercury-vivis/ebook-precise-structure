#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_common.py —— ebook-precise-structure 技能共享内部模块

pipeline_assemble.py（PDF 路径）与 assemble_epub.py（EPUB 路径）共用的确定性匹配工具。
抽到此处单一真相源，避免「改一处忘另一处」的双路径漂移（B3 一期）。

两脚本运行方式均为 `python scripts/<脚本>.py ...`，脚本所在目录自动进入 sys.path[0]，
故 `from _common import ...` 可直接解析（无需改 PYTHONPATH）。
"""

import os
import re  # 预留；当前 _nchar / find_span 不依赖，仅保持导入一致
import glob  # _load_corr_entries / collect_systematic_olds 扫描 corrections/*.json 用


def _nchar(ch):
    """归一化单字：全角 ASCII -> 半角；全角空格 -> 半角空格；所有空白 -> 空（匹配时忽略）。"""
    cp = ord(ch)
    if 0xFF01 <= cp <= 0xFF5E:
        return chr(cp - 0xFEE0)
    if ch == "\u3000":
        return " "
    if ch.isspace():
        return ""
    return ch


# ---- R6 性能修复（2026-09-08）：快速归一化（语义与 _nchar 完全一致）----
# _WS_CHARS 是 CPython str.isspace() 的完整字符集；归一化时除 \u3000（保留为空格语义）外全部删除。
# 实现用正则两步（全 C 级）：全角区替换（稀疏回调）+ 空白删除。37 万字符 ~3ms
# （逐字符 _nchar 生成器 ~190ms；str.translate 的 dict 映射走慢路径 ~70ms，弃用）。
_WS_CHARS = "\t\n\r\v\f\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
_DEL_SET = frozenset(c for c in _WS_CHARS if c != "\u3000")  # 归一化时被删除的字符（命中后位置映射用）
_WS_RE = re.compile("[\t\n\r\v\f\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f]+")
_FW_RE = re.compile("[\uff01-\uff5e]")


def _norm_fast(s):
    """快速归一化：全角 ASCII -> 半角；\\u3000 原样保留（占位语义 = _nchar 的 ' '，两侧同函数故匹配一致）；其余 isspace 删除。
    与 _nchar 逐字符映射的匹配语义严格一致（已由随机 fuzz 验证：needle/hay 中 \\u3000 与
    半角空格的交叉场景，新旧实现的命中/失配判定完全相同）。
    快路径：全角字符在 OCR 文本中极稀疏，无全角时跳过全角替换步（37 万字符 ~19ms -> ~10ms）。"""
    if _FW_RE.search(s):
        s = _FW_RE.sub(lambda m: chr(ord(m.group()) - 0xFEE0), s)
    return _WS_RE.sub("", s)


# 「仅容忍换行」正则的进程内缓存（无上限）。
# find_span 第 2 级与 systematic 通改共用同一个模式构造（needle 去 \n\r 后逐字 re.escape、
# 字间插 [\n\r]*），此前每次都现编：re 模块的内建缓存仅 512 项，而本库 needle 种类
# （逐页补丁 + 通改合计近 2800 条）远超此数，命中率低、反复重编——实测 4029 次
# re.compile 占 apply_corrections_core 全程耗时约 32%。此处按模式串直接缓存，一次编译复用。
_LB_RE_CACHE = {}


def _lb_re(ns):
    """取（或新建）「ns 各字间允许任意 \\n/\\r」的正则。ns 须为已去 \\n\\r 的串。"""
    rx = _LB_RE_CACHE.get(ns)
    if rx is None:
        rx = re.compile("[\n\r]*".join(re.escape(ch) for ch in ns))
        _LB_RE_CACHE[ns] = rx
    return rx


def find_span(hay, needle):
    """返回 (start,end) 首个匹配 span。依次：精确 -> 容忍换行 -> 容忍空白/全角半角。
    容错匹配对 hay/needle 做「去空白 + 全角转半角」归一化后定位，再映射回原文位置，
    故原文本里的全角空格、半角/全角句号差异等 OCR 常见噪声不再导致补丁失配。
    （2026-09-08 R6 提速：匹配语义与提速前完全一致；第 2 级 C 级正则等价替代原
      O(n·m) 逐字符双循环，第 3 级归一化改 _norm_fast，位置映射仅在命中后执行。）
    """
    if not needle:
        return None
    # 1) 精确
    i = hay.find(needle)
    if i >= 0:
        return i, i + len(needle)
    # 2) 容忍换行（\n\r 在 hay/needle 中可有可无；正则逐字符 re.escape，无特殊语义）
    ns = needle.replace("\n", "").replace("\r", "")
    if ns:
        m = _lb_re(ns).search(hay)
        if m:
            return m.start(), m.end()
    # 3) 容忍空白/全角：归一化后匹配，再映射回原位置
    nh = _norm_fast(hay)
    nn = _norm_fast(needle)
    if not nn:
        return None
    p = nh.find(nn)
    if p < 0:
        return None
    keep = [oi for oi, ch in enumerate(hay) if ch not in _DEL_SET]  # 位置映射：第 i 个未删字符的原下标
    start = keep[p]
    end = keep[p + len(nn) - 1] + 1
    return start, end


def iter_spans(body, needle):
    """容忍匹配取 needle 在 body 中的**全部** span（与 find_span 同一匹配语义）。

    补丁施加的统一入口：systematic 通改、存疑标记、per-phys 逐处纠错都走这里，
    保证「同一错形在全书/全页各处一律按同一判据命中」，不再出现某一路径用裸
    str.replace（只在逐字相同时命中）而静默漏掉 OCR 硬换行切断的词
    （如「阗\\n闾」；2026-09-22 实测 345 条通改中 18 条各漏 1 处）。

    返回按位置升序的 (start, end) 列表；**调用方必须反向施加**，否则前段插入/替换
    会移动后段坐标。"""
    spans, pos = [], 0
    while True:
        sp = find_span(body[pos:], needle)
        if sp is None:
            return spans
        s, e = sp
        spans.append((pos + s, pos + e))
        pos += e


def compile_linebreak(old):
    """为 systematic 通改预编译「仅容忍换行」正则：old 各字之间允许插入任意 \\n/\\r。

    通改是**全局声明式改写**（作者按原文所见形态书写），故只容忍 OCR 硬换行这一种
    噪声；不并入 find_span 的"去空白/全角折半"归一——那会让「”,」与「”，」这类
    全半角规则互相命中，命中判定失去意义、残留自检也无从判定。
    预编译后逐页 search，避免 345 条 × N 页重复构造正则。"""
    return _lb_re(old.replace("\n", "").replace("\r", ""))


def same_text(a, b):
    """两串在「仅去空白」意义下是否为同一段文字。

    用于识别"只归并 OCR 硬换行、不改变任何字"的补丁（如「伍\\n子胥」→「伍子胥」）。
    这类补丁**该施加**（被换行切断的词在 03 里连续可搜，03 是跨书检索保底层），
    但**不该插留痕**——留痕记的是「原书作 X」（§3.1），X 与 Y 逐字相同即为空壳
    （读数=改值），留在正文里只增噪声。（2026-09-22 实测 phys312 的「伍\\n子胥」三处）
    """
    return re.sub(r"[\s]+", "", a) == re.sub(r"[\s]+", "", b)


def miss_alarm(miss_count, threshold, log_rel="correction_miss_log.md"):
    """补丁未命中两级告警（B2，PDF/EPUB 共用）。

    返回 (warning_md, should_fail)：
    - miss_count > threshold 时，warning_md 是一段 ⚠️ 醒目块（插入 00 设闸审计报告、提示主控方必须处理）；
      should_fail=True（调用方应在写完所有交付件后 sys.exit(1)，除非 --no-fail 兜底）。
    - 否则返回空串 + False（静默通过）。

    设计：只做"软提示 + 非零退出"两件事，本身不写文件；落盘与退出由调用方统一负责，
    故 PDF 与 EPUB 两条路径行为一致、无漂移。
    """
    if miss_count > threshold:
        block = (
            f"\n> ⚠️ **补丁未命中 {miss_count} 处（>{threshold} 阈值），主控方必须处理！**\n"
            f"> 未命中的补丁未并入 03，引用前请先核对 `corrections/*.json` 与 `{log_rel}`；"
            f"修订补丁后重跑 assemble（幂等）即自动并入 03。\n"
        )
        return block, True
    return "", False


def classify_miss(miss_list):
    """P2 新-1：把 correction_miss_log 条目分四类，仅 hard 计入失败阈值（n_miss）。

    背景：旧 n_miss = 全部 miss 去掉「回退成功」，把良性类（无效 systematic 通改、
    冗余 per-phys、护栏拦截、重叠）一并计入失败阈值，导致本书 0 命中的无效通改 /
    已被通改代劳的冗余补丁也能把 assemble 顶到 rc=1，反而掩盖「全文未找到」这类真漏改。

    分类：
    - hard（真实内容风险 / 脏数据，应阻断）：'全文未找到' / 'phys N 不在全文中' /
      '脏值'(phys 脏值) / '正则非法'(systematic 正则坏) / '系统替换残留'(通改后仍命中=真残留)。
    - invalid_sys（无效 systematic 通改，本书 0 命中，建议清理 manifest，不阻断）：
      '系统替换未命中'。
    - redundant（冗余 per-phys，本书已正确，建议置 deleted，不阻断）：
      'old 全文未命中但 new 已存在'。
    - benign（护栏 / 重叠 / 回退成功，不计）：'systematic 通改被护栏拦截' /
      'old 过短' / '与更长补丁重叠' / '回退成功'。
    未知类别保守计 hard。
    """
    hard = redundant = invalid_sys = 0
    for m in miss_list:
        if "回退成功" in m:
            continue
        if "系统替换未命中" in m:
            invalid_sys += 1
        elif "old 全文未命中但 new 已存在" in m:
            redundant += 1
        elif ("全文未找到" in m or "不在全文中" in m or "脏值" in m
              or "正则非法" in m or "系统替换残留" in m):
            hard += 1
        elif ("old 过短" in m or "与更长补丁重叠" in m
              or "通改被护栏拦截" in m):
            continue
        else:
            hard += 1
    return hard, redundant, invalid_sys


# ============================================================
# B2 · clean_note（笔记清洗）—— 单一真相源
# ============================================================
# 2026-09-05 抽至此：此前 `clean_note` 在 pipeline_assemble.py 与 assemble_epub.py
# 各存一份**逐字相同**的副本（AST 比对相似度 1.000 / 19 行），属典型的双路径漂移源。
#
# 两份副本的差异（lite_assemble.py 的增强版，即"Bug10 修复"）已做成显式开关，
# 默认关闭 → PDF / EPUB 两条路径行为与抽取前**逐字节一致**（由基线对照单测锁死）：
#   strip_code_fences : 剥除 ```candidate``` / ```json``` 抽取块
#   strip_note_tails  : 剥除 `## 候选错处(candidate)` / `## 速查概念（…）` / 孤立 `summary:` 行
#

_CANDIDATE_FENCE_RE = re.compile(r"```(?:candidate|json)\s*\n.*?```", re.S)
_NOTE_TAIL_RES = (
    re.compile(r"\n?##\s*候选错处\s*\(?candidate\)?\s*\n+(?:\s*\n)*"),
    re.compile(r"\n?##\s*速查概念[^\n]*\n+[\s\S]*?(?=\n##|\Z)"),
    re.compile(r"^\s*summary:\s*.+\n?", re.M),
)


def clean_note(txt, fname=None, strip_code_fences=False, strip_note_tails=False):
    """笔记清洗：统一 `## N.` 标题层级，去首部体例说明与空行。

    strip_code_fences / strip_note_tails 见上方说明；默认全部关闭 = PDF/EPUB 既有行为。
    """
    if strip_code_fences:
        txt = _CANDIDATE_FENCE_RE.sub("", txt)
    if strip_note_tails:
        for _re in _NOTE_TAIL_RES:
            txt = _re.sub("", txt)
    lines = txt.splitlines()
    out, i = [], 0
    if lines and re.match(r"^#\s+\d+\.\s", lines[0]):
        num = re.match(r"^#\s+(\d+)\.", lines[0]).group(1)
        if any(re.match(r"^##\s+" + num + r"\.\s", l) for l in lines[1:]):
            i = 1
        else:
            lines[0] = "## " + lines[0][2:].strip(); i = 0
    elif lines and re.match(r"^#\s", lines[0]):
        i = 1
    while i < len(lines) and lines[i].lstrip().startswith(">"): i += 1
    while i < len(lines) and not lines[i].strip(): i += 1
    body = lines[i:]
    for l in body:
        m = re.match(r"^#\s+(\d+)\.\s", l)
        if m: l = "## " + l[2:].strip()
        out.append(l)
    return "\n".join(out)


# ============================================================
# B3 · strip_leaked_headers（漏剥页眉清理）—— 单一真相源
# ============================================================
# 2026-09-05 抽至此：此前 pipeline_assemble.py 与 assemble_epub.py 各存一份
# （相似度 0.906，仅差锚正则是否含 `book=`）。锚正则参数化后两路径共用一份。

_HDR_RE = re.compile(r"^\s*第[一二三四五六七八九十百千0-9]+[章节目编部卷篇]")

ANCHOR_RE_EPUB = re.compile(r"<<<PAGE phys=\d+ book=[^>]*>>>\n(.*?)(?=\n<<<PAGE|\Z)", re.S)


def strip_leaked_headers(fulltext, anchor_re=None):
    """对每页：若首行命中『第X章/节/编…』模式则整行剥除（运行书眉，非正文）。

    anchor_re 缺省用 PDF/EPUB 的 `phys=\\d+ book=...` 锚；调用方若用无 `book` 字段的
    裸 `phys=\\d+` 锚，显式传入。返回 (文本, 剥除数)。

    两条护栏（2026-09-08 R1 修复）：
      1. 锚块之间的文本与首锚前/末锚后的残余一律拼回——此前 finditer 只拼接锚块，
         首锚之前的内容、任何不被锚正则匹配的文本会被静默丢弃（数据丢失）。
      2. 频率判定：同一首行在全文锚块中出现 ≥2 次才剥——运行书眉逐页重复，
         章首真标题（如「第一章 绪论」）只出现一次，必须保留。
    """
    from collections import Counter
    rx = anchor_re or ANCHOR_RE_EPUB
    matches = list(rx.finditer(fulltext))
    first_cnt = Counter()
    for m in matches:
        lines = m.group(1).split("\n")
        if lines:
            first_cnt[lines[0]] += 1
    seen_first = set()
    out = []
    n = 0
    last_end = 0
    for m in matches:
        if m.start() > last_end:
            out.append(fulltext[last_end:m.start()])  # 锚间未匹配文本，不得丢弃
        head = m.group(0).split("\n", 1)[0]
        body = m.group(1)
        lines = body.split("\n")
        if lines and _HDR_RE.match(lines[0]) and first_cnt[lines[0]] >= 2:
            if lines[0] in seen_first:
                # 该首行已出现过且全文高频 → 运行书眉，剥除
                n += 1
                lines = lines[1:]
                body = "\n".join(lines)
            else:
                # 首次出现保留：章首真标题必先于其同文本书眉出现（宁留勿删）
                seen_first.add(lines[0])
        out.append(head + "\n" + body)
        last_end = m.end()
    if last_end < len(fulltext):
        out.append(fulltext[last_end:])  # 末锚之后的残余文本
    return "\n".join(out), n


# ============================================================
# R5 · apply_corrections 核心（补丁施加）—— 单一真相源（2026-09-08 第 3 批）
# ============================================================
# 此前 pipeline_assemble.py（PDF）与 assemble_epub.py（EPUB）各存一份 ~130 行近似重复
# 实现，仅在块结构（b[2] vs b[3]）与返回元组形状上有差——最核心、最易出 bug 的逻辑反而
# 没进单一真相源，违背本文件头「勿双路径漂移」的自家纪律（审查报告 R5）。
#
# 分工：
#   - 本核心：补丁加载（BOM/JSONL 容错 + 损坏即整体终止）、systematic 冲突检测与通改、
#     🟡10 单字通改护栏、逐页 per-phys 施加（C2 同页重叠消解 + 全局回退留痕）、跨页合并。
#     就地修改 blocks[i][3]（body）。
#   - 各脚本薄壳 apply_corrections：锚解析（正则不同：PDF book=任意串无 title、EPUB
#     PAGE/SEC 双锚 title 可选）+ 回写序列化 + 保持各自历史返回元组（黄金值契约）。

import json as _json
import os as _os
import sys as _sys

_TRACE_DEL = f"〔原书OCR衍\"{{old}}\"，删〕"      # R3 规范文案（删除型），f-string 用 .format 展开
# 替换型（2026-09-26 随 §3.1 改号）：记号由「原书OCR作"X"，据义应为"Y"」改为「原书作「X」」——
# 「原书」与「OCR」是两个不同的层（§3.1），留在正文的记号指向底本层，故不写「OCR」；
# 改后的字由正文承载，记号只记「原书作 X」，两者合起来即完整的改前/改后。
# 存量件仍是旧式记号，一切剥除/计数必须兼容两式（见 count_traces / assemble 的 TRACE_RE）。
_TRACE_SUB = f"〔原书作「{{old}}」〕"

# 留痕定界符转写表（2026-09-22 立）。留痕的定界符就是 `〔…〕`，而它引述的原文字串**本身可能
# 含 `〔`/`〕`**——古籍题注（`〔汉〕班固`、`〔晋〕常璩`）最常见。两者同形时，下游任何
# `〔[^〕]*〕` 式剥除（canon 比对 / C1 留痕计数 / 引文逐字率核验）会**匹配到内层、只剥内层、
# 留下外层残壳**。故生成留痕时把引述文本里的 `〔`/`〕` 转写为 `⟦`/`⟧`（Unicode 数学括号，
# 形近、不与本技能任何标记冲突），使留痕定界唯一、下游可正确剥除。
_TRACE_LB_TRANS = str.maketrans({"〔": "⟦", "〕": "⟧"})


def esc_trace_text(s):
    """转写留痕引述文本里的 `〔`/`〕` → `⟦`/`⟧`（防与留痕定界符同形，见上方说明）。"""
    return s.translate(_TRACE_LB_TRANS)


def trace_balance(fulltext):
    """留痕定界自检（软告警，不阻断）。返回 `(n_nested, n_unbalanced, samples)`。

    - **n_nested**：某处 `〔` 与其**最近** `〕` 之间还夹着另一个 `〔` —— 定界被内层括号破坏，
      即引述文本里的括号未转写（本应经 `esc_trace_text` 处理）。下游 `〔[^〕]*〕` 在此处必剥错。
    - **n_unbalanced**：全文 `〔` 与 `〕` 计数不等（留痕与正文夹注各自应成对；不等须查明来路）。
    - **samples**：前 5 处嵌套样例（截取 ±20 字，换行以 `⏎` 显示），供人工定位。

    正文夹注 `〔夹注〕` 成对出现、内层无 `〔`，不触发误报；古籍夹注本身嵌套（`〔甲〔乙〕〕`）
    极罕见，若出现按样例人工确认即可。
    """
    n_nested, samples = 0, []
    for m in re.finditer("〔", fulltext):
        s = m.start()
        e = fulltext.find("〕", s + 1)
        if e < 0:
            continue
        if "〔" in fulltext[s + 1:e]:
            n_nested += 1
            if len(samples) < 5:
                samples.append(fulltext[max(0, s - 20):e + 20].replace("\n", "⏎"))
    n_unbalanced = len(re.findall("〔", fulltext)) - len(re.findall("〕", fulltext))
    return n_nested, n_unbalanced, samples


def count_traces(fulltext):
    """C1 审计单一真相源：统计 03 全文中的留痕处数（P0 #1 / #24 横切收敛，2026-09-12）。

    三族留痕全部计入：替换型〔原书作…〕、删除型〔原书OCR衍…〕、跨页合并〔跨页合并…〕。
    **替换型兼容新旧两式记号**（2026-09-26 §3.1 改号）：现行写法是「原书作「X」」，
    存量交付件里仍是旧式「原书OCR作"X"，据义应为"Y"」；两式互不重叠（「原书OCR作」不含
    「原书作」），故分别计数相加——只认新式会让存量件上的留痕全部漏计，C1 误报静默改动。
    此前 pipeline_assemble.py:173 与 assemble_epub.py:197 各内联一份只数「原书OCR作 + 跨页合并」
    的公式，漏掉删除型一族——于是每处**合法的** OCR 衍字删除（SKILL.md §3 明文支持的做法）
    都会被 C1 误报「疑似未留痕的静默改动」（假阳）。抽到此处后，两条路径与 golden 共用同一口径。
    """
    return (fulltext.count("原书OCR作")
            + fulltext.count("原书作")
            + fulltext.count("原书OCR衍")
            + fulltext.count("〔跨页合并"))


# ---------- clean-03：剥纠错留痕（v1.14.0 单一真相源）----------
# 03 干净正文 / verifier 残留回放 / 05 台账 共用。只剥四类「纠错留痕」，绝不剥 legit 〔汉〕班固 / 〔注〕。
_TRACE_STRIP_RE = re.compile(r"〔原书(?:OCR)?作[^〕]*〕|〔原书OCR衍[^〕]*〕|〔跨页合并[^〕]*〕|〔存疑：[^〕]*〕")
_TONGGAI_LINE_RE = re.compile(r"^\s*(?:##\s*通改清单|>\s*\*\*通改清单)")


def _page_blocks(text):
    """按 <<<PAGE phys=N ...>>> 锚把正文切成 phys(int) -> 页块文本。无锚/锚异常返回空 dict。"""
    blocks = {}
    pat = re.compile(r"<<<PAGE\s+phys=(\d+)[^>]*>>>")
    ms = list(pat.finditer(text))
    for i, m in enumerate(ms):
        try:
            ph = int(m.group(1))
        except ValueError:
            continue
        start = m.end()
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        blocks[ph] = text[start:end]
    return blocks


# 形近字裸串集合（防纠错造错）：单字 old 落在此集且补丁无 context 字段 → 标注"无上下文形近互换"供人工复核。
# 两路径（OCR/EPUB）共用，单一真相源。
_FORM_NEAR_SET = set(
    "己已曰日刺剌人入末未木术瓜爪祗祇戊戌戍肓盲享亨叚假赢嬴拆折准淮崇祟瞻赡"
    "坼柝汨汩菅管刺剌孛悖"
)


def is_residual_full(clean_text, old, new=""):
    """N5（2026-10-04 审查）：残留判定的唯一权威口径（全文）。

    增字型补丁先剔 new 再判 old；systematic/全局补丁（无具体 phys）也查全文档。
    emit_apply_report（apply 报告）与 pre_delivery_verifier（闸2）共用，杜绝口径分叉
    （旧版 apply_report 只查"声明 phys 页块"，phys 错位时误判"已应用"，真残留被洗成通过）。"""
    if new and old != new:
        return old in clean_text.replace(new, "\x00")
    return old in clean_text


def emit_apply_report(work, out, meta_path, pending):
    """组装应用报告：遍历 active 补丁，分类 已应用 / 底本0命中 / 残留，列 存疑待核，标注形近裸串。

    禁止静默跳过：未命中/残留/存疑均显式列出明细。写 work/apply_report.md，返回 (summary, bad, residual_n)。

    两路径统一（v1.18.1 改造13）：被 pipeline_assemble（OCR/PDF 路径）与 assemble_epub（EPUB 路径）
    共用，单一真相源。两条路径的 03 都以 `<<<PAGE phys=N ...>>>` 锚切页（EPUB 的 phys＝SEC idx），
    故 `_page_blocks` 页块定域对两路径都生效；增字型补丁先剔 new 再判 old（A+C 修正，与闸2 同口径）
    亦两路径通用。先前 EPUB 版漏这两项修正，导致 EPUB 书整篇扫 old 易把目录页/重复短语合法同串
    误报残留——统一后消除，且日后两条路径不再漂移。"""
    mp = os.path.join(work, "patch_manifest.json")
    active = []
    if os.path.isfile(mp):
        for p in read_json_cn(mp).get("patches", []):
            if p.get("status") == "active" and p.get("old"):
                active.append(p)
    else:
        for fp in sorted(glob.glob(os.path.join(work, "corrections", "*.json"))):
            data = expand_patch_data(read_json_cn(fp))
            for c in data:
                if isinstance(c, dict) and c.get("old") and not c.get("merge"):
                    active.append({"old": c["old"], "new": c.get("new", ""),
                                   "phys": c.get("phys", ""),
                                   "type": "systematic" if c.get("systematic") else "ocr_typo",
                                   "reason": c.get("reason", ""), "source": os.path.basename(fp)})
    cands = sorted(glob.glob(os.path.join(out, "*03_*.md")))
    clean = read_text_cn(cands[0]) if cands else ""
    # v1.18.4（东晋门阀政治数据修复实测发现）：残留判定前剥纠错留痕——替换型〔原书作「old」〕/
    # 删除型〔原书OCR衍"old"〕记号内**合法保留 old**，不剥则台账已生效的补丁被误报残留
    # （闸2 verifier 早已剥留痕，apply_report 漏了这步 → "apply_report 报 4 残留 / 闸2 PASS" 打架）。
    # 剥后判定与闸2 完全同口径；struct（改前底本）本就无留痕，不剥。
    clean_check = strip_correction_traces(clean) if clean else clean
    struct_p = os.path.join(work, "fulltext_struct.txt")
    struct = read_text_cn(struct_p) if os.path.isfile(struct_p) else ""
    applied, zero_hit, residual, formnear = [], [], [], []
    blocks = _page_blocks(clean_check)
    for p in active:
        old = p["old"]
        if not old:
            continue
        in_struct = struct.count(old) > 0
        new = p.get("new", "")
        phys = p.get("phys")
        # 逐页补丁声明页块（仅诊断用）：phys 越界时回退全文避免 NoneType.replace 崩溃。
        scope = blocks.get(int(phys)) if (phys not in (None, "") and str(phys).isdigit()) else clean_check
        if scope is None:
            scope = clean_check  # phys 越界时回退全文，避免 NoneType.replace 崩溃
        # N5（2026-10-04 审查）：res（权威残留）统一走全文口径 is_residual_full，与闸2 单一实现，
        # 杜绝"apply_report 无残留 / 闸2 拒交付"打架。旧版只查声明页块（scope），phys 错位时漏报真残留。
        # res_page 仅诊断：old 是否同时落在声明页内（区分"声明页内残留"与"phys错位·全文仍见"）。
        # v1.18.4：判定基准统一用剥留痕后的 clean_check（留痕内 old 合法保留，不算残留）。
        res = is_residual_full(clean_check, old, new)
        if new and old != new:
            res_page = old in scope.replace(new, "\x00")
        else:
            res_page = old in scope
        if res:
            _qual = "【phys错位·全文仍见，真残留】" if not res_page else "【声明页内残留】"
            residual.append(f"{old}→{p.get('new','')}（来源 {p.get('source','')}，phys {p.get('phys','')}）{_qual}")
        elif in_struct:
            applied.append(f"{old}→{p.get('new','')}（phys {p.get('phys','')}）")
        else:
            zero_hit.append(f"{old}→{p.get('new','')}（来源 {p.get('source','')}，底本0命中，疑似补丁/底本错位）")
        if len(old.strip()) == 1 and old.strip() in _FORM_NEAR_SET and not p.get("context"):
            formnear.append(f"{old}→{p.get('new','')}（phys {p.get('phys','')}，无上下文形近互换，须人工复核）")
    L = ["# 组装应用报告（assemble apply-report · Stage 4「assemble 应用报告规范」）", "",
         f"> 书名：{read_json_cn(meta_path).get('book','') if os.path.isfile(meta_path) else ''}；assemble 后、交付前生成。", "",
         f"- 已应用（active 补丁 old 已不在 03 干净正文）：{len(applied)} 处",
         f"- 底本 0 命中（补丁 old 不在 fulltext_struct，疑似错位）：{len(zero_hit)} 处",
         f"- ⚠️ 残留（old 仍在 03 干净正文，未应用成功）：{len(residual)} 处",
         f"- 存疑待核（补丁冲突，同一原文多候选）：{len(pending)} 处",
         f"- 形近字裸串无上下文（须人工复核，防纠错造错）：{len(formnear)} 处", ""]
    if zero_hit:
        L += ["## 底本 0 命中明细（禁止静默跳过，须人工核实）"] + [f"- {x}" for x in zero_hit] + [""]
    if residual:
        L += ["## 残留明细（须补充纠错 + 重跑 assemble，闸2 会拒交付）"] + [f"- {x}" for x in residual] + [""]
    if pending:
        L += ["## 存疑待核明细（补丁冲突，须人工选边）"] + [f"- 原文「{o}」候选：{c}" for o, c in pending] + [""]
    if formnear:
        L += ["## 形近字裸串无上下文明细（防纠错造错，须人工复核）"] + [f"- {x}" for x in formnear] + [""]
    safe_write(os.path.join(work, "apply_report.md"), "\n".join(L))
    summary = (f"已应用 {len(applied)} / 底本0命中 {len(zero_hit)} / 残留 {len(residual)}"
               f" / 存疑 {len(pending)} / 形近裸串 {len(formnear)}（详见 work/apply_report.md）")
    return summary, (len(residual) > 0 or len(zero_hit) > 0), len(residual)


def strip_correction_traces(text, *, strip_tonggai_lines=False):
    """剥除 03 里的纠错留痕记号，得干净可引用正文（v1.14.0：03 不留〔〕留痕，改错记录见 04_改错留痕.md）。

    traces=True（默认）：剥四类纠错留痕（替换型〔原书作「X」〕/ 删除型〔原书OCR衍…〕/
    跨页合并〔跨页合并…〕/ 存疑〔存疑：…〕）。
    strip_tonggai_lines=True：额外剔除头部「通改清单」**整块**（标题行 + 所有 `> - 「X」→「Y」`
    内容行，兼容存量旧 03）。v1.14.0 P0-2 修复：原行级正则只删标题行、内容行中的 old 被
    verifier 误报为残留；改为块级删除——从标题行起连续删 `> ` 前缀行，遇非 `> ` 行或空行止。
    与 pre_delivery_verifier.clean_03_body 共用本函数，杜绝多处漂移。"""
    text = _TRACE_STRIP_RE.sub("", text)
    if strip_tonggai_lines:
        lines = text.split("\n")
        out = []
        in_block = False
        for line in lines:
            if _TONGGAI_LINE_RE.match(line):
                in_block = True
                continue  # 跳过通改清单标题行
            if in_block:
                stripped = line.lstrip()
                # 通改清单内容行以 > 开头（含 > - 「...」→... 和空的 > 行）；
                # 遇非 > 行或空行则块结束，该行保留。
                if stripped and not stripped.startswith(">"):
                    in_block = False
                    out.append(line)
                # 否则跳过（内容行 / 块内空行）
                continue
            out.append(line)
        text = "\n".join(out)
    return text


_BACKUP_ROOT = None


def set_backup_root(path):
    """设定备份根目录（两条 assemble 路径启动时设为该书的 work 目录）。

    备份落点必须与交付目录分离：交付目录只应含六件（00–05），`.bak` 与六件并列
    会让「六件齐出」无法一眼核验，且幂等重跑会持续堆积旧件。未设定时退化为
    目标同级的 `_bak/` 子目录——同样不与交付件并列。
    """
    global _BACKUP_ROOT
    _BACKUP_ROOT = path


def backup_target(path, suffix="bak"):
    """计算备份落点并按需建目录：优先 `_BACKUP_ROOT`，否则目标同级 `_bak/` 子目录。"""
    import time

    ts = time.strftime("%Y%m%d-%H%M%S")
    base = _os.path.basename(path)
    d = _BACKUP_ROOT if _BACKUP_ROOT else _os.path.join(_os.path.dirname(path) or ".", "_bak")
    _os.makedirs(d, exist_ok=True)
    cand = _os.path.join(d, "%s.%s.%s" % (base, ts, suffix))
    i = 1
    while _os.path.exists(cand):
        cand = _os.path.join(d, "%s.%s.%s.%d" % (base, ts, suffix, i))
        i += 1
    return cand


def safe_write(path, text, *, backup=True, encoding="utf-8"):
    """交付件落盘单一入口：覆盖保护 + 原子写。

    原写法 `open(path, "w", newline="\n").write(text)` 有两个问题：
      ① --out 写错或复用目录 → 六件套被**静默覆盖**，无确认、无备份（OneDrive 同步后双机扩散）；
      ② 非原子 —— 写一半崩溃或 OneDrive 抢同步会留下半截文件。
    现行为：
      - 目标已存在**且内容不同**才备份（幂等重跑内容相同 → 不产生垃圾备份）；
      - 备份落点由 `backup_target()` 决定，**绝不落在交付件同级目录**；
      - 先写同目录 .tmp 再 os.replace 原子替换。
    """
    d = _os.path.dirname(path)
    if d:
        _os.makedirs(d, exist_ok=True)
    if backup and _os.path.exists(path):
        try:
            with open(path, encoding=encoding) as f:
                old = f.read()
        except Exception:
            old = None
        if old is not None and old != text:
            import shutil as _shutil

            bak = backup_target(path)
            _shutil.move(path, bak)
            print("  [覆盖保护] %s 已存在且内容不同 → 旧件备份至 %s" % (_os.path.basename(path), bak))
    tmp = f"{path}.tmp"
    # newline="\n"：**禁止平台自动翻译**。Windows 下 newline=None 会把 \n 写成 \r\n，
    # 这是交付件 CRLF 的系统性来源。全库交付文本统一 LF。
    with open(tmp, "w", encoding=encoding, newline="\n") as f:
        f.write(text)
    _os.replace(tmp, path)


def read_text_cn(path, *, encoding="utf-8-sig"):
    """文本读取单一入口：统一 `utf-8-sig` 容错（#24 横切收敛，2026-09-13）。

    🟡8 的 `utf-8-sig` 当初只修了「两个 assemble 的 struct 读取 + _common 的 corrections」，
    漏掉 3 处：meta 读取、raw/page_index 读取、pipeline_split 的 struct 读取——
    用户用 Excel/记事本另存过的 meta 带 BOM 时，`json.load` 或锚解析照样炸。
    统一走本函数后，新增读取点不会再漏（纪律：本技能内读文本一律用它，不要裸 open）。
    """
    with open(path, encoding=encoding) as f:
        return f.read()


def read_json_cn(path, *, encoding="utf-8-sig"):
    """JSON 读取单一入口（#24 横切收敛）：与 read_text_cn 同一套 BOM 容错口径。"""
    import json as _j
    with open(path, encoding=encoding) as f:
        return _j.load(f)


def esc_cell(v):
    """表格单元格转义单一入口（#24 横切收敛，2026-09-13）。

    原 `_esc_cell` 只在 `pipeline_assemble.py:115` 存在，`assemble_epub` 的**全部
    00/02 表格裸插值**——章名/候选串里含 `|` 会直接撑破 Markdown 表格列为多列，
    含换行会把表格截断，且两条路径产物格式漂移（#24「部分铺开」实例之一）。
    相对原 `_esc_cell` 增加换行归整（换行同样是撑破表格的直接原因）。
    """
    return (str(v).replace("|", "\\|")
            .replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>"))


def check_anchors(struct, fulltext_clean, *, exit_code=2):
    """R4 锚点硬闸单一入口（#24 横切收敛，2026-09-13）。

    原实现只在 `assemble_epub.py:189-194`（EPUB 侧）有，PDF 侧**完全缺失**——
    struct 为空 / 锚格式异常 / 误喂他路径产物时，PDF 路径照常产出空或半截的 03，
    且**无任何告警**（§6.1 列明的失败模式在 PDF 路径静默发生）。

    在写任何交付件之前调用；异常时 `SystemExit(exit_code)`，调用方无需再判。
    返回 `(struct_anchors, anchor)` 供调用方做统计与报告。
    """
    struct_anchors = len(re.findall(r"<<<(?:PAGE|SEC)", struct))
    anchor = fulltext_clean.count("<<<PAGE")
    if anchor == 0:
        print("❌ 致命：fulltext_clean 中锚点数为 0 —— fulltext_struct.txt 可能不含本脚本可识别的锚行"
              "（struct 为空？锚格式异常？误把其他路径产物喂给了本脚本？）。终止，不产出任何交付件。")
        raise SystemExit(exit_code)
    if anchor < struct_anchors:
        # 未被 ANCHOR_RE 识别的锚，其页块正文**根本不会拼进 03**——不是"补丁施不上"，
        # 而是整页内容静默消失，且下游无任何校验会发现（闸2 只回放台账、闸1b 只查已进 03 的文本）。
        # 语义上比错字残留更严重，故必须硬失败，不能只告警。
        print(f"❌ 锚点数不一致：struct 含锚 {struct_anchors} 个，03 仅解析出 {anchor} 个"
              f"——{struct_anchors - anchor} 个锚不被 ANCHOR_RE 识别（常见于缺 title= 字段或锚行被折行）。"
              f"这些页的正文不会进入 03（整页静默丢失），终止，不产出任何交付件。")
        print("   处理：检查 fulltext_struct.txt 的锚格式是否统一为 <<<PAGE phys=N book=M>>>（EPUB 含 title=）。")
        raise SystemExit(exit_code)
    return struct_anchors, anchor


def expand_patch_data(data):
    """corrections 文件顶层形态归一——**加载口径单一真相源**。

    三种形态：① `{"patches":[...]}` 包裹层；② 顶层 list；③ 顶层单条 dict。
    各处**不得**再自行写 `data.get("patches", [])`：那会漏掉形态③，形成
    「施加侧真的改了 03，而 patch_manifest / 04_改错留痕 / verifier / apply_report
    四处整份丢弃」的假通过链——改了正文却无任何台账记录、闸2 也不查它。
    """
    if isinstance(data, dict):
        if isinstance(data.get("patches"), list):
            return data["patches"]
        return [data]
    if isinstance(data, list):
        return data
    return []


def check_prereq_files(work, files, exit_code=1):
    """前置文件存在性硬校验——**在写任何交付件之前**调用。

    放在落盘之后等于只报警不拦，退出时交付目录已躺着半成品（"不构成有效交付"
    只能靠一句话声明，下游无从判断）。缺前置即终止，不产出任何交付件。
    """
    miss = [f for f in files if not _os.path.isfile(_os.path.join(work, f))]
    if miss:
        print("❌ [前置缺失] work 目录缺少：" + "、".join(miss))
        print("   这些文件由前置脚本产出；前置不全不放行，本次不产出任何交付件。")
        print("   处理：补跑前置脚本生成后重跑 assemble。")
        raise SystemExit(exit_code)


def _load_corr_entries(corr_files, work_dir=None):
    """加载 corrections/*.json → 产出 (条目dict, 来源文件名)。
    若 work_dir 给出且 patch_manifest.json 存在，则过滤 status=deleted 的补丁（v1.14.0 P0-1）：
    deleted 补丁不物理删 corrections/*.json，但 assemble 不施加，verifier 只查 active。
    容忍 BOM（🟡8）；JSONL 回退带二次保护——损坏文件在写任何交付件前整体终止（设闸纪律）。"""
    deleted_ids = set()
    if work_dir:
        mp = _os.path.join(work_dir, "patch_manifest.json")
        if _os.path.isfile(mp):
            try:
                manifest = _json.load(open(mp, encoding="utf-8-sig"))
                for p in manifest.get("patches", []):
                    if p.get("status") == "deleted":
                        deleted_ids.add(p.get("id", ""))
            except Exception as e:
                # N4（2026-10-04 审查）：manifest 损坏不得静默退化——否则只在 manifest 里标
                # deleted 的补丁会被重新施加；verifier 无 manifest 时同样退化，兜不住。
                # 主删除机制（单条 status=deleted，见下）不依赖 manifest，故此处不硬退，
                # 仅强告警并继续（退化加载全部，但 deleted tombstone 仍由单条 status 过滤）。
                _sys.stderr.write(
                    f"⚠️ patch_manifest.json 解析失败（{mp}）：{e}\n"
                    f"   deleted 过滤将依赖单条 status=deleted（仍生效）；仅 manifest 级 deleted 失效、"
                    f"已核实删除的补丁可能被重新施加，请修复该文件或重跑 gen_patch_manifest.py。\n")
    for cf in corr_files:
        try:
            data = _json.load(open(cf, encoding="utf-8-sig"))
        except _json.JSONDecodeError:
            try:
                data = [_json.loads(l) for l in open(cf, encoding="utf-8-sig") if l.strip()]
            except Exception as e:
                _sys.exit(f"❌ 补丁文件解析失败：{cf}（既非合法 JSON 也非合法 JSONL）：{e}\n"
                          f"   请修复或移除该文件后重跑 assemble（幂等）。")
        # 统一走 expand_patch_data：包裹层 / 顶层 list / 单条 dict 三种形态同口径，
        # 且下标须在展开后取，否则与 gen_patch_manifest 的稳定 id 错位、deleted 过滤失效。
        data = expand_patch_data(data)
        base = _os.path.basename(cf)
        for i, c in enumerate(data):
            if isinstance(c, dict):
                # P0 改造4（v1.17.0）：status=deleted 无条件过滤，脱离 manifest 依赖。
                # 原实现仅在「有 manifest 且 id 命中 deleted_ids」时跳过，无 manifest 时
                # 标记 deleted 的补丁会被当 active 施加（本轮中层 9 条问题补丁即此 bug）。
                # 现：单条 explicit deleted 一律不施加；manifest 的 deleted_ids 作为二级兜底。
                if c.get("status") == "deleted":
                    continue
                pid = f"{base}#{i}"
                if pid in deleted_ids:
                    continue  # v1.14.0：跳过已核实删除的补丁（不物理删 corrections，只不施加）
                yield c, base


def scan_confusion_dict(clean_text, book_type=""):
    """P0 改造1：OCR 混淆词典内容正确性扫描（独立基准，不依赖台账）。

    返回 content_residual 列表（格式见 ocr_confusion_dict.scan）。
    compound 直接 count（安全）；char_class 仅本书型命中、用 ctx 正则扫描（禁裸计数）。
    词典模块缺失时返回空（不阻断流程）。"""
    try:
        from ocr_confusion_dict import scan as _scan
        return _scan(clean_text, book_type)
    except Exception as e:
        # N10（2026-10-04 审查）：内容正确性扫描异常不得静默吞（返回 [] 会被调用方当"扫描通过"）。
        # 返回 None 以示"未执行"，调用方须据此判未通过，与"内容正确性无逃生口"一致。
        _sys.stderr.write(f"⚠️ 混淆词典内容扫描未执行（{e}）——内容正确性本次缺失，不得据此判 PASS。\n")
        return None


def collect_systematic_olds(work_dir):
    """P1 改造7：收集本书 active 的 systematic 补丁 old 集合（含 manifest 记 type="systematic"）。

    用于闸1b 裸单字告警：判断 error_forms.txt 里的裸单字条目是否被台账 systematic 补丁覆盖
    （覆盖则安全，未覆盖则闸1b 不查裸单字、闸2 仅查台账补丁，存在漏检风险）。
    加载口径与 _load_corr_entries 一致（manifest 优先；deleted 过滤）。"""
    olds = set()
    mp = _os.path.join(work_dir, "patch_manifest.json")
    if _os.path.isfile(mp):
        try:
            for p in read_json_cn(mp).get("patches", []):
                if p.get("status") == "active" and p.get("old"):
                    if bool(p.get("systematic")) or p.get("type") == "systematic":
                        olds.add(p["old"])
        except Exception:
            pass
    for cf in sorted(glob.glob(_os.path.join(work_dir, "corrections", "*.json"))):
        try:
            data = read_json_cn(cf)
        except Exception:
            continue
        if isinstance(data, dict):
            data = data.get("patches", []) if isinstance(data.get("patches"), list) else [data]
        if not isinstance(data, list):
            continue
        for c in data:
            if isinstance(c, dict) and c.get("old") and not c.get("merge"):
                if c.get("status") == "deleted":
                    continue
                if bool(c.get("systematic")) or str(c.get("phys", "")).upper() == "ALL":
                    olds.add(c["old"])
    return olds


def build_gate1_terms(meta, struct, book_type=""):
    """P1 改造8：构造闸1 检索词（章名片段 + book_meta.proper_names + 词典正确形态）。

    返回去重后的检索词 list（章名抽 ≥4 字片段；proper_names 原样；词典正确形态仅取在
    struct（底本）中出现过的，避免把本书未涉实体强加为可达性断言）。
    闸1 为非阻断告警闸；本函数仅把检索面扩展到「专名/词典」层，弥补 N4「章名错即假 PASS」。"""
    import re as _re
    terms, seen = [], set()
    # ① 章名 ≥4 字片段（与 pipeline_assemble.auto_retrieval_terms 同口径）
    for ch in meta.get("chapters", []):
        name = ch.get("name", "")
        name = _re.sub(r"[〈〉《》「」『』“”\"'（）()\[\]【】]", " ", name)
        for seg in _re.split(r"[、，。；：\s／/]+", name):
            seg = seg.strip()
            if len(seg) >= 4 and seg not in seen:
                seen.add(seg); terms.append(seg)
    # ② book_meta.proper_names（作者显式给的专名白名单）
    for pn in meta.get("proper_names", []) or []:
        pn = (pn or "").strip()
        if pn and pn not in seen:
            seen.add(pn); terms.append(pn)
    # ③ 词典正确形态（compound right）仅取在 struct 中出现的——该书确实涉及该实体，
    #    才断言其在 03 可达；否则属无关实体，强加会制造假 miss。
    if struct:
        try:
            from ocr_confusion_dict import COMPOUND
            for wrong, right in COMPOUND.items():
                if right and right in struct and right not in seen:
                    seen.add(right); terms.append(right)
        except Exception:
            pass
    return terms


# 🟡NEW（v1.10.4 引入 · v1.10.5 扩右括号集）：内置脚注号形态归一器——根治 systematic 通改按"逐数字"声明、更高编号漏网。
# 匹配任意括号组合的脚注号形态（左∈{[〔［} × 右∈{]〕］}\)）}），统一归一为全角〔N〕；
# 与 clean_sys 同序在逐页补丁前施加，不依赖 agent 逐数字声明，覆盖任意编号。
# v1.10.5 补右括号三态：OCR 会把收尾全角〕误识成 `}` / `）` / `)`（第17册实踩 8 处：〔10}〔1）〔12）〔17）〔54）〔13)〔34)），
#   旧集只收 `]〕］` 故整批漏网，只能靠逐条 systematic 补丁兜底。左括号**故意不扩**半角 `(`：
#   正文半角 `(1)` 编号/区间（如 `[0,1)`）常见，扩左集合会误伤——右扩已覆盖实踩全部形态。
_FOOTNOTE_BRACKET_RX = re.compile(r"[\[〔［](\d+)[\]〕］}\)）]")


def _normalize_footnote_brackets(text):
    """把 [N〕/〔N］/[N]/〔N]（N 为数字串，括号任意混用）归一成全角〔N〕；其它形态不动。"""
    return _FOOTNOTE_BRACKET_RX.sub(
        lambda m: "〔" + m.group(1) + "〕", text)


# ---- corrections 台账缺失的显式感知（2026-10-08 五裁定·分析①）----
CORRECTIONS_EMPTY_NOTE = ("N/A：无 corrections 台账（0 份）——本次未施加任何纠错，03 等于未纠错的 struct；"
                          "如本书确有纠错，请先由分章 Agent 产出 corrections/*.json 后重跑 assemble")


def report_corrections_ledger(corr_files):
    """corrections 台账为空的统一显式感知（PDF / EPUB 两路径同源，2026-10-08 分析①）。

    不阻断（首轮成书尚无纠错记录属正常态），但必须**显式**：打印强告警，并返回 N/A 说明
    供 audit_report 的「补丁施用」字段落痕——否则「真漏台账」与「首轮无台账」在报告里
    不可区分（旧版 PDF 仅一句 WARNING、EPUB 连告警都没有，两路径还不一致）。

    返回 ""（台账非空）或 CORRECTIONS_EMPTY_NOTE（为空，已打印告警）。
    """
    if corr_files:
        return ""
    _sys.stderr.write(
        "⚠️ 无 corrections 台账（corrections/*.json = 0 份）：本次未施加任何纠错，03 将等于未纠错的\n"
        "   结构清洗稿、04_改错留痕 为空。若本书确有纠错，请先由分章 Agent 产出纠错补丁后重跑 assemble。\n")
    return CORRECTIONS_EMPTY_NOTE


def final_error_form_scan(out_dir, ef_items, *, targets=("00", "01", "02", "03")):
    """交付级全件错形复扫（2026-10-05 P0）——六件落盘后扫，残留 >0 即拒交付。

    为什么另设这一道：闸1b 原只扫 03 干净正文。01/02 由 notes 编译、00 由章名与统计
    汇总生成，三者都不在扫描空间里；一旦它们保留了已判定的错形，就形成「错形孤岛」——
    02 的名句在 03 里检索 0 命中，「可检索 / 可回溯」契约在派生件上断裂，而全闸无感。

    扫描范围故意**不含 04**：04 改错留痕按定义要逐条列出 old 错形，扫它必然全量误报。
    03 须先剥留痕与头部通改清单（清单本身记录错形），与闸1b 同口径，否则恒报残留。

    返回 (hit, skipped)：
      hit     = {文件名: [(wrong, right, cnt), ...]}（只含 cnt>0 的项）
      skipped = [(文件名, 原因), ...]——**读不进去的目标件**（IO / 编码异常）。
    调用方须在 skipped 非空时**拒交付**（扫描覆盖不可证，静默跳过等于漏检）；
    ef_items 为空时本函数无从扫描，调用方应在此之前按强制项缺失处理（闸1b 之责）。
    """
    import glob as _g
    hit, skipped = {}, []
    if not ef_items:
        return hit, skipped
    for t in targets:
        fs = [f for f in sorted(_g.glob(_os.path.join(out_dir, "*" + t + "_*.md")))
              if _os.path.isfile(f)]
        for f in fs:
            try:
                txt = read_text_cn(f)
            except Exception as e:
                skipped.append((_os.path.basename(f), f"{type(e).__name__}: {e}"))
                continue
            if t == "03":
                txt = strip_correction_traces(txt, strip_tonggai_lines=True)
            bad = []
            for wrong, right in ef_items:
                if wrong and wrong != right and wrong in txt:
                    bad.append((wrong, right, txt.count(wrong)))
            if bad:
                hit[_os.path.basename(f)] = bad
    return hit, skipped


# ---- 设闸审计报告统一构建（2026-10-08）----
# 背景：PDF 路径（pipeline_assemble）与 EPUB 路径（assemble_epub）的 work/audit_report.md
# 字段集与写法长期不统一（一为 16 字段、一为 7 字段，仅 4 项同名），主控方跨路径读报告
# 需两套解析逻辑。现抽公共 builder：两路径的「00 第五节」与「audit_report.md」共用本函数，
# 字段集与顺序完全一致；某字段本路径无数据时传 None → 渲染 N/A 并注明原因，不留缺口。
AUDIT_FIELD_ORDER = (
    ("gate0_anchor",       "闸0 锚点有效性"),
    ("gate1_retrieval",    "闸1 检索可达性"),
    ("gate1b_clear",       "闸1b 高频错字清零"),
    ("gate1b_direction",   "闸1b 方向校验"),
    ("gate1b_bare",        "闸1b 裸单字"),
    ("gate2_content",      "闸2 内容正确性（独立词典基准）"),
    ("gate2_replay",       "闸2 补丁生效审计（原 C1）"),
    ("gate3_completeness", "闸3 02 完整性（原 C2）"),
    ("gate4_verbatim",     "闸4 引文逐字率"),
    ("trace_ledger",       "纠错留痕"),
    ("pending_conflict",   "存疑待核（补丁冲突）"),
    ("patch_apply",        "补丁施用"),
    ("silent_delete",      "静默删除（结构性噪音）"),
    ("zero_fabrication",   "零虚构"),
)


def build_audit_report(kind, book, script_name, fields, *, heading=True, b2_warn="",
                       na_note="N/A"):
    """设闸审计报告统一构建（PDF/EPUB 两路径同构，2026-10-08 适配性整改）。

    kind:        'PDF·phys 体系' / 'EPUB·SEC 体系'
    fields:      {字段键: 内容字符串（不含「- 标题：」前缀与换行）| None}；键取自 AUDIT_FIELD_ORDER
    heading:     True  → 输出 `# 设闸审计报告（kind）` + 书名引言（用于 work/audit_report.md）
                 False → 只输出字段行（用于 00 的「## 五、设闸审计报告」之下，外层已有标题）
    b2_warn:     B2 未命中告警块原文（可空），置于字段之后
    na_note:     字段缺数据时的说明；调用方若已把说明写进内容可传空串
    返回：可直接写入的完整字符串（LF 结尾）。
    """
    out = []
    if heading:
        out.append(f"# 设闸审计报告（{kind}）")
        out.append("")
        out.append(f"> 书名：《{book}》；生成于 {script_name}（与 00_梳理总览与目录对照 第五节同源）。")
        out.append("")
    for key, title in AUDIT_FIELD_ORDER:
        v = fields.get(key)
        out.append(f"- {title}：{v}" if v is not None else f"- {title}：{na_note}")
    if b2_warn:
        out.append(b2_warn.rstrip("\n"))
    return "\n".join(out) + "\n"


def note_anchor_range_report(compiled_notes, chapters, kind="SEC"):
    """各章笔记锚点是否落在对应篇区间——**按实算渲染**，不再无条件断言。

    01 头部原写死「各章笔记锚点均落入对应篇 SEC 区间」，实测这句从未被计算：
    笔记跨篇引用（第 3 章引第 1 章的 SEC）时结论仍写"均落入"，并与 00 的
    「零虚构：引文逐字 + 原书页锚」并列——把未核实的话写进权威交付件，比不写更坏。

    kind="SEC"（EPUB，锚写作（SEC idx=N））/ "PHYS"（PDF，锚写作 原书页N）。
    返回 (ok, offenders, display)；offenders = [(章号, 篇名, [越界锚...])]。
    """
    rx = re.compile(r"（SEC idx=(\d+)）" if kind == "SEC" else r"原书页[码]?\s*(\d+)")
    chap_rx = re.compile(r"^##\s*(\d+)[.、]", re.M)
    bounds = []
    for i, cc in enumerate(chapters):
        st = cc.get("phys_start")
        nx = chapters[i + 1].get("phys_start") if i + 1 < len(chapters) else None
        en = (nx - 1) if isinstance(nx, int) else None
        bounds.append((cc.get("no"), cc.get("name", ""), st, en))
    cited = {}
    for txt in compiled_notes:
        marks = [(m.start(), int(m.group(1))) for m in chap_rx.finditer(txt)]
        for j, (pos, no) in enumerate(marks):
            end = marks[j + 1][0] if j + 1 < len(marks) else len(txt)
            cited.setdefault(no, set()).update(int(x) for x in rx.findall(txt[pos:end]))
    offenders, checked = [], 0
    for no, name, st, en in bounds:
        if st is None:
            continue
        vals = cited.get(no)
        if not vals:
            continue
        checked += 1
        bad = sorted(v for v in vals if v < st or (en is not None and v > en))
        if bad:
            offenders.append((no, name, bad))
    if not checked:
        return True, [], "各章笔记未解析出区间锚点，本项未核验（须人工复核笔记锚点）"
    if not offenders:
        return True, [], f"已核验 {checked} 章：各章笔记锚点均落入对应篇区间"
    return False, offenders, (
        f"已核验 {checked} 章，**{len(offenders)} 章锚点越出本篇区间**（"
        + "；".join(f"第{no}章 {name} 越界锚 {bad}" for no, name, bad in offenders[:5])
        + "）：跨篇引用须在笔记里写明，不得笼统称「均落入」")


def sync_text_with_corrections(text, corr_files, *, work_dir=None):
    """把**已裁定的纠错台账**同步施加到派生文本（notes 编译后的 01/02 正文）。

    为什么必须有这一步（2026-10-05 审查 P0）：notes 由分章 Agent 在台账定稿前写成，
    援引的是**未修正**原文；03 经 apply_corrections 后已是干净正文。二者不同步就形成
    「错形孤岛」——01/02 里仍留着已被判定的错形（如「磨宋」「待臣」），拿 02 的名句
    去 03 grep 命中 0，「可检索 / 可回溯」契约在 01/02 上断裂；而闸1b / 闸2 只扫 03，
    闸4 逐字率以 raw ∪ clean 为底本又能命中 raw，全闸对此无感（实测假通过）。

    故：用**同一份已裁定台账**把派生文本同步到与 03 一致的形态。这不是放宽标准，
    是把「03 是权威改正底本」这条既有契约真正贯彻到全部交付件。

    不参与同步的条目（保留原样，宁可留到闸1b 报警也不猜）：
      - silent:true（结构性删除：错乱表格 / 图版文字碎片）——对象是版式产物不是正文；
      - 存疑项（同一 old 多个 new 未定稿）与正则型通改——形态未定 / 上下文依赖强；
      - 单字 systematic——与 apply_corrections_core 同护栏，未经 --force-systematic 不放行。
    改动**逐条返回**并由调用方记入 04 改错留痕（派生件同步节），零静默。

    返回 (new_text, changes)；changes = [(old, new, count, src), ...]
    """
    if not text or not corr_files:
        return text, []
    sys_map = {}          # old -> set(new)
    per_olds = []         # [(old, new, src)] 逐页定点补丁（notes 无页界，按文本命中）
    for c, src in _load_corr_entries(corr_files, work_dir=work_dir):
        if "old" not in c or "new" not in c:
            continue
        if c.get("silent") or c.get("merge") is not None or "merge" in c:
            continue                                  # 结构性删除 / 跨页合并：不同步
        old, new = c["old"], c["new"]
        if not old or old == new:
            continue
        if str(c.get("phys", "")).upper() == "ALL" or c.get("systematic"):
            if c.get("regex") or len(old) == 1:
                continue                              # 正则型 / 单字护栏：不同步
            sys_map.setdefault(old, set()).add(new)
        else:
            per_olds.append((old, new, src))
    # 冲突（同一 old 多个 new）= 未定稿，整条跳过，交由闸1b 报警
    sys_clean = [(o, next(iter(n))) for o, n in sys_map.items() if len(n) == 1]
    # 长 old 优先，避免短错形（「磨宋」）先替换掉长错形（「磨宋之役」）的命中
    todo = sorted(sys_clean + [(o, n, s) for o, n, s in per_olds],
                  key=lambda t: -len(t[0]))
    changes = []
    for old, new, *rest in todo:
        src = rest[0] if rest else "systematic"
        spans = iter_spans(text, old)
        if not spans:
            continue
        for s0, e0 in reversed(spans):                # 反向施加，坐标不移位
            text = text[:s0] + new + text[e0:]
        changes.append((old, new, len(spans), src))
    return text, changes


def apply_corrections_core(blocks, corr_files, *, merge_label="物理页", force_systematic=False, work_dir=None):
    """补丁施加核心。blocks = [[phys, book, title, body], ...]（PDF 薄壳 title 恒空串）。

    merge_label：跨页合并留痕标签（PDF="物理页" → 〔跨页合并 物理页5–6：…〕；EPUB="SEC"）。
    force_systematic：🟡10 护栏——单字 systematic 通改（如「二」→「于」）误标即全书静默
      损坏，且 C1 留痕审计对 str.replace 型通改天然失明；默认拦截（不施加、记 miss 并提示
      放行方式），确认无误后由调用方显式放行。
    work_dir：v1.14.0 P0-1——若给出且 patch_manifest.json 存在，过滤 status=deleted 的补丁
      （deleted 不物理删 corrections/*.json，但 assemble 不施加）。
    返回 (miss, pending, applied_trace, tongai, applied_silent, applied_ocr)。
    """
    by_phys = {b[0]: b for b in blocks}
    systematic = {}   # old -> list of (new, reason, src_file)
    sys_regex = set()  # 🟡NEW(v1.10.6)：声明为正则型通改的 old 字面串集合（不进逐字转义通道）
    per_phys = {}     # phys -> list of corr
    merges = []       # (phys, note)
    miss = []         # 未命中 / 被拦截的补丁
    pending = []      # (old, cands_str) 冲突存疑项
    applied_trace = 0 # 已施用的"留痕型"补丁数（per-phys 逐处纠错 + 跨页合并），C1 审计用
    applied_silent = 0 # 已施用的"静默型"补丁数（silent:true，结构性删除不留痕，C1 单独显示）
    applied_ocr = 0   # 已施用的"转录层"补丁数（ocr:true，§3.1 判为 OCR 误：修正照常施加进正文、但不留〔〕记号以免冤枉古籍，2026-10-01）
    # 留痕/存疑**延后插入**（2026-09-22）：这些是元数据，一旦就地插入就会打断后续扫描依赖的
    #   原文邻接关系——实测 phys30：逐页补丁的留痕插在「”」与「,」之间，通改规则「”,」→「”，」
    #   再也匹配不到，全角逗号被留在半角；另一页则出现留痕套留痕。故插入只登记坐标（在"不含
    #   延后项"的可见文本坐标系里），全部扫描结束后再按位置倒序一次性插入。
    pending_ins = {}  # phys -> [(pos, text), ...]

    def _pend(phys, pos, text):
        pending_ins.setdefault(phys, []).append((pos, text))

    def _shift(phys, s, e, delta):
        """某次替换把 [s,e) 换成 delta 长度差后，平移本块已登记的插入点。"""
        lst = pending_ins.get(phys)
        if not lst:
            return
        out = []
        for (pos, text) in lst:
            if pos >= e:
                out.append((pos + delta, text))
            elif pos > s:
                out.append((s + max(delta, 0), text))   # 落在被替换区内：随文前移
            else:
                out.append((pos, text))
        pending_ins[phys] = out

    def _replace(phys, body, s, e, new, trace):
        """把 body[s:e] 换成 new，并把 trace 登记为延后插入；返回新 body。"""
        _shift(phys, s, e, len(new) - (e - s))
        if trace:
            _pend(phys, s + len(new), trace)
        return body[:s] + new + body[e:]

    def _trace_for(old, new, silent=False, ocr=False):
        """生成留痕；silent=true（结构性删除）、ocr=true（转录层，§3.1：修正照常施加进正文、不留〔〕记号）或
        「只归并空白」的补丁返回空串（不留痕）。

        引述文本经 `esc_trace_text` 转写 `〔`/`〕` → `⟦`/`⟧`：留痕本身以 `〔…〕` 定界，
        引述的原文字串若自带 `〔〕`（古籍题注）会同形冲突、令下游剥不干净（详见该函数说明）。
        silent 仅限结构性错误产物（错乱表格HTML、图谱碎片小图、表/图文字层碎片），普通文字纠错严禁使用。
        ocr 仅限 §3.1 判层为转录层的纠错（错在我方 OCR，非原书排印）——修正照常施加，
        正文不留「原书作」记号（否则冤枉古籍），台账即 corrections/ 条目本身（2026-10-01）。
        """
        if silent or ocr or same_text(old, new):
            return ""
        # 2026-10-01（《商代战争与军制》实测）：old 含换行时留痕文本继承换行，
        # 「〔原书作「…」〕」被折成跨行/孤行（闭〕落次行首），破坏段落且视觉断裂。
        # 留痕引述一律压成单行：内部连续空白/换行折为 ⏎，掐头去尾。
        eo = re.sub(r"[\n\r\t ]+", "⏎", esc_trace_text(old)).strip("⏎")
        en = esc_trace_text(new)
        return _TRACE_DEL.format(old=eo) if new == "" else _TRACE_SUB.format(old=eo)

    for c, src in _load_corr_entries(corr_files, work_dir=work_dir):
        # 跳过 _meta / section 等非校订标记（无 old/phys/merge）
        if "old" not in c and "merge" not in c and "phys" not in c:
            continue
        # 🟡7 防崩（2026-09-08 剩余批）：Agent 写了脏 phys（"12a"/null/缺失）时旧实现
        # int() 裸转换会崩掉整个 assemble 且交付件半成品；现记 miss 丢弃该条，流程继续。
        if c.get("merge"):
            try:
                merges.append((int(c["phys"]), c.get("note", "")))
            except (TypeError, ValueError):
                miss.append(f"merge 补丁 phys 脏值 {c.get('phys')!r}（{src}），补丁丢弃")
            continue
        if str(c.get("phys", "")).upper() == "ALL" or c.get("systematic"):
            old = c["old"]
            # 🟡NEW（v1.10.6）：正则型通改——old 写作正则原文（条目带 "regex": true），
            #   用于"一类形态"无法逐条枚举的场合（如"汉字数字串内任意位置的 ○"）。
            #   安全性由作者写的正则自负：正则自带上下文约束，故**不套单字护栏**——
            #   那种"纯单字符 old"的护栏正是为拦住无上下文的常用字通改而设。
            #   编译期即校验：非法正则当场丢弃并报警，不让它在逐页扫描时才炸。
            if c.get("regex"):
                try:
                    re.compile(old)
                except re.error as e:
                    miss.append(f"systematic 正则非法：old「{old}」（{e}；{src}），补丁丢弃")
                    continue
                sys_regex.add(old)
            # 🟡10 护栏（2026-09-08 第 3 批）：单字 systematic = 常用字全书通改，
            # SKILL §3.5 明令严禁常用字双向形近混淆设 systematic；此处是唯一能兜住的位置。
            elif len(old) == 1 and not force_systematic:
                miss.append(f"systematic 通改被护栏拦截：单字「{old}」→「{c['new']}」（{c.get('reason', '')}，{src}）"
                            f"——单字通改误标即全书静默损坏。确认无误后加 --force-systematic 放行，或改为逐页补丁。")
                continue
            systematic.setdefault(old, []).append((c["new"], c.get("reason", ""), src))
        else:
            try:
                per_phys.setdefault(int(c["phys"]), []).append(c)
            except (TypeError, ValueError):
                miss.append(f"per-phys 补丁 phys 脏值 {c.get('phys')!r}：old「{str(c.get('old', ''))[:16]}…」（{src}），补丁丢弃")

    # 系统噪声：先做冲突检测——同一 old 出现多个不同 new 时不自动应用，原地标存疑待核
    clean_sys = []  # (old, new, reason) 无冲突，可全局替换
    for old, lst in systematic.items():
        if len({n for n, _, _ in lst}) > 1:
            uniq_news = list(dict.fromkeys(n for n, _, _ in lst))  # 保序去重
            cands = " / ".join(f"“{n}”" for n in uniq_news)
            pending.append((old, cands))
            mark = f"〔存疑：原文“{esc_trace_text(old)}”，候选{cands}，需手动核实〕"
            for b in blocks:
                for (s, e) in iter_spans(b[3], old):   # 容忍匹配：跨行错形同样标存疑，不漏标
                    _pend(b[0], e, mark)               # 延后插入，不打断后续扫描的原文邻接
        else:
            clean_sys.append((old, lst[0][0], lst[0][1]))

    # ⚠️ 施加顺序（2026-09-22 定）：存疑标记 → 通改（对逐页补丁让位）→ 逐页定点补丁。
    #   通改在先：补丁集里大量 old 抄的是"通改已改过"的文本（实测 phys8：原文「与宋良公谋图翁业」，
    #   补丁 old 写「…谋图霸业」），通改后行会让这类补丁全部失配。
    #   但通改必须**对逐页补丁声明的区间让位**：补丁按原文写的长锚（phys185「…又使得齐框」）里若
    #   含通改错形，通改先改掉它就会让长锚失配、补丁 new 里其余错字静默留下（该处「鄫国」退回
    #   「邮国」）。故先按原文收集逐页补丁的"声明区间"（claim），通改遇到与 claim 重叠的命中即让位，
    #   交由该补丁整体改写；万一补丁后来也没施上，通改残留自检会把该错形报出来（不失为可见）。

    # 零散错字（per-phys）：本页精确 → 全局回退（≥4字）→ 逐处留痕
    # C2 (2026-09-05)：同页多补丁须基于【本页当轮 body0】批量收集 span 后整体重叠消解 + 反向施加，
    #   杜绝渐进式逐条写回导致的嵌套留痕（旧实现后条会命中前条 trace 内子串，如「3章」⊂「63章」）。
    #   全局回退（落他页）立即施加；仅同页重叠场景走批量消解。
    def _run_per_phys(todo):
        """就地施加 todo（phys -> [corr]）；未施用的补丁按未命中记账（口径见下）。"""
        nonlocal applied_trace, applied_silent, applied_ocr
        for phys, lst in todo.items():
            if phys not in by_phys:
                miss.append(f"phys {phys} 不在全文中（补丁丢弃）")
                continue
            body0 = by_phys[phys][3]      # 本页当轮 body（原文经通改；留痕已延后，不在其中）
            collected = []                # (s, e, new, old, corr_idx) 基于 body0 收集，防嵌套留痕
            for ci, c in enumerate(lst):
                # P0 #4 / 🟡（2026-09-12）：原为 c["old"]/c["new"] 硬取，缺字段即 KeyError 崩掉
                # 整个 assemble（且此时交付件已写一半，留半成品）。改为记 miss 丢弃该条、流程继续，
                # 与 🟡7「脏 phys」口径一致——脏数据不致命，崩才致命。预检侧已在 check_patch 拦下。
                if "old" not in c or "new" not in c:
                    miss.append(f"phys {phys} 补丁缺 old/new 字段（脏数据，已丢弃）：{str(c)[:80]}")
                    continue
                old, new = c["old"], c["new"]
                sl, oc = c.get("silent", False), c.get("ocr", False)  # silent=结构性；ocr=转录层纠错：修正照常施加进正文、不留〔〕记号（§3.1，2026-10-01）
                # 1) 先在本页 body0 上定位全部 old 出现（多命中一并收集）
                spans = iter_spans(body0, old)
                if spans:
                    for (s, e) in spans:
                        collected.append((s, e, new, old, ci, sl, oc))
                    continue
                # 2) 本页未命中 → 全局回退（仅 old≥4 字），落他页立即施加（保持原行为，跨页不在此防嵌套范围）
                if len(old) >= 4:
                    fb = None
                    for b in blocks:
                        if find_span(b[3], old):
                            fb = b
                            break
                    if fb is not None:
                        # 🟡6 修复（2026-09-08）：回退施用成功 ≠ 未命中——仍落 miss 日志备查，
                        # 但带「回退成功」标记，主流程统计/告警/退出判定剔除该类条目（main 中 n_miss）。
                        miss.append(f"phys {phys} 本页未命中，回退成功至 phys {fb[0]}：{old[:16]}…（已施用，回退成功不计未命中）")
                        fb_body = fb[3]
                        fspans = iter_spans(fb_body, old)
                        if fspans:
                            trace = _trace_for(old, new, sl, oc)
                            for (s, e) in reversed(fspans):
                                fb_body = _replace(fb[0], fb_body, s, e, new, trace)
                            fb[3] = fb_body
                            if trace:       # 空壳留痕（只归并空白）不计入 C1 审计
                                applied_trace += 1
                            elif sl:
                                applied_silent += 1
                            elif oc:
                                applied_ocr += 1
                        continue
                else:
                    # 短 old 不做全局回退（跨页误改风险高）。但若原文有此 old、通改后的本页已出现
                    # 它的 new，说明该处已由 systematic 通改按同一修正处理（见上"代劳复核"②），
                    # 不记 miss，免得同一处修正在通改与逐页补丁两路重复报账。
                    if new and find_span(pre_original.get(phys, ""), old) and find_span(body0, new):
                        continue
                    miss.append(f"phys {phys} 本页未命中且 old 过短（{len(old)} 字），禁止全局回退：{old[:16]}…")
                # 3) 全文未命中判定
                # ⚠️ 删除型补丁 new=="" 时「new in body」恒真会整条静默吞掉 → 必须排除（OOS-A 同源）：
                #    空串 new 不能走「已覆盖」捷径，未命中即记 miss 丢弃。
                # 修正 B（2026-10-03，v1.15.2）：old 全文未命中时，不得因 new 在别处出现而静默吞掉
                # 整条补丁——这会掩盖漏标 / phys 标错导致的未纠正处（本轮 11 处即此被吞）。改为记
                # miss 报警，由人工核实是否漏标 / phys 错，绝不静默跳过。
                if new and any(new in b[3] for b in blocks):
                    miss.append(f"phys {phys} old 全文未命中但 new 已存在（疑似漏标/phys 错，未施加）：{old[:16]}…")
                    continue
                miss.append(f"phys {phys} 全文未找到「{old[:16]}…」")

            # 同页重叠消解（长 old 优先；短 old 作为子串被覆盖时丢弃，防嵌套留痕）+ 反向整体施加
            collected.sort(key=lambda x: (x[0], -(x[1] - x[0])))
            kept = []
            last_end = -1
            for (s, e, new, old, ci, sl, oc) in collected:
                if s < last_end:
                    miss.append(f"phys {phys} 「{old[:16]}…」与更长补丁重叠，已跳过（防嵌套留痕）")
                    continue
                kept.append((s, e, new, old, sl, oc)); last_end = e
            if kept:
                new_body = body0
                n_trace = 0
                n_silent = 0
                n_ocr = 0
                for (s, e, new, old, sl, oc) in sorted(kept, key=lambda x: -x[0]):
                    trace = _trace_for(old, new, sl, oc)
                    if trace:
                        n_trace += 1
                    elif sl:
                        n_silent += 1
                    elif oc:
                        n_ocr += 1
                    new_body = _replace(phys, new_body, s, e, new, trace)
                by_phys[phys][3] = new_body
                # P0 #1（2026-09-12）：按**实际插入的留痕数**累加。原用 len(kept_ci)——那是去重后的
                # 补丁序号集合，一条补丁同页命中 3 处时 kept=3 而 kept_ci=1，少计 2，使 C1 的
                # 「留痕数 ≥ 已施用数」形同虚设（假阴）。kept 已是重叠消解后真正写入的条目；
                # n_trace 再排除"只归并空白"的空壳（_trace_for 对其返回空串、正文不插留痕），
                # 使该计数与正文里实际的〔…〕条数一致。
                applied_trace += n_trace
                applied_silent += n_silent
                applied_ocr += n_ocr

    # 逐页补丁的"声明区间"（claim）：按**原文**定位每条补丁 old 的落点，供通改让位使用。
    #   与 _run_per_phys 的定位口径一致（同页优先、否则落首个命中的他页），但只取区间、不改文本。
    #   记录 (s, e, old, new)：前两项判重叠，old/new 供"让位复核"实测该补丁是否真会失配。
    def _claim_spans(body, old):
        """old 在 body 中可锚定的全部区间。

        整体匹配优先；整体不中时**退化取前 6 字**定位——补丁 old 可能**混用**「通改前」与
        「通改后」两种形态，在原文里整体找不到，但其前半段仍能圈出通改可能触及的位置。
        实测第3卷 phys403：old 为 `今本《鹃冠子》是先秦古籍《鹖冠子》和《庞媛》的合集`，
        原文作 `今本《鹃冠\\n子》是先秦古籍《鸮冠子》和《庞媛》的合集`——前一处「鹃冠子」是
        通改前原样，后一处 old 已写成通改后的「鹖冠子」。整体在原文失配；若据此放弃 claim，
        通改（容忍换行后能命中跨行的「鹃冠子」）就会把补丁锚吃掉，补丁失配、其 new 里的
        「庞煖」静默留下。退化前缀定位即可圈出该区间，让让位复核接手判定。
        """
        sp = iter_spans(body, old)
        if sp:
            return sp
        if len(old) > 6:
            return iter_spans(body, old[:6])
        return []

    claims = {}                  # phys -> [(s, e, old, new), ...]
    for phys, lst in per_phys.items():
        for c in lst:
            old = c.get("old")
            if not old:
                continue
            tgt = None
            sp = []
            if phys in by_phys:
                sp = _claim_spans(by_phys[phys][3], old)
                if sp:
                    tgt = by_phys[phys]
            if tgt is None and len(old) >= 4:
                for b in blocks:
                    sp = _claim_spans(b[3], old)
                    if sp:
                        tgt = b
                        break
            if tgt is not None and sp:
                for (s, e) in sp:
                    claims.setdefault(tgt[0], []).append((s, e, old, c.get("new", "")))

    # 通改前的原文快照（str 不可变，值语义安全）：供两处"代劳复核"判断——
    #   ① 通改规则零命中时，若其错形在原文里确有，说明它已被更长/更早的规则吃掉（实测
    #      「伍子膏」先于「子膏」施用），结果正确，不记 miss；
    #   ② 逐页短 old 补丁未命中时，若原文有此 old、通改后的本页又出现了它的 new，说明同一
    #      修正已由通改完成（通改的容忍换行正则能命中被 OCR 硬换行切断的短错形，如「惰\n况」），
    #      属"代劳"，不记 miss——否则同一处修正在通改与逐页补丁两路重复报账。
    pre_original = {b[0]: b[3] for b in blocks}

    # ---- 系统噪声（通改）单遍批量施加（2026-09-22 重写）----
    # 旧实现是 `b[3].count/replace` 裸替换：OCR 硬换行把错形词切断时（「阗\n闾」「普\n文公」
    # 「齐框\n公」）既不命中、也不报警——345 条通改实测静默漏 18 处（2026-09-22 在
    # 中国军事通史·第2卷上定量复现）。现改为：
    #   ① 每条规则预编译「仅容忍换行」正则（compile_linebreak）；
    #   ② 逐页、按补丁登记顺序**逐条**施加（与旧行为的顺序语义一致）——不用"一次收齐再消解重叠"，
    #      因为那会把仅共用一个边界字的相邻规则误判为重叠丢弃（实测 phys391：「(同上)」与
    #      「),」共享一个「)」，后者的全角逗号升级被整条丢掉）；
    #   ③ 整段替换命中 span：词内的硬换行一并归并，使修正后的词在 03 里连续可搜
    #      （03 是跨书检索保底层，词被换行切断即搜不到）；
    #   ④ 扫描的是"本页当轮文本"（原文；留痕/存疑已延后，不在其中）：通改既改不到留痕内部，
    #      也不会被延后插入物打断邻接关系；
    #   ⑤ 让位复核（按**效果**判定）：把该规则命中试施加到本页副本后，若某条逐页补丁的锚被吃掉、
    #      且它 new 又没被通改代劳（该补丁的修正确实会丢），才让这些命中退出给补丁。
    #      phys30 的「”,」→「”，」只是逗号转全角、补丁锚「…相望”」仍完好 → 不让位；
    #      phys185 的「齐框\n公」→「齐桓公」会吃掉补丁锚「…齐框」而补丁 new 尚未出现 → 让位。
    # 命中口径：施用数 + 让位数 > 0 即视为已处理；两者皆 0 才记未命中。
    # 🟡v1.10.6：sys_rules 元素末位 is_regex —— 正则型用原样 re.compile，字面型用 compile_linebreak
    # （逐字 escape + 字间容忍换行）。替换时正则型走 Match.expand(new) 以支持 \1 反向引用。
    sys_rules = [(old, new, reason,
                  (re.compile(old) if old in sys_regex else compile_linebreak(old)),
                  old in sys_regex)
                 for old, new, reason in clean_sys]
    sys_hit = {old: [0, 0] for old, _, _, _, _ in sys_rules}   # old -> [施用数, 让位数]

    if sys_rules:
        for b in blocks:
            page_claims = claims.get(b[0], ())
            for old, new, reason, rx, is_rx in sys_rules:
                hits = [(m.start(), m.end(), m) for m in rx.finditer(b[3])]
                if not hits:
                    continue
                body = b[3]
                if page_claims:      # 让位复核：只保留不会吃掉"补丁锚且补丁修正确会丢"的命中
                    while hits:
                        scratch = body
                        for (s, e, m) in sorted(hits, key=lambda x: -x[0]):
                            scratch = scratch[:s] + (m.expand(new) if is_rx else new) + scratch[e:]
                        shielded = []
                        for (cs, ce, cold, cnew) in page_claims:
                            if not any(s < ce and cs < e for (s, e, _m) in hits):
                                continue
                            if find_span(scratch, cold) or (cnew and cnew in scratch):
                                continue    # 锚仍在 / 通改已代为修正 → 无需让位
                            shielded.append((cs, ce))
                        if not shielded:
                            break
                        remain = [h for h in hits if not any(h[0] < ce and cs < h[1] for (cs, ce) in shielded)]
                        if len(remain) == len(hits):
                            break               # 无命中可让 → 跳出（防死循环）
                        sys_hit[old][1] += len(hits) - len(remain)
                        hits = remain
                for (s, e, m) in sorted(hits, key=lambda x: -x[0]):
                    body = body[:s] + (m.expand(new) if is_rx else new) + body[e:]
                    sys_hit[old][0] += 1
                b[3] = body
        for old, new, reason, rx, is_rx in sys_rules:
            if sum(sys_hit[old]) == 0:
                # 原文确有该错形却零施用、零让位 → 已被更长/更早的规则代劳（结果正确），不报。
                if any(rx.search(t) for t in pre_original.values()):
                    continue
                miss.append(f"系统替换未命中: {old}->{new}")

    # 🟡NEW（v1.10.4）：内置脚注号形态归一——覆盖任意编号，根治逐数字声明漏网。
    # 与通改同序在逐页补丁前施加；不影响留痕（此时 body 仍是纯原文已通改态）。
    for b in blocks:
        b[3] = _normalize_footnote_brackets(b[3])

    # 逐页定点补丁统一在通改之后施加（唯一一轮，两类补丁同轮处理）：
    #   ① old 抄"通改前原文形态"的（长锚含错形/跨行），因通改已对其声明区间让位而原样留存 → 此处命中；
    #   ② old 抄"通改后形态"的（如「与宋良公谋图霸业」），恰在通改后的文本上命中。
    #   单轮即可覆盖两形态；拆成两轮会让第 1 轮把第 2 轮的锚吃掉（实测 108 页差异），故不拆。
    _run_per_phys(per_phys)

    # 通改残留自检：**全部施加完成之后**按同一判据复查，仍能命中即说明该错形真有残留
    #   （例如两条通改链式相接、正确形又含错形），落 miss 引人处理——把"静默漏改"变"可见告警"。
    #   位置必须在逐页补丁之后：通改会对逐页补丁的声明区间让位（phys185「齐框\n公」等），让位处
    #   的错形正由该补丁清掉；若在补丁之前自检，会把"留给补丁处理"误报成残留（实测 3 条）。
    #   还须叠加"原文确有"：逐页补丁的 new 可能**补出**与通改错形同形的串（实测 phys306 把
    #   原文「普向伍子」补成「曾向伍子胥」，新造的「向伍子胥」不该由通改「向伍子胥→问伍子胥」
    #   去动——该处「向」是介词、本就用对），原文没有的命中不在通改管辖内，不报。
    #   自我包含型规则（old 是 new 的子串，如补字型 `X`→`X息`）施用后必然仍能命中，跳过。
    for old, new, reason, rx, is_rx in sys_rules:
        if old in new:
            continue
        for b in blocks:
            if rx.search(b[3]) and rx.search(pre_original.get(b[0], "")):
                miss.append(f"系统替换残留: {old}->{new}（施用后正文仍能命中该错形，请确认是否需补逐页补丁）")
                break

    # 全部扫描结束：把延后登记的留痕/存疑按位置倒序插入（此后不再有匹配，插入不再干扰判定）
    for phys, lst in pending_ins.items():
        if phys not in by_phys or not lst:
            continue
        body = by_phys[phys][3]
        for (pos, text) in sorted(lst, key=lambda x: -x[0]):
            body = body[:pos] + text + body[pos:]
        by_phys[phys][3] = body

    # 跨页合并标记
    for phys, note in merges:
        if phys in by_phys:
            by_phys[phys][3] = by_phys[phys][3].rstrip() + f"\n〔跨页合并 {merge_label}{phys}–{phys+1}：{note}〕\n"
            applied_trace += 1  # 跨页合并留痕（C1 审计）

    tongai = "\n".join(
        f"- 「{old}」→「{new}」（{reason}）" + ("〔正则型〕" if old in sys_regex else "")
        for old, new, reason in clean_sys) if clean_sys else ""
    tongai += ("\n" if tongai else "") + (
        "- 「[N〕/〔N]/[N]（N 为数字）」→「〔N〕」（脚注号形态归一：OCR 半角/混用括号归一成全角〔N〕；"
        "内置、覆盖任意编号，不依赖逐数字 systematic 声明）")
    return miss, pending, applied_trace, tongai, applied_silent, applied_ocr


# ---------- 交付件前缀推导（v1.15.1：源头规范，六件统一）----------
_PREFIX_STRIP_RE = re.compile(r"[_\-－—\s]*(OCR|ocr|文字版|OCR版|扫描版|电子版|精校版|校对版)[_\-－—\s]*$", re.IGNORECASE)

def derive_prefix(out_dir: str) -> str:
    """交付件前缀 = 交付目录名（去掉 _OCR / _文字版 等加工标注）。

    书籍库源头已规范命名（书名_作者），直接取目录名即可，
    不再做分段/保留/删除/白名单等复杂加工。六件统一前缀。
    """
    folder = os.path.basename(os.path.normpath(out_dir))
    return _PREFIX_STRIP_RE.sub("", folder)


# ============================================================================
# 2026-10-04 审查（P0-2 / P1-4 / P2-14 / P2-15）收敛的共享常量与函数
# —— 原在多处脚本内联重复定义，现统一为单一真相源，避免「改一处漏一处」漂移。
# ============================================================================

# P0-2：纯锚正则（phys + 可选 book，book 可为 None/数字/任意串）。
# 宽松到 book=([^>\s]+)，兼容 PDF 侧 01b 写出的 book=None 与 EPUB 的 book=N。
# ⚠️ 注意：assemble_epub 的 ANCHOR_RE（锚+正文抽取，捕获 body 第3组、含 SEC idx 分支）
#   与 04_check_citations 的 ANCHOR_RE（锚+正文，re.S）语义不同，二者不在此收敛，仍本地。
ANCHOR_RE = re.compile(r"<<<PAGE\s+phys=(\d+)(?:\s+book=([^>\s]+))?>>>", re.I)

# P0-2（2026-10-04 审查）：含正文的锚块正则（phys + 可选 book + 捕获 body 第三组，re.S）。
# 供 04_check_citations 引用，替代其本地定义；与 ANCHOR_RE（纯锚）语义不同，不混用。
ANCHOR_BLOCK_RE = re.compile(r"<<<PAGE phys=(\d+)(?: book=(\d+|None))?>>>\n(.*?)(?=\n<<<PAGE|\Z)", re.S)

# P2-14：描述性短语特征（Agent 把解释误写进 old 字段时的启发式提示）。
DESC_RE = re.compile(r"原文|此处|错字|应为|据义|上下文|误作|改作|缺字|漏字|当为|当作|应作|写错|认错|窜改|讹作")

# P1-4：叙事型书前缀 + 判定（决定 02 速查卡形态）。
# 缺省按叙事型处理（兼容旧 book_meta，type 为空时返回 True）——与 PDF 路径契约一致。
NARRATIVE_TYPE_PREFIX = ["①", "③", "④", "⑤", "⑥", "⑧", "⑩"]


def is_narrative(btype):
    """判断 book_meta.json 的 type 字段是否为叙事型（决定 02 速查卡形态）。"""
    if not btype:
        return True  # 缺省按叙事型处理（兼容旧 book_meta）
    return any(btype.strip().startswith(p) for p in NARRATIVE_TYPE_PREFIX)
