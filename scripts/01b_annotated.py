#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""01b_annotated.py —— 注本/校点本 EPUB 的语义抽取（注、校记、内条序号分标）

【与 01b_extract_epub.py 的分工】
    01b_extract_epub.py 是**通用**抽取器：按块级元素出行，块内标签压平。
    注本一旦压平，`<span class="note">`（胡三省注）与正文就并成一句 —— 引文必失真，
    这是史学引用最致命的错误（把注当原文）。本脚本按 **class 语义** 分流，
    注/校记各带行内标记，原文一字不动：

        <span class="note">   → 〖注〗…〖/注〗     行内胡注（注码原位）
        <p    class="note2">  → 〖注〗…〖/注〗     卷首成段注
        <span class="note5">  → 〖校〗…〖/校〗     校记（如章钰校记『章：…』）
        <p    class="note3">  → 剔除（版记行，计数留痕）
        <span class="number"> → 〔N〕             年内条序号（年重置）
        <p    class="emperor">→ **帝王**         加粗行
        <p    class="reign-title"> → ### 纪年    三级标题（SEC 边界）
        <h2   title="周纪一"> → ## 卷NNN　周纪一 卷题（SEC 边界）

【锚点】
    SEC = 一个可引用的最小单元（卷头 1 + 每纪年 1 + 每帝王 1 ……），
    由 profile 的 `unit_label` 决定命名。EPUB 无印页，phys = SEC idx、book = SEC idx。
    锚 title 必须能**一次 grep 定位到「卷·年·帝王」**，所以卷名与纪年要拼进去。

【产出】（与 01b 同契约，可直接喂 assemble_epub.py）
    fulltext_raw.txt     `<<<SEC idx=N title=...>>>` 逐字原样（法医底本）
    fulltext_struct.txt  `<<<PAGE phys=N book=N title=...>>>`（纠错流水线的输入）
    page_index.json      [{idx, title, char_count}]
    toc_tree.json        {front:[...], juan:[{idx,name}]}
    annotated_report.md  分流计数（注/校/条序/版记），供 00/04 引用

【用法】
    python 01b_annotated.py <book.epub> <WORK> --dump-classes     # 先普查 class，再写 profile
    python 01b_annotated.py <book.epub> <WORK> --profile p.json   # 按 profile 抽取

【profile schema】见 references/annotated-profile.md
    必填键：classes{}；其余可选。**不做隐式猜测** —— 语义分流一旦猜错就是静默改书，
    宁可硬失败要求显式声明（纪律同「加工层不得静默建目录」）。
