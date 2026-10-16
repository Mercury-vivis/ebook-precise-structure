#!/usr/bin/env python3
# 01b_struct.py —— Stage 2 结构清洗（程序确定性，主路径）
#
# 适用：OCR 文字层 PDF（坐标不可靠，整页单块、无 y 分区）——**纯文本规则即主方法**（旧"坐标+字号"法已废）。
# 从 PDF 逐页 get_text()：剥离运行书眉/页脚（落日志可溯源），加 `<<<PAGE phys=N book=M>>>` 锚，
# 输出 `fulltext_struct.txt`。**仍保留原 OCR 字节，不做语义纠错**——语义纠错在 Stage 3/5。
#
# 用法：
#   python 01b_struct.py <book.pdf> <WORK目录> \
#       [--offset N] [--pagemap '{"6":5,"31":30,"33":32,"38":37}'] \
#       [--head-frags "注孙子,代序"] [--footer-frags "升子,羊下"] \
#       [--ratio 0.5] [--no-freq] [--comment-markers "○"]
#
# 设计纪律：
#   1. 三重判定防误删：含书名/篇名片段 + 短行(≤16字) + 无句读，缺一不可盲删。
#   2. 运行书眉/书脚靠"频率≥ratio 且 短无句读"或"显式片段"识别；首次篇名/章名标题只出现一次(频率低)不剥（结构锚保留）。
#   3. 页脚：纯数字行必剥；"书名/章名+数字"混排整行剥；OCR 错讹片段兜底。
#   4. 夹注（古籍集注）：以 comment_markers 起首的行加 〔夹注〕 轻标记，绝不剥离。
#   5. 剥离逐页落日志，可审计。
#   6. OCR 重度窜改书眉：组合多个稳定片段（如"军事历史"+"柯林斯"）覆盖变体；书眉与节标题合并同行则只剥前缀留标题。

# ---- 解释器守卫（v1.30.0）：先切到 EBOOK_PY 指定的 venv，再 import 第三方库 ----
# 详见 _interp_guard.py；必须位于 import fitz 之前。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

import fitz, json, re, os, argparse
from collections import Counter

def norm(s):
    return re.sub(r"[^\u4e00-\u9fff]", "", s)

SENT_PUNCT = re.compile(r"[曰：，。！？；、（）]")

def is_short_no_punct(line):
    if SENT_PUNCT.search(line):
        return False
    return len(norm(line)) <= 16

_FILLER = re.compile(r"[\-—=~＿_．.·\s]+$")
def strip_filler(s):
    return _FILLER.sub("", s.rstrip())

# ---------- 装饰/书眉盲区清理（norm 空 → 频率/三重判定不可见，01b 曾整批漏剥）----------
# 纯装饰行（排版横线/点阵/等号规则，如 `————————————————`）：整行仅由装饰符号构成，长度≥6。
_DECOR_LINE = re.compile(r"^[\-—=_~·.．・\s]{6,}$")
# 运行书眉数字行（如 `~ 99 ~` / `--99--` / `·100·`）：整行经去装饰符后仅剩页码数字，
# 且确含装饰符（长度短于原行）——不含纯数字行（纯数字由 is_page_seq 处理）。
def is_runhead_line(line):
    s = line.strip()
    if len(s) < 3:
        return False
    core = re.sub(r"[\-—=_~·.．・\s]", "", s)
    return bool(core) and core.isdigit() and len(core) <= 4 and len(core) < len(s)

