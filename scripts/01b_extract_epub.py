#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EPUB 全文文本化（带章节锚点标记）—— ebook-precise-structure 技能扩展
用法:
  python 01b_extract_epub.py <book.epub> <book_workdir>

产出（落 <book_workdir>/）:
  - fulltext_raw.txt   逐章节文本（法医底本），每章前加 `<<<SEC idx=N title=...>>>` 标记，不改一字
  - fulltext_struct.txt 结构清洗版：锚统一为 `<<<PAGE phys=N book=B title=...>>>`（EPUB 无印刷页，phys=SEC idx；
                      若探测到真实印刷页码锚则 book=B=print_page 真值，否则 book=SEC idx 回退）
                      并安全合并孤立的注释序号行（整行仅圈数字）；仍保留原字节、未纠错。assemble_epub.py 的输入。
  - page_index.json    章节索引: [{idx, href, title, path, char_count}]
  - chapters/NNN_标题.txt  按阅读序逐章切分（保留双标记）
  - toc_tree.json      嵌套目录树（带 idx 映射），用于交付文档的目录骨架

说明:
  EPUB 无固定印刷页码。若检测到 `id=pageN` / `pagebreak` 等印刷页码锚点，则记录
  print_page 字段；否则 print_page=null，引用以「章节序号 + 目录路径」为锚点。