"""
import sys
import os
import re
import json
import argparse
from collections import Counter, defaultdict

from _interp_guard import ensure_interpreter  # noqa: E402
from _work_guard import ensure_work_dir  # noqa: E402

ensure_interpreter()

from bs4 import BeautifulSoup, NavigableString, Tag  # noqa: E402

BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol",
              "td", "th", "tr", "table", "blockquote", "pre", "section", "article",
              "figcaption", "caption", "dd", "dt"}
FOOTNOTE_ATTRS = ("data-wr-footernote", "data-footernote", "data-note", "data-footnote")

# 行内标记（写进正文，供 03 检索；不可与底本字符冲突）
M_NOTE_OPEN, M_NOTE_CLOSE = "〖注〗", "〖/注〗"
M_CAL_OPEN, M_CAL_CLOSE = "〖校〗", "〖/校〗"
M_NUM = "〔{}〕"


# ---------------------------------------------------------------- 基础工具
def _safe_anchor_title(t):
    """锚 title 含 `>>>` 或换行会破坏锚格式（ANCHOR_RE 按第一个 >>> 截断）：
    换行/制表折叠为半角空格、半角 > 全角化。
    ⚠ 不动全角空格 U+3000：书名与卷名的间隔惯用全角空格，折叠成半角会让
    锚 title 与正文、与既有交付件不一致（grep 一致性命中率下降）。"""
    t = re.sub(r"[\r\n\t]+", " ", str(t))
    t = re.sub(r" {2,}", " ", t).strip()
    return t.replace(">", "＞")


def _img_marker(el):
    """图片 → 显式标记（不静默丢弃 + 可检索）。"""
    w = el.get("data-w") or el.get("data-w-new") or el.get("width") or ""
    try:
        wv = float(re.sub(r"[^\d.]", "", str(w)) or 0)
    except Exception:
        wv = 0.0
    return "〔原书图表·图片，文字层不可提取〕" if wv >= 200 else "〔原书图字·图片，文字层不可提取〕"


def _clean(t):
    t = t.replace("\u00a0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def _bare(t):
    """剥掉行内注/校标记，只留可导航的头衔或纪年（锚 title 用）。
    `威烈王〖注〗名午，考王之子…〖/注〗` → `威烈王`；标记未闭合时截到行尾。"""
    t = re.sub(M_NOTE_OPEN + r".*?(?:" + re.escape(M_NOTE_CLOSE) + r"|$)", "", t)
    t = re.sub(M_CAL_OPEN + r".*?(?:" + re.escape(M_CAL_CLOSE) + r"|$)", "", t)
    return t.strip()


# ---------------------------------------------------------------- 行内序列化
def make_inliner(P):
    """按 profile 生成行内序列化函数：谁加注标记、谁加校标记、谁转条序号。
    入参是**整份 profile**（不是 classes 子表）——img_marker 这类开关在顶层。"""
    C = P.get("classes") or {}
    inl_note = set(C.get("inline_note") or [])
    cal = set(C.get("collation") or [])
    num = set(C.get("index_number") or [])
    mark_img = P.get("img_marker", True)  # 装饰性印章/水印图多的书关掉，免得正文全是占位标记

    def _inline(node, stat):
        parts = []
        for c in node.children:
            if isinstance(c, NavigableString):
                parts.append(str(c))
                continue
            if not isinstance(c, Tag):
                continue
            if c.name in ("script", "style"):
                continue
            if c.name == "br":
                parts.append(" ")
                continue
            if c.name == "img":
                if mark_img:
                    stat["img"] += 1
                    parts.append(_img_marker(c))
                else:
                    stat["img_skipped"] += 1
                continue
            cls = set(c.get("class") or [])
            # 脚注属性展开（微信读书等：文本在属性里，元素自身无文字）
            fn = ""
            for a in FOOTNOTE_ATTRS:
                v = c.get(a)
                if v and str(v).strip():
                    fn = str(v).strip()
                    break
            if fn and not c.get_text(strip=True):
                stat["note_attr"] += 1
                parts.append(M_NOTE_OPEN + fn + M_NOTE_CLOSE)
                continue
            inner = _inline(c, stat)
            if cls & cal:
                stat["cal"] += 1
                parts.append(M_CAL_OPEN + inner + M_CAL_CLOSE)
            elif cls & inl_note:
                stat["note"] += 1
                parts.append(M_NOTE_OPEN + inner + M_NOTE_CLOSE)
            elif cls & num:
                stat["num"] += 1
                parts.append(M_NUM.format(inner.strip()))
            else:
                parts.append(inner)
        return "".join(parts)

    return _inline


def walk_blocks(root, inline, stat):
    """按 DOM 顺序产出 (tag, classes, text, title_attr)，块级元素为一行。"""
    out = []

    def rec(el):
        for c in el.children:
            if not isinstance(c, Tag):
                continue
            if c.name in ("script", "style"):
                continue
            cls = set(c.get("class") or [])
            if c.name in BLOCK_TAGS:
                nested = any(isinstance(d, Tag) and d.name in BLOCK_TAGS
                             for d in c.descendants if d is not c)
                if nested:
                    rec(c)
                else:
                    out.append((c.name, cls, inline(c, stat), c.get("title") or ""))
            else:
                rec(c)

    rec(root)
    return out


# ---------------------------------------------------------------- class 普查
def dump_classes(EPUB):
    import zipfile
    z = zipfile.ZipFile(EPUB)
    cnt, tagof, lens, samp = Counter(), defaultdict(Counter), defaultdict(list), defaultdict(list)
    for n in sorted(x for x in z.namelist() if x.endswith((".html", ".xhtml"))):
        soup = BeautifulSoup(z.read(n).decode("utf-8", "ignore"), "html.parser")
        for t in soup.find_all(True):
            cls = t.get("class") or []
            if not cls:
                continue
            key = " ".join(cls)
            cnt[key] += 1
            tagof[key][t.name] += 1
            tx = t.get_text(" ", strip=True)
            if tx and len(lens[key]) < 400:
                lens[key].append(len(tx))
            if tx and len(samp[key]) < 1:
                samp[key].append(tx[:50])
    print("%-16s %8s  %-18s %6s  %s" % ("class", "count", "tag", "medlen", "sample"))
    for key, n in cnt.most_common(60):
        L = sorted(lens[key]) or [0]
        print("%-16s %8d  %-18s %6d  %s" % (
            key, n, str(tagof[key].most_common(1)), L[len(L) // 2], (samp[key] or [""])[0]))


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("epub")
    ap.add_argument("work")
    ap.add_argument("--profile", help="语义分流 profile（JSON）")
    ap.add_argument("--dump-classes", action="store_true", help="只普查 class，不抽取")
    a = ap.parse_args()

    if a.dump_classes:
        dump_classes(a.epub)
        return
    if not a.profile:
        sys.exit("缺 --profile；先用 --dump-classes 普查 class 再写 profile"
                 "（schema 见 references/annotated-profile.md）")

    P = json.load(open(a.profile, encoding="utf-8-sig"))
    C = P.get("classes") or {}
    if not C:
        sys.exit("profile 缺 classes{}；本脚本不做隐式语义猜测")

    WORK = ensure_work_dir(a.work, "01b_annotated")
    inline = make_inliner(P)
    stat = Counter()

    title_classes = set(C.get("title") or [])
    emperor_classes = set(C.get("emperor") or [])
    reign_classes = set(C.get("reign_title") or [])
    block_note = set(C.get("block_note") or [])
    imprint_classes = set(C.get("imprint") or [])
    imprint_re = re.compile(P["imprint_re"]) if P.get("imprint_re") else None

    unit_label = P.get("unit_label", "卷{no:03d}")
    doc_re = re.compile(P["unit_doc_re"]) if P.get("unit_doc_re") else None
    short_max = int(P.get("unit_doc_max_bytes", 0) or 0)
    title_fix = {int(k): v for k, v in (P.get("title_fix") or {}).items()}
    front_docs = P.get("front_docs") or {}
    ignore_docs = set(P.get("ignore_docs") or [])  # 封面 xhtml 等：不入正文，也不算「未归类」
    merge_rules = [(re.compile(m["doc_re"]), m["title"]) for m in (P.get("merge_docs") or [])]
    heading_cont = "起"  # 卷题行里「起讫行」的起点字（「起…尽…凡…」）

    # ---- 阅读序：spine（不可用文件名排序代替，SEC idx 定义即阅读序）----
    # ⚠ ebooklib 会把 <head> 重新序列化为空 `<head/>`，`get_content()` 拿不到 <title>，
    # 所以「卷号归属」必须从 zip 原始字节取（raw_map）；spine 只用来定顺序。
    import zipfile
    from ebooklib import epub, ITEM_DOCUMENT
    _z = zipfile.ZipFile(a.epub)
    raw_map = {n: _z.read(n) for n in _z.namelist() if n.endswith((".html", ".xhtml"))}
    book = epub.read_epub(a.epub)
    id_to_item = {it.id: it for it in book.get_items()}
    ordered, seen = [], set()
    for entry in book.spine:
        idref = entry[0] if isinstance(entry, (tuple, list)) else entry
        it = id_to_item.get(idref)
        if it and it.get_type() == ITEM_DOCUMENT:
            ordered.append(it)
            seen.add(it.get_name())
    if len(ordered) < 5:
        print("WARNING: spine 有效文档数 <5，回退全量文档序（≠阅读序），SEC 编号需人工核对！")
        ordered = [it for it in book.get_items_of_type(ITEM_DOCUMENT)]

    def decode(doc):
        b = raw_map.get(doc.get_name())
        if b is None:
            b = doc.get_content()
        for enc in ("utf-8", "utf-8-sig"):
            try:
                return b.decode(enc)
            except UnicodeDecodeError:
                continue
        return b.decode("utf-8", "replace")  # 绝不静默丢字节

    # ---- 预扫：卷号归属 ----
    unit_of = {}
    if doc_re:
        for doc in ordered:
            m = doc_re.search(decode(doc))
            if m:
                unit_of[doc.get_name()] = int(m.group(1))

    blocks = []
    cur = None

    def new_sec(title, first=None, kind="sub"):
        nonlocal cur
        cur = {"title": title, "lines": [], "kind": kind}
        blocks.append(cur)
        if first:
            cur["lines"].append(first)

    def push(t, cls):
        """一行正文的落点：版记剔、成段注包、其余原样。"""
        if cls & imprint_classes and (imprint_re is None or imprint_re.search(t)):
            stat["imprint"] += 1
            return
        if cls & block_note:
            stat["note_block"] += 1
            cur["lines"].append(M_NOTE_OPEN + t + M_NOTE_CLOSE)
            return
        cur["lines"].append(t)

    for doc in ordered:
        name = doc.get_name()
        raw = decode(doc)
        soup = BeautifulSoup(raw, "html.parser")
        body = soup.body or soup
        rows = walk_blocks(body, inline, stat)

        # ---- 显式忽略（封面 xhtml / 导航页）----
        if name in ignore_docs:
            stat["ignored_doc"] += 1
            continue

        # ---- 卷题衔页（短模板页）：只记卷号与卷题，套话不入正文 ----
        if name in unit_of and short_max and len(raw.encode("utf-8")) < short_max:
            continue

        # ---- 卷正文 ----
        if name in unit_of:
            jn = unit_of[name]
            started = False
            emp = ""
            for (tag, cls, txt, ttl) in rows:
                t = _clean(txt)
                if not t:
                    continue
                if not started and (tag in ("h1", "h2") or (cls & title_classes)):
                    orig = ttl or t
                    label = title_fix.get(jn, orig)
                    new_sec(unit_label.format(no=jn) + "　" + label, kind="juan")
                    started = True
                    # 卷题行 = 纪名 + 起讫行（「起…尽…凡…」）。起讫行按首个「起」字定位，
                    # 不能按标题长度切尾：底本标题可能脱字（「后唐纪」脱「八」），
                    # 长度切法会吐出「八起阏逢…」或整行丢失。
                    i = t.find(heading_cont)
                    rest = (t[i:] if i > 0 else
                            (t[len(orig):] if t.startswith(orig) else "")).strip()
                    if rest:
                        cur["lines"].append(rest)
                    continue
                if not started:  # 未见卷题前的散行，仍归本卷（宁留勿删）
                    new_sec(unit_label.format(no=jn) + "　" + (t[:20]), kind="juan")
                    started = True
                if cls & emperor_classes:
                    emp = _bare(t)[:16]
                    new_sec(unit_label.format(no=jn) + "·" + emp, "**" + t + "**")
                    continue
                if cls & reign_classes:
                    # 标题拼「帝王+纪年」：单写「元年」在几百卷里无法定位；
                    # 注/校标记从标题里剥掉（截断会留半句注文碎片）。
                    new_sec(unit_label.format(no=jn) + "·" + emp + _bare(t)[:12], "### " + t)
                    continue
                push(t, cls)
            continue

        # ---- 前辅文（显式登记的固定文档）----
        if name in front_docs:
            title = front_docs[name]
            new_sec(title, kind="front")
            for (tag, cls, txt, ttl) in rows:
                t = _clean(txt)
                if not t or (tag in ("h1", "h2") and t == title):
                    continue
                push(t, cls)
            continue

        # ---- 按规则合并的连续文档（如校勘述略 7 个文档并一节）----
        merged = False
        for rx, title in merge_rules:
            if rx.search(name):
                if cur is None or cur["title"] != title:
                    new_sec(title, kind="front")
                for (tag, cls, txt, ttl) in rows:
                    t = _clean(txt)
                    if not t or (tag in ("h1", "h2") and t == title):
                        continue
                    push(t, cls)
                merged = True
                break
        if merged:
            continue

        stat["unclassified_doc"] += 1
        print("  [未归类文档] %s" % name, file=sys.stderr)

    # ---------- 落盘 ----------
    raw_lines, struct_lines, page_index = [], [], []
    for i, b in enumerate(blocks, 1):
        title = _safe_anchor_title(b["title"])
        bodytxt = "\n\n".join(b["lines"])
        raw_lines.append("<<<SEC idx=%d title=%s>>>\n%s" % (i, title, bodytxt))
        struct_lines.append("<<<PAGE phys=%d book=%d title=%s>>>\n%s" % (i, i, title, bodytxt))
        page_index.append({"idx": i, "title": title, "char_count": len(bodytxt)})

    os.makedirs(WORK, exist_ok=True)

    def w(fn, s):
        # 统一 LF（本库纪律：写盘 newline="\n"，禁 CRLF）
        with open(os.path.join(WORK, fn), "w", encoding="utf-8", newline="\n") as f:
            f.write(s)

    w("fulltext_raw.txt", "\n\n".join(raw_lines) + "\n")
    w("fulltext_struct.txt", "\n\n".join(struct_lines) + "\n")
    w("page_index.json", json.dumps(page_index, ensure_ascii=False, indent=1))
    chs = [{"idx": i, "name": _safe_anchor_title(b["title"])}
           for i, b in enumerate(blocks, 1) if b["kind"] == "juan"]
    w("toc_tree.json", json.dumps(
        {"front": [_safe_anchor_title(b["title"]) for b in blocks if b["kind"] == "front"],
         "juan": chs}, ensure_ascii=False, indent=1))

    nchar = sum(len("\n\n".join(b["lines"])) for b in blocks)
    rep = ["# 注本语义抽取报告（01b_annotated）", "",
           "- 底本：`%s`" % os.path.basename(a.epub),
           "- profile：`%s`" % os.path.basename(a.profile),
           "- SEC 总数：**%d**（卷级 %d）" % (len(blocks), len(chs)),
           "- 正文字符：%d" % nchar, "",
           "| 分流项 | 处数 | 说明 |", "| --- | --- | --- |",
           "| 行内注行内标记 | %d | 注码原位加 〖注〗…〖/注〗 |" % stat["note"],
           "| 成段注 | %d | 卷首/段首整段注 |" % stat["note_block"],
           "| 属性脚注展开 | %d | data-wr-footernote 等属性内文 |" % stat["note_attr"],
           "| 校记标记 | %d | 〖校〗…〖/校〗 |" % stat["cal"],
           "| 内条序号 | %d | 转 〔N〕（年内重置） |" % stat["num"],
           "| 图片标记 | %d | 〔原书图表/图字·图片〕占位 |" % stat["img"],
           "| 图片忽略 | %d | profile img_marker=false（装饰性印章/水印） |" % stat["img_skipped"],
           "| 版记剔除 | %d | 电子版制作版记（雠覆/标点/覆校行），不属原书正文 |" % stat["imprint"],
           "| 未归类文档 | %d | 需在 profile 里补 front_docs / merge_docs |" % stat["unclassified_doc"],
           ""]
    w("annotated_report.md", "\n".join(rep))

    print("SEC 总数 %d；卷 %d；正文字符 %d；注 %d（成段 %d）/ 校 %d / 条序 %d / 版记剔 %d"
          % (len(blocks), len(chs), nchar, stat["note"], stat["note_block"],
             stat["cal"], stat["num"], stat["imprint"]))
    if stat["unclassified_doc"]:
        print("⚠ 有 %d 个未归类文档，见 stderr；未归类＝内容未进正文，必须补 profile 后重跑"
              % stat["unclassified_doc"])


if __name__ == "__main__":
    main()