def main():
    ap = argparse.ArgumentParser(description="Stage 2 结构清洗：页眉/页脚剥离 + book 锚（主路径，纯文本规则）")
    ap.add_argument("pdf")
    ap.add_argument("work")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--pagemap", default="", help='分段映射 JSON，如 \'{"6":5,"31":30,"38":37}\'（key=段起始物理页, value=该段 OFFSET，即 book = phys - OFFSET）')
    ap.add_argument("--head-frags", default="", help="已知运行书眉片段，逗号分隔")
    ap.add_argument("--footer-frags", default="", help="已知页脚标题 OCR 错讹片段，逗号分隔")
    ap.add_argument("--ratio", type=float, default=0.5)
    ap.add_argument("--no-freq", action="store_true")
    ap.add_argument("--comment-markers", default="○")
    ap.add_argument("--from-raw", default="",
                    help="从已有 fulltext_raw.txt 读取各页文本（而非 PDF get_text）。用于 01a_ocr.py 等外部 OCR 引擎产出的底本；此时 pdf 参数仅作占位，可不读。")
    args = ap.parse_args()

    head_frags = [f for f in args.head_frags.split(",") if f]
    footer_frags = [f for f in args.footer_frags.split(",") if f]
    pagemap = json.loads(args.pagemap) if args.pagemap else None
    starts = sorted(int(k) for k in pagemap.keys()) if pagemap else []

    if args.from_raw:
        # 外部 OCR 引擎产出的法医底本：逐 <<<PAGE phys=N>>> 块取文本（PDF 不参与）
        _raw = open(args.from_raw, encoding="utf-8").read()
        raw_lines = {}
        for ch in re.split(r"(?=<<<PAGE phys=)", _raw):
            m = re.match(r"<<<PAGE phys=(\d+)", ch)
            if not m:
                continue
            body = ch.split(">>>", 1)[1] if ">>>" in ch else ""
            raw_lines[int(m.group(1))] = [l.rstrip() for l in body.splitlines() if l.strip()]
        TOTAL = max(raw_lines) if raw_lines else 0
    else:
        doc = fitz.open(args.pdf)
        TOTAL = len(doc)
        raw_lines = {}
        for i in range(TOTAL):
            phys = i + 1
            raw_lines[phys] = [l.rstrip() for l in doc[phys - 1].get_text().splitlines() if l.strip()]

    firsts, lasts = {}, {}
    for phys in range(1, TOTAL + 1):
        lines = raw_lines.get(phys, [])
        firsts[phys] = lines[0] if lines else ""
        lasts[phys] = lines[-1] if lines else ""

    # v1.16.2 修复：频率表须基于"已剥装饰/书眉数字行"后的有效首/末行，否则真书眉被压在
    # 装饰行之下、norm 为空、整批漏剥（审计 §三 高）。先对每页首/末各做一次装饰剥离取有效行。
    def _strip_ends(ls):
        while ls and (_DECOR_LINE.match(ls[0]) or is_runhead_line(ls[0])):
            ls = ls[1:]
        while ls and (_DECOR_LINE.match(ls[-1]) or is_runhead_line(ls[-1])):
            ls = ls[:-1]
        return ls
    eff_firsts, eff_lasts = {}, {}
    for phys in range(1, TOTAL + 1):
        sl = _strip_ends(raw_lines.get(phys, []))
        eff_firsts[phys] = sl[0] if sl else ""
        eff_lasts[phys] = sl[-1] if sl else ""
    first_cnt = Counter(norm(eff_firsts[p]) for p in range(1, TOTAL + 1) if eff_firsts[p] and norm(eff_firsts[p]))
    last_cnt = Counter(norm(eff_lasts[p]) for p in range(1, TOTAL + 1) if eff_lasts[p] and norm(eff_lasts[p]))

    # 页首纯数字页码识别（连续序列判定）：页首页码被 OCR 提取成独立纯数字行，
    # 不含书名片段、不满足「三重判定」，须单独按「纯数字 + 相邻页连续」识别，避免误删正文数字。
    _PURE_NUM = re.compile(r"^\s*\d{1,4}\s*$")
    head_num = {}
    for p in range(1, TOTAL + 1):
        f = firsts[p]
        head_num[p] = int(f.strip()) if (f and _PURE_NUM.match(f)) else None

    def is_page_seq(p):
        n = head_num.get(p)
        # 排除年份特征（>1900 多为公历年份，编年体/年表式书籍首行年份易被误判为页码）
        # 🟡23（2026-09-08 剩余批）：仅排 >1900 不够——编年体书 1066/1067/1068 这类
        # 连续年份独立成行（≤1900 且相邻 ±1）仍会被误当页码剥掉。补一条硬上界：
        # 真实页码不可能显著超过全书物理页数 TOTAL（OCR 抖动/换算余量 +50），
        # 1066 之类的年份在 TOTAL<1000 的常规件中必然被排除；小页码（<100）判定不受影响。
        if n is None or n > 1900 or n > TOTAL + 50:
            return False
        nxt = head_num.get(p + 1)
        prv = head_num.get(p - 1)
        return (nxt is not None and nxt in (n + 1, n + 2)) or \
               (prv is not None and prv in (n - 1, n - 2))

    def book_of(phys):
        if pagemap:
            off = None
            for s in starts:
                if phys >= s:
                    off = pagemap[str(s)]
            if off is None:
                return None
            b = phys - off
            return b if b > 0 else None
        else:
            b = phys - args.offset
            return b if b > 0 else None

    header_log, footer_log = [], []
    blocks, index = [], []
    for i in range(TOTAL):
        phys = i + 1
        lines = raw_lines[phys]
        # 装饰/书眉盲区清理：循环剥顶部纯装饰行与运行书眉数字行，暴露真实首行给后续首行判定
        # （横线 `———`、运行书眉 `~ N ~` 等 norm 为空，频率/三重判定不可见，须前置剥除）。循环而非单次。
        while lines and (_DECOR_LINE.match(lines[0]) or is_runhead_line(lines[0])):
            header_log.append(f"phys{phys}: 剥顶部装饰/书眉行「{lines[0][:24]}」")
            lines = lines[1:]
        # 循环剥底部纯装饰行与运行书眉数字行（页脚页码常夹在装饰符间）
        while lines and (_DECOR_LINE.match(lines[-1]) or is_runhead_line(lines[-1])):
            footer_log.append(f"phys{phys}: 剥底部装饰/书眉行「{lines[-1][:24]}」")
            lines = lines[:-1]
        if lines:
            f = lines[0]
            fn = norm(f)
            freq_hit = (not args.no_freq) and bool(fn) and first_cnt[fn] >= args.ratio * TOTAL
            frag_hit = any(fr in fn for fr in head_frags)
            mkeep = re.search(r"_+\s*([\u4e00-\u9fff][^\n]*?)\s*_*$", f)
            if is_page_seq(phys):
                lines = lines[1:]
                header_log.append(f"phys{phys}: 剥页首页码「{f}」(纯数字连续页码)")
            elif frag_hit and mkeep:
                kept = mkeep.group(1).strip()
                lines = lines[1:]
                lines = [kept] + lines
                header_log.append(f"phys{phys}: 剥书眉前缀，留节标题「{kept}」 源「{f}」")
            # v1.16.2 修复：含片段即整行删会绕过"无句读"判定（审计 §三 高），与"三重判定缺一不可"相反。
            # 现强制 (frag_hit 或 freq_hit) 且 is_short_no_punct（短行+无句读）才剥，去掉 frag_hit 单独放行分支。
            elif (frag_hit or freq_hit) and is_short_no_punct(strip_filler(f)):
                lines = lines[1:]
                header_log.append(f"phys{phys}: 剥页眉「{f}」(frag={frag_hit}, freq={first_cnt[fn]})")
        # 顶部装饰/书眉数字行二次循环：CJK 书眉（如「[ 孙子兵法 ]」）经 freq/frag 剥除后，
        # 其下暴露的横线 / `~ N ~`（norm 空，频率/三重判定不可见）须再循环剥，否则整批残留
        # （郭化若本实测：横线 828 + 运行书眉 414 因此漏剥）。
        while lines and (_DECOR_LINE.match(lines[0]) or is_runhead_line(lines[0])):
            header_log.append(f"phys{phys}: 剥顶部装饰/书眉行「{lines[0][:24]}」")
            lines = lines[1:]
        if lines:
            last = lines[-1]
            ln = norm(last)
            tail_num = re.fullmatch(r"[\-—=~＿_．.·\s]*\d{1,4}[\-—=~＿_．.·\s]*", last.strip()) is not None
            freq_hit = (not args.no_freq) and bool(ln) and last_cnt[ln] >= args.ratio * TOTAL
            frag_hit = any(fr in ln for fr in footer_frags) or any(fr in ln for fr in head_frags)
            # v1.16.2 修复：页脚 frag_hit 单独放行同样绕过"无句读"判定，统一要求 (frag 或 freq) 且 is_short_no_punct。
            if tail_num or ((frag_hit or freq_hit) and is_short_no_punct(strip_filler(last))):
                lines = lines[:-1]
                footer_log.append(f"phys{phys}: 剥页脚「{last}」(num={tail_num}, frag={frag_hit}, freq={last_cnt[ln]})")
        if args.comment_markers:
            new = []
            for l in lines:
                ls = l.lstrip()
                if ls and ls[0] in args.comment_markers and not l.startswith("〔夹注〕"):
                    new.append("〔夹注〕" + l)
                else:
                    new.append(l)
            lines = new
        body = "\n".join(lines)
        b = book_of(phys)
        bstr = str(b) if b is not None else "None"
        blocks.append(f"<<<PAGE phys={phys} book={bstr}>>>\n{body}")
        index.append({"phys": phys, "book": b, "char_count": len(body)})

    os.makedirs(args.work, exist_ok=True)
    with open(os.path.join(args.work, "fulltext_struct.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(blocks))
    with open(os.path.join(args.work, "page_index.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.work, "header_strip_log.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(header_log) or "（无页眉剥离）")
    with open(os.path.join(args.work, "footer_strip_log.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(footer_log) or "（无页脚剥离）")

    print(f"完成：{TOTAL} 页 -> {args.work}/fulltext_struct.txt")
    print(f"页眉剥离 {len(header_log)} 页；页脚剥离 {len(footer_log)} 页")
    print(f"正文总字符 {sum(x['char_count'] for x in index)}")
    print(f"日志：header_strip_log.md / footer_strip_log.md")

if __name__ == "__main__":
    main()