"""
import sys, re, json, os

# ---- 解释器守卫（v1.30.0）：先切到 EBOOK_PY 指定的 venv，再 import 第三方库 ----
# 详见 _interp_guard.py；必须位于 import ebooklib / bs4 之前。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

from ebooklib import epub, ITEM_DOCUMENT
from bs4 import BeautifulSoup, NavigableString, Tag

if len(sys.argv) < 3:
    sys.exit("用法: python 01b_extract_epub.py <book.epub> <book_workdir>")  # 💭：缺参友好提示（原为 IndexError）
EPUB = sys.argv[1]
OUTDIR = sys.argv[2]

# ---------- 0. 块级抽取（替代 soup.get_text("\n")，见 §3E）----------
# 旧实现 `soup.get_text("\n")` 把「每一个 <span>」都拆成独立一行，导致：
#   (a) 行宽排版切分的英文单词被切成碎片（personnel rank class / ifi / ca / ti / on system）；
#   (b) 中文句子在 <span> 边界断裂，句末标点（。/，）被甩到下一行成孤行；
#   (c) 微信读书 EPUB 的脚注存在 data-wr-footernote 属性里，get_text() 完全取不到 → 全书注释静默丢失。
# 新实现：以「块级元素」为行单位，块内 <span> 直接拼接不插分隔符；脚注属性展开为〔注：…〕；图片转标记。
BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol",
              "td", "th", "tr", "table", "blockquote", "pre", "section", "article",
              "figcaption", "caption", "dd", "dt"}
# 脚注正文所在的属性名（微信读书 data-wr-footernote 为主，兼顾常见变体）
FOOTNOTE_ATTRS = ("data-wr-footernote", "data-footernote", "data-note", "data-footnote")


def _safe_anchor_title(t):
    """🟡4（2026-09-08 剩余批）：TOC 标题含 `>>>` 或换行会破坏 `<<<SEC idx=N title=...>>>`
    锚格式（后续 ANCHOR_RE 按第一个 `>>>` 截断，正文/标题解析错位）。统一清洗：
    换行折叠为空格、半角 `>` 全角化（保语义、不再终结锚）。"""
    t = re.sub(r"\s+", " ", str(t)).strip()
    return t.replace(">", "＞")


def _img_marker(el):
    """图片 → 显式标记。宽图（>=200px）判为图表/表格版，窄图判为图版文字（如简牍隶定字）。
    本技能不接 OCR，图片内容无法进入文字层，标记的作用是「不静默丢弃」+ 可检索。"""
    w = el.get("data-w") or el.get("data-w-new") or el.get("width") or ""
    try:
        wv = float(re.sub(r"[^\d.]", "", str(w)) or 0)
    except Exception:
        wv = 0.0
    return "〔原书图表·图片，文字层不可提取〕" if wv >= 200 else "〔原书图字·图片，文字层不可提取〕"


def _node_text(node):
    """递归拼出节点内文本：<span> 等行内元素不插分隔符，脚注属性展开，<img>/<br> 转标记。"""
    parts = []
    for c in node.children:
        if isinstance(c, NavigableString):
            parts.append(str(c))
        elif isinstance(c, Tag):
            if c.name in ("script", "style"):
                continue
            if c.name == "br":
                parts.append(" ")
                continue
            if c.name == "img":
                parts.append(_img_marker(c))
                continue
            # 脚注：文本在属性里、元素自身无文字 → 就地展开，位置即原书注码位置
            fn_txt = ""
            for a in FOOTNOTE_ATTRS:
                v = c.get(a)
                if v and str(v).strip():
                    fn_txt = str(v).strip()
                    break
            if fn_txt and not c.get_text(strip=True):
                parts.append("〔注：" + fn_txt + "〕")
                continue
            parts.append(_node_text(c))
    return "".join(parts)


def _extract_lines_with_captions(soup):
    """同 _extract_lines，但对 figcaption/caption/图说类段落加 〔图表题〕 前缀。"""
    body = soup.body or soup
    out = []
    for el in _walk_blocks(body):
        txt = re.sub(r"[ \t\u00a0]+", " ", el[1]).strip()
        txt = re.sub(r"\s*\n\s*", "", txt)
        if not txt:
            continue
        if el[0]:
            txt = "〔图表题〕" + txt
        out.append(txt)
    return out


def _walk_blocks(root):
    """产出 (is_caption, text)；is_caption 依据标签名或 class 判定。
    🟡5（2026-09-08 剩余批）：旧实现把 NavigableString 一律跳过——顶层裸文本、嵌套块
    （如 div > 段落间直接文本）都被静默丢弃（内容丢失，违反"不静默丢弃"纪律）。
    现改为统一按 DOM 顺序遍历：连续裸文本合并为一行产出（宁留勿删，只多不少）；
    块级元素嵌套时递归展开；img 转标记。空白行仍被 _extract_lines_with_captions 过滤。
    （注：nested 判定的 O(n²) 全子树扫描未动——改判定粒度会波及分块行为，风险大于收益。）"""
    pending = []
    yield_out = []

    def _flush():
        nonlocal pending
        if pending:
            t = re.sub(r"\s+", " ", "".join(pending)).strip()
            pending = []
            if t:
                yield_out.append((False, t))
    for el in root.children:
        if isinstance(el, NavigableString):
            pending.append(str(el))
            continue
        if not isinstance(el, Tag):
            continue
        if el.name in ("script", "style"):
            continue
        _flush()
        if el.name == "img":
            yield_out.append((False, _img_marker(el)))
            continue
        cls = " ".join(el.get("class") or [])
        is_cap = el.name in ("figcaption", "caption") or bool(
            re.search(r"tushuo|caption|figtitle|tabletitle|imgtitle", cls, re.I))
        if el.name in BLOCK_TAGS or el.name in ("body", "html"):
            nested = any(isinstance(d, Tag) and d.name in BLOCK_TAGS for d in el.descendants)
            if not nested:
                yield_out.append((is_cap, _node_text(el)))
            else:
                yield_out.extend(_walk_blocks(el))
        else:
            t = _node_text(el)
            if t.strip():
                yield_out.append((is_cap, t))
    _flush()
    yield from yield_out
os.makedirs(OUTDIR, exist_ok=True)
os.makedirs(os.path.join(OUTDIR, "chapters"), exist_ok=True)

book = epub.read_epub(EPUB)

# ---------- 1. 元数据 ----------
md = {}
for k in ["title", "creator", "publisher", "date", "identifier", "language"]:
    try:
        v = book.get_metadata("DC", k)
        md[k] = v[0][0] if v else ""
    except Exception:
        md[k] = ""
meta = {
    "title": md.get("title", ""),
    "creator": md.get("creator", ""),
    "publisher": md.get("publisher", ""),
    "date": md.get("date", ""),
    "identifier": md.get("identifier", ""),
    "language": md.get("language", ""),
    "format": "EPUB",
}

# ---------- 2. TOC 映射 ----------
href_to_title = {}
href_to_path = {}

def norm(h):
    return (h or "").split("#")[0].strip()

def walk(toc, path):
    for t in toc:
        if isinstance(t, epub.Section):
            title = getattr(t, "title", "") or ""
            href = norm(getattr(t, "href", None))
            if href:
                href_to_title.setdefault(href, title)
                href_to_path.setdefault(href, path + [title])
            walk(getattr(t, "children", []) or [], path + ([title] if title else []))
        elif isinstance(t, epub.Link):
            title = getattr(t, "title", "") or ""
            href = norm(getattr(t, "href", None))
            if href:
                href_to_title.setdefault(href, title)
                href_to_path.setdefault(href, path + [title])
        elif isinstance(t, tuple):
            # 兼容 (title, href) / (title, [children]) / (uid, title, href)
            if len(t) >= 3 and isinstance(t[2], str):
                href = norm(t[2]); title = str(t[1])
            elif len(t) >= 2 and isinstance(t[1], str):
                href = norm(t[1]); title = str(t[0])
            else:
                href = ""; title = str(t[0])
            if href:
                href_to_title.setdefault(href, title)
                href_to_path.setdefault(href, path + [title])
        else:
            pass

walk(book.toc, [])

# ---------- 3. 按 spine 顺序取文档 ----------
spine = book.spine
id_to_item = {it.id: it for it in book.get_items()}
ordered = []
seen = set()
for entry in spine:
    # 🟡2（2026-09-08 剩余批）：个别 EPUB 库 spine 元素是裸字符串而非 (idref, linear)，
    # 旧实现的解包直接 ValueError 崩溃；现做形状兼容。
    idref = entry[0] if isinstance(entry, (tuple, list)) else entry
    it = id_to_item.get(idref)
    if it and it.get_type() == ITEM_DOCUMENT:
        ordered.append(it); seen.add(it.get_name())
if len(ordered) < 5:
    # 🟡2：兜底顺序≠spine 阅读序，而 SEC idx 恰恰定义为阅读序——短书必触发且旧版静默，
    # 会 silently 改变全书章节编号。现加醒目告警提醒主控方核对目录。
    print("WARNING: spine 有效文档数 <5，回退 get_items_of_type 全量文档序（≠spine 阅读序）；"
          "SEC idx 语义可能受影响，请对照目录骨架核对章节编号！")
    ordered = [it for it in book.get_items_of_type(ITEM_DOCUMENT)]

# ---------- 4. 逐章抽取 + 页码锚点探测 ----------
PAGE_PATTERNS = [
    re.compile(r'id=["\']?page[_\-]?(\d+)', re.I),
    re.compile(r'id=["\']?p[_]?(\d+)', re.I),
    # 🟡1（2026-09-08 剩余批）：旧正则 `class=["\']?pagebreak` 无捕获组，m.groups() 恒空，
    # 此条永远不产出页码、形同虚设。现补齐：pagebreak 元素属性中首个数字串（常见为
    # title="123" / id="pb123"）。局限：若 class 后还有其他含数字属性会误取——print_page
    # 仅作 book 字段的印页标注（phys=SEC idx 才是引用锚），误取影响有限且有 min() 兜底。
    re.compile(r'class=["\']?pagebreak[^>]*?(\d+)', re.I),
]
index = []
print_pages_by_idx = {}  # D3：按章携带探测到的 print_page，供 struct 锚回填（避免复用循环尾值）
full_parts = []
chapters_written = []
idx = 0
for doc in ordered:
    name = doc.get_name()
    # v1.16.2 修复：decode("utf-8","ignore") 会静默丢弃非 UTF-8 字节（审计 §三 高）。
    # 先试 utf-8 / utf-8-sig，都不行再用 replace（绝不丢字节）。
    _raw_bytes = doc.get_content()
    raw = None
    for _enc in ("utf-8", "utf-8-sig"):
        try:
            raw = _raw_bytes.decode(_enc)
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        raw = _raw_bytes.decode("utf-8", "replace")
    soup = BeautifulSoup(raw, "html.parser")
    # 去掉脚本/样式
    for tag in soup(["script", "style"]):
        tag.decompose()
    # 块级抽取（§3E）：块内 <span> 不插分隔符，脚注属性展开为〔注：…〕，图片转显式标记
    lines = _extract_lines_with_captions(soup)
    text = "\n".join(lines)

    # 标题/路径
    base = os.path.basename(name)
    title = href_to_title.get(name) or href_to_title.get(base) or ""
    path = href_to_path.get(name) or href_to_path.get(base) or []
    if not title:
        # 用首个非空行或文件名兜底
        title = lines[0][:40] if lines else base
    title = _safe_anchor_title(title)  # 🟡4：>>> / 换行清洗，防锚格式被破坏（下游 page_index/chapters/struct 锚全部受益）

    # 印刷页码锚点探测
    print_pages = []
    for pm in PAGE_PATTERNS:
        for m in pm.finditer(raw):
            num = m.group(1) if m.groups() else ""
            if num:
                print_pages.append(int(num))
    print_page = min(print_pages) if print_pages else None

    idx += 1
    anchor = f"<<<SEC idx={idx:03d} title={title}>>>"
    full_parts.append(anchor + "\n" + text)
    chapters_written.append((idx, title, text))
    print_pages_by_idx[idx] = print_page  # D3：按章记录印页，struct 锚回填用
    index.append({
        "idx": idx, "href": name, "title": title,
        "path": path, "char_count": len(text), "print_page": print_page,
    })

# 写 fulltext_raw.txt
with open(os.path.join(OUTDIR, "fulltext_raw.txt"), "w", encoding="utf-8", newline="\n") as f:
    f.write("\n\n".join(full_parts))

# 写 chapters/NNN_标题.txt（双标记：SEC 锚点 + 正文）
for (i, t, txt) in chapters_written:
    safe = re.sub(r'[\\/:*?"<>|\n]', "_", t)[:40]
    fn = os.path.join(OUTDIR, "chapters", f"{i:03d}_{safe}.txt")
    with open(fn, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"<<<SEC idx={i:03d} title={t}>>>\n\n{txt}")

# 写 page_index.json
with open(os.path.join(OUTDIR, "page_index.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump(index, f, ensure_ascii=False, indent=2)

# 写 toc_tree.json（带 idx 映射）
toc_tree = {"meta": meta, "sections": index}
with open(os.path.join(OUTDIR, "toc_tree.json"), "w", encoding="utf-8", newline="\n") as f:
    json.dump(toc_tree, f, ensure_ascii=False, indent=2)

# ---------- 5. 结构清洗版 fulltext_struct.txt（统一 PAGE 锚 + 安全合并孤立注释序号行）----------
def merge_orphan_markers(text):
    """把「整行仅由带圈数字序号（①–⑳、㉑+）组成」的孤立行并入上一段。
    EPUB 排版偶发：注释序号单独成行、错位到段尾；这行不是正文、也不该独立成锚。
    判定保守：行内容去掉空白后必须全部落在圈数字区间才合并，避免误并真正文短句。"""
    MARKER = re.compile(r'^[\u2460-\u24FF\u3251-\u32BF\s]+$')
    out = []
    for ln in text.split("\n"):
        if MARKER.match(ln.strip()) and out:
            out[-1] = out[-1].rstrip() + ln.strip()
        else:
            out.append(ln)
    return "\n".join(out)

struct_parts = []
for (i, t, txt) in chapters_written:
    merged = merge_orphan_markers(txt)
    # D3 印刷页码回填（带护栏）：用本章程探测到的 print_page（print_pages_by_idx[idx]），phys 仍=SEC idx；
    # 探测到真实印页（正 int>0）时把 book 填成真值，否则回退 book=idx（与旧行为一致）。
    # book 字段仅作人类可读的印页标注，phys=SEC idx 才是引用/grep 锚，故 corrections/notes 仍以 SEC idx 定位，不受影响（见 §3E）。
    cur_pp = print_pages_by_idx.get(i)
    pp = cur_pp if (isinstance(cur_pp, int) and cur_pp > 0) else i
    sa = f"<<<PAGE phys={i} book={pp} title={t}>>>"
    struct_parts.append(sa + "\n" + merged)
with open(os.path.join(OUTDIR, "fulltext_struct.txt"), "w", encoding="utf-8", newline="\n") as f:
    f.write("\n\n".join(struct_parts))
print(f"[OK] 已写 fulltext_struct.txt（PAGE 锚×{idx}，孤儿序号行已安全合并）")

print(f"[OK] EPUB 抽取完成")
print(f"  书名: {meta['title']}")
print(f"  作者: {meta['creator']}  出版: {meta['publisher']} {meta['date'][:10]}")
print(f"  章节文档数: {idx}")
print(f"  总字符数: {sum(len(t) for _,_,t in chapters_written)}")
print(f"  检测到印刷页码锚点: {sum(1 for x in index if x['print_page'])} 章（已回填进 fulltext_struct 锚的 book 字段；无锚点章 book 仍回退=SEC idx）")
print(f"  输出目录: {OUTDIR}")
print("\n--- 目录骨架（前 60 章标题）---")
for x in index[:60]:
    p = " / ".join(x["path"]) if x["path"] else ""
    print(f"  [{x['idx']:03d}] {x['title']}   <{p}>")
