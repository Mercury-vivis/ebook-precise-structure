#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
assemble_epub.py —— EPUB 路径官方组装器（ebook-precise-structure 技能）

与 PDF 路径的 pipeline_assemble.py 平行：PDF 用「原书印刷页」锚，EPUB 无印刷页，
本脚本用「阅读序章节号 SEC idx」锚（phys = book = SEC idx）。其余正确性契约完全相同：
零静默改动、纠错留痕、设闸审计。

输入（落 <work>/）：
  - fulltext_struct.txt  锚 `<<<PAGE phys=N book=N title=...>>>`（01b_extract_epub.py 产出；也兼容 `<<<SEC idx=N>>>`）
  - fulltext_raw.txt     法医底本（引文逐字率比对源）
  - page_index.json      SEC 索引
  - corrections/*.json   结构化纠错补丁（与 PDF 同格式）
  - notes/0N_*.md        分章分析笔记（每章 `## N. 篇名`，引用（SEC idx=N））
  - book_meta.json       【EPUB 必建】见下方 _META_SCHEMA

输出（落 <out>/ = <书名>/，五件 00–04）：
  - <书名>03_带页码全文.md   权威纠错底本（SEC 锚 + 干净正文，纠错留痕见 04_改错留痕.md）
  - <书名>00_梳理总览与目录对照.md
  - <书名>01_章节详解.md      编译 notes/
  - <书名>02_脉络与速查卡.md  类型自适应：思想/古籍型→概念+名句双轨；叙事/事件型→要点+名句
  - 文件名直接带交付目录名前缀（去_OCR/_文字版标注，五件统一，一步到位）
  - （work 目录附带）fulltext_clean.txt、correction_miss_log.md、audit_report.md

退出码（下游判断「本次是否成功交付」以此为准，不得按交付目录文件存在性判断）：
  0 = 五件齐出（00–04）并已定稿；1 = 装不出来（前置缺件 / 台账 schema ERROR /
      manifest 与 04 生成失败 / 交付件读入失败）。
  书对不对由交付前的 check_book.py 判（闸A 可读性抽检出清单、闸B 书账互释 + 交付完整性为阻断项）——
  本脚本只保证「有东西可交、格式干净」，各设闸审计项一律是报告项、不参与退出码。

_META_SCHEMA（book_meta.json，供本脚本；按书填写）：
{
  "book": "书名",
  "author": "作者/译注",
  "translator": "译者(可选)",
  "publisher": "出版社(可选)", "year": "出版年(可选)",
  "type": "② 思想/理论型（取自 §4 类型库，决定 02 速查卡形态）",
  "source": "版本/来源说明",
  "pagemap_desc": "锚点体系一句话说明（SEC 如何对应阅读序）",
  "ocr_note": "纠错留痕规则(可选,缺省给默认)",
  "total_phys": <SEC 单元总数>,
  "overview": "全书总览一段话(可选)",
  "chapters": [ {"no":1,"name":"篇名","phys_start":<SEC idx>}, ... ],  # no=全局篇序(1..N)；phys_start=该篇首个 SEC idx
  "books": [ {"name":"子书/单元名","sec_start":<SEC idx>,"sec_end":<SEC idx>,"chapter_count":<n>}, ... ]  # 可选：02 按单元分组；缺省按 chapters[].book 分组或平铺
}
"""
import re, json, glob, os, argparse, sys, subprocess, shutil
from _common import (build_audit_report,             # 设闸审计报告统一构建（2026-10-08：与 PDF 路径字段集对齐）
                     report_corrections_ledger,       # 分析①：corrections 台账为空的显式感知（打印强告警 + 返回 N/A 说明，2026-10-08）
                     miss_alarm,                      # PDF/EPUB 共用确定性匹配 + miss 两级告警（见 §6.3 / §6.1 / scripts/_common.py）
                     classify_miss,                  # P2 新-1：miss 分四类，仅 hard 计失败阈值
                     clean_note,                      # B2：笔记清洗（原本地副本与 pipeline_assemble 逐字相同，已删）
                     sync_text_with_corrections,       # 派生件错形同步（01/02 与 03 一致性，2026-10-05 P0）
                     final_error_form_scan,            # 交付级全件错形复扫（00/01/02/03，2026-10-05 P0）
                     scan_general_items,               # 通用混淆条目（交付级复扫独立基准，2026-10-17）
                     sha256_file,                      # 交付指纹（04 末尾记 00/01/02/03 摘要，2026-10-17）
                     note_anchor_range_report,         # 01 头部区间断言按实算渲染（2026-10-05 P1）
                     reading_coverage_summary,         # 精读覆盖率如实结论（无记录须明写「未执行」，2026-10-18）
                     citation_verbatim_summary,        # 引文逐字率 C 口径：只核「关键原句摘录」四类分栏（2026-10-19）
                     check_prereq_files,               # 前置文件硬校验（写任何交付件之前，2026-10-05 P0）
                     apply_corrections_core,          # R5：补丁施加核心（2026-09-08 抽单一真相源，本文件只留薄壳）
                     count_traces, strip_correction_traces, # C1 留痕计数；strip_correction_traces=clean-03 单一真相源（v1.14.0）
                     trace_balance,                   # 留痕定界自检（2026-09-22）：引述文本里的〔〕须转写为⟦⟧
                     safe_write,                      # P0 #3：交付件落盘（覆盖保护 + 原子写），勿再 open(...,"w", newline="\n").write()
                     read_text_cn, read_json_cn,      # #24：文本/JSON 读取（统一 utf-8-sig 容错）
                     esc_cell,                        # #24：表格单元格转义（本文件此前全部裸插值）
                     check_anchors,                   # #24：R4 锚点硬闸（原内联在 main，收进 _common 供双路径共用）
                     reset_staging_dir,               # 交付暂存区落点与清理（2026-10-14 抽单一真相源，PDF/EPUB 共用，避同步盘文件锁）
                     derive_prefix,                   # v1.15.1：交付件前缀=目录名（去_OCR/_文字版标注），五件统一
                     set_backup_root,                 # 备份落点隔离：.bak 落 work 目录，不与交付五件并列
                     build_gate1_terms, collect_systematic_olds,  # P1 改造8/改造7：闸1 检索词构造 / 台账 systematic old 集合
                     emit_apply_report, _FORM_NEAR_SET, is_narrative)  # v1.18.1 改造13 统一 apply-report；P1-4 收敛 is_narrative 到 _common
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------- 施加纠错补丁（SEC 锚点感知，兼容 PAGE/SEC 两种锚）----------
# 2026-09-08 R4 修复：title 字段改为可选（原正则强制要求 ` title=`，导致
# `<<<SEC idx=N>>>` / `<<<PAGE phys=N book=N>>>` 等无 title 锚完全不被匹配——
# 文档声称的"兼容"并不成立，且 blocks 为空时静默产出空 03）；book 值放宽为
# `[^>]*` 以兼容 PDF 侧 01b 写出的 `book=None`。
ANCHOR_RE = re.compile(
    r"<<<(?:PAGE phys=(\d+) book=(\d+|None)|SEC idx=(\d+))(?: title=(.*?))?>>>\n(.*?)(?=\n<<<(?:PAGE|SEC)|\Z)", re.S)

def _anchor_phys(m):
    return int(m.group(1)) if m.group(1) else int(m.group(3))

def _anchor_book(m):
    if m.group(2):  # PAGE 分支 book 字段（含 "None" 字符串，原样保留）
        return m.group(2)
    return m.group(3) or ""  # SEC 分支：book=SEC idx

def apply_corrections(struct_text, corr_files, force_systematic=False, work_dir=None):
    """EPUB 路径薄壳（R5，2026-09-08）：锚解析 + 回写序列化在此，施加逻辑统一走
    _common.apply_corrections_core（单一真相源，勿在此再实现施加细节）。
    签名与 7 元组返回保持不变（黄金值契约；第 6 位 applied_silent 为静默施用留痕、第 7 位 applied_ocr 为转录层台账纠错数，2026-10-01）。force_systematic：🟡10 单字通改护栏放行开关。
    work_dir：v1.14.0 P0-1——传给 apply_corrections_core，过滤 patch_manifest 中 status=deleted 的补丁。"""
    blocks = []
    for m in ANCHOR_RE.finditer(struct_text):
        blocks.append([_anchor_phys(m), _anchor_book(m), m.group(4) or "", m.group(5)])
    miss, pending, applied_trace, tongai, applied_silent, applied_ocr = apply_corrections_core(
        blocks, corr_files, merge_label="SEC", force_systematic=force_systematic, work_dir=work_dir)
    fulltext = "\n".join(
        f"<<<PAGE phys={b[0]} book={b[1]} title={b[2]}>>>\n{b[3]}" for b in blocks)
    return fulltext, tongai, miss, pending, applied_trace, applied_silent, applied_ocr

# ---------- 笔记清洗（统一 `## N.` 标题，去首部体例/空行）----------
# clean_note 已抽到 _common.py（B2，2026-09-05）：此前本文件与 pipeline_assemble.py 各存一份
# 逐字相同的副本（AST 比对相似度 1.000 / 19 行）。统一走 _common.clean_note。
# 切勿在此文件再定义同名函数，否则与 PDF 路径双路径漂移。

# ---------- 02 速查卡：类型自适应抽取 ----------
CHAP_RE = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$")
def split_chapters(note_text):
    """返回 [(global_no, name, [lines])]"""
    lines = note_text.splitlines()
    chaps = []; cur = None
    for l in lines:
        m = CHAP_RE.match(l)
        if m:
            if cur: chaps.append(cur)
            cur = [int(m.group(1)), m.group(2).strip(), []]
        else:
            if cur: cur[2].append(l)
    if cur: chaps.append(cur)
    return chaps

SUBSEC_RE = re.compile(r'^\*\*[^*]+\*\*[:：]?\s*$')
def section_lines(block, header_keyword):
    """捕获以 **<含 keyword 的粗体标题>** 结尾的小节正文（允许尾部冒号）。"""
    cap = False; out = []; started = False
    for l in block:
        st = l.strip()
        if not started:
            if header_keyword in st and SUBSEC_RE.match(st):
                cap = True; started = True
            continue
        if cap:
            if SUBSEC_RE.match(st): break
            if re.match(r'^##\s', st): break
            if re.match(r'^---+\s*$', st): break
            out.append(l)
    return out

# 速查卡栏目：从笔记 subsection 标题中识别（部分匹配 keyword）
CARD_CONCEPT_KEYS = ["核心概念", "关键概念"]
CARD_QUOTE_KEY = "关键原句摘录"
CARD_POINT_KEYS = ["论证要点", "过程要点", "大事脉络", "纪事条目", "几点认识", "关键要点", "操作要点", "要点"]
# 叙事/事件型书才渲染「要点」栏目（思想/古籍型已由 01 承载，避免翻倍）
# ⑩ 文献选编/文集型同属要点型：章＝独立文献，配套字段「核心内容要点」（含"要点"，命中下方 CARD_POINT_KEYS 兜底）
# NARRATIVE_TYPE_PREFIX / is_narrative 已收敛到 _common（2026-10-04 审查 P1-4）

def extract_card(block, kind):
    if kind == "concept":
        for k in CARD_CONCEPT_KEYS:
            lines = section_lines(block, k)
            if lines:
                return [l.strip() for l in lines if l.strip().startswith("- ")]
        return []
    if kind == "quote":
        lines = section_lines(block, CARD_QUOTE_KEY)
        return [l.strip()[1:].strip() for l in lines if l.strip().startswith(">")]
    if kind == "point":
        for k in CARD_POINT_KEYS:
            lines = section_lines(block, k)
            if lines:
                return [l.strip() for l in lines if l.strip().startswith("- ")]
        return []
    return []

# is_narrative 已收敛到 _common（2026-10-04 审查 P1-4；缺省按叙事型，与 PDF 路径契约一致）

# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="本书 work 目录（含 fulltext_struct.txt / notes / corrections / book_meta.json）")
    ap.add_argument("--out", required=True, help="交付目录（建议用【绝对路径】或相对 work 的正确级数，见 SKILL.md §2）")
    ap.add_argument("--meta", required=True, help="book_meta.json 路径")
    ap.add_argument("--retrieval", default="", help="检索可达性清单（每词一行），可选")
    ap.add_argument("--error-forms", default="", help="错字清单复扫用的错形清单（每行 错误形态|正确形态|判定依据，# 注释）；与闸1 互补。"
                    "参数可省（默认探测 work/error_forms.txt）；清单缺失或为空则本项跳过并记入报告，不阻断交付")
    ap.add_argument("--no-fail", action="store_true",
                    help="历史兼容参数：补丁未命中属报告项，已恒不阻断，本旗无实际作用，保留仅为兼容既有命令行。")
    ap.add_argument("--miss-threshold", type=int, default=10,
                    help="补丁未命中的告警阈值：超过则在 00 插 ⚠️ 块并点名（报告项，不阻断、不改退出码）")
    ap.add_argument("--force-systematic", action="store_true",
                    help="🟡10 护栏放行：允许单字 systematic 通改（默认拦截——单字通改误标即全书静默损坏）")
    ap.add_argument("--no-gate1b", action="store_true",
                    help="跳过错字清单复扫（本项已是报告项，默认不阻断；此旗仅省略扫描）")
    args = ap.parse_args()
    B,  OUT = args.work, args.out
    os.makedirs(OUT,  exist_ok=True)
    # 备份隔离：交付目录只应含五件（00–04），旧件备份一律落 work 目录，避免幂等重跑持续堆积
    set_backup_root(os.path.join(B, "_bak"))
    # 缺陷件不落盘（2026-10-05 P1，复审实测确认）：全部交付件先写**系统临时暂存区**
    # （落点与清理见 `_common.reset_staging_dir`——避开 OneDrive/同步盘的文件锁；此前 EPUB 侧
    # 仍留在 `work/_out_staging`，work 落在同步盘时整体重建会被文件锁撞断，2026-10-14 收口），
    # main 末尾才整体定稿到真实交付目录。中途 sys.exit（前置缺件 / 台账 schema ERROR /
    # manifest 与 04 生成失败 / 交付件读入失败）时，真实交付目录不被触碰——上次成功交付不被破坏。
    REAL_OUT = OUT
    _staging = reset_staging_dir(B)
    OUT = _staging
    # v1.15.1：交付件前缀=真实交付目录名（去_OCR/_文字版标注），五件统一；staging 重定向不影响命名
    PREFIX = derive_prefix(REAL_OUT)
    F00 = PREFIX + "00_梳理总览与目录对照.md"
    F01 = PREFIX + "01_章节详解.md"
    F02 = PREFIX + "02_脉络与速查卡.md"
    F03 = PREFIX + "03_带页码全文.md"
    meta = read_json_cn(args.meta)  # #24：统一 utf-8-sig 容错（原裸 json.load）
    ch = meta["chapters"]
    books = meta.get("books", []) or []

    # 前置缺失硬校验（2026-10-05 P0：一律在**写任何交付件之前**做，落在后面只报警不拦，
    # 退出时交付目录已躺着半成品）。EPUB 路径三件由前置脚本产出，缺任一则锚点审计
    # 与逐字率比对无从谈起。
    check_prereq_files(B, ["fulltext_struct.txt", "fulltext_raw.txt", "page_index.json"])
    struct = read_text_cn(os.path.join(B, "fulltext_struct.txt"))  # #24：单一入口
    corr_files = sorted(glob.glob(os.path.join(B, "corrections", "*.json")))
    # 2026-10-08（分析①）：台账为空的显式感知（与 PDF 路径同源）——EPUB 侧旧版连告警都没有。
    _no_corr_note = report_corrections_ledger(corr_files)
    # ---- 台账校验（check_ledger，§6.1.3 闸前移；2026-10-18 取代 validate_corrections + check_patch）----
    # 只拦会让施加器崩溃的 schema ERROR。可落地性问题只报告不阻断——那是台账要修的事，
    # 把整本书拦下来只会逼人让报错闭嘴；书好不好由交付前的 check_book 判定。
    _sd = os.path.dirname(os.path.abspath(__file__))
    _rc_ledger = subprocess.run([sys.executable, os.path.join(_sd, "check_ledger.py"), B, "--strict"],
                                check=False).returncode
    if _rc_ledger != 0:
        print("❌ 台账 schema ERROR（缺 new 键等会让施加器崩溃），须先修台账再 assemble。")
        sys.exit(1)
    fulltext_clean, tongai, miss, pending, applied_trace, applied_silent, applied_ocr = apply_corrections(
        struct, corr_files, force_systematic=args.force_systematic, work_dir=B)
    safe_write(os.path.join(B, "correction_miss_log.md"),
                "\n".join(miss) or "（无未命中）")  # N3（2026-10-04 审查）：交付辅助件走 safe_write 原子写
    # 🟡6（2026-09-08）：剔除「回退成功」类条目后的真实未命中数——告警/统计/退出判定一律用 n_miss；
    # miss 全量仍落 correction_miss_log.md 备查（回退成功属留痕信息，非失败）。
    # P2 新-1：n_miss 只计 hard（真实内容风险），冗余/无效通改/护栏/重叠不计入失败阈值（见 _common.classify_miss）。
    n_miss_hard, n_redundant, n_invalid_sys = classify_miss(miss)
    n_miss = n_miss_hard  # 阈值 / 退出判定沿用 n_miss（=hard）
    n_fallback = sum(1 for m in miss if "回退成功" in m)
    # ---- R4 硬闸（#24 收敛）：与 PDF 路径共用 _common.check_anchors，在写任何交付件之前 ----
    struct_anchors, anchor = check_anchors(struct, fulltext_clean)
    traces = count_traces(fulltext_clean)  # P0 #1（2026-09-12）：三族留痕全计入；原内联公式漏掉删除型〔原书OCR衍…〕，合法删字被误报为静默改动
    # 留痕定界自检（2026-09-22）：软告警，不阻断（与 PDF 路径同源）。引述文本若自带 〔〕（古籍
    # 题注），留痕定界会被内层括号破坏、下游剥不干净；生成侧已由 _common.esc_trace_text 转写为 ⟦⟧。
    n_nest, n_unbal, nest_samples = trace_balance(fulltext_clean)
    if n_nest or n_unbal:
        print(f"⚠️ 留痕定界自检：疑似嵌套 {n_nest} 处、〔〕计数差 {n_unbal}（样例：{nest_samples[:2]}）")
    # ---- B2 两级告警（保留）；C1 补丁生效审计已并入闸2（§6.1 / §6.1.3）----
        # ---- B2 两级告警（保留）；C1 补丁生效审计已并入闸2（§6.1 / §6.1.3）----
    warn_block, should_fail = miss_alarm(n_miss, args.miss_threshold)
    print(f"纠错施用：anchors={anchor}，未命中 {n_miss}（真实风险；另 冗余 per-phys {n_redundant} / 无效通改 {n_invalid_sys} / 回退成功 {n_fallback}），存疑 {len(pending)}，已应用留痕型补丁 {applied_trace}，静默删除 {applied_silent}")
    if should_fail:
        print(f"  ⚠️ 补丁未命中 {n_miss} 处（>{args.miss_threshold} 阈值）：主控方必须处理，见 correction_miss_log.md"
              + ("（--no-fail 已设，不退出）" if args.no_fail else "，将以非零状态退出"))
    # 书账是否互释由交付前的 check_book 闸B 判定（§6.1.2），此处仅保留信息打印。
    if applied_silent:
        print(f"  （静默删除 {applied_silent} 处：结构性噪音，有原图/region_noise_log 可核）")
    if applied_ocr:
        print(f"  （转录层纠错 {applied_ocr} 处：已施加进 03 正文、不留〔〕记号，§3.1；完整台账见 04_改错留痕.md）")
    if miss:
        print("  未命中/回退明细（回退成功不计未命中）：")
        for m in miss: print("   -", m)
    if pending:
        print("  存疑明细：", pending)
    notes = sorted(glob.glob(os.path.join(B, "notes", "*.md")))
    # ---- 派生件错形同步（2026-10-05 P0，与 PDF 侧 pipeline_assemble.py 同口径）：
    # notes 写于台账定稿前，援引未修正原文；不同步则 01/02 留着已判定的错形，
    # 02 名句去 03 grep 命中 0，「可检索/可回溯」断裂而全闸无感。
    _sync_changes, compiled = [], []
    _synced_by_file = {}   # 02 卡片抽取须用同一份同步后文本（否则 02 仍留错形孤岛）
    for _f in notes:
        _t, _ch = sync_text_with_corrections(
            clean_note(open(_f, encoding="utf-8").read(), _f), corr_files, work_dir=B)
        compiled.append(_t)
        _synced_by_file[_f] = _t
        _sync_changes.extend(_ch)
    if _sync_changes:
        with open(os.path.join(B, "derived_sync_log.json"), "w", encoding="utf-8", newline="\n") as _fh:
            json.dump({"source": "assemble 派生件同步（notes → 01/02）",
                       "rule": "用 corrections/ 已裁定台账把 notes 同步到与 03 一致的形态；silent/存疑/正则/单字不参与",
                       "changes": [{"old": o, "new": n, "count": k, "file": src}
                                   for o, n, k, src in _sync_changes]}, _fh, ensure_ascii=False, indent=2)
        print(f"派生件错形同步：notes 同步 {sum(k for _, _, k, _ in _sync_changes)} 处（{len(_sync_changes)} 条规则）→ 04 改错留痕 §四")
    book = meta["book"]
    author = meta.get("author", "")
    translator = meta.get("translator", "")
    publisher = meta.get("publisher", "")
    year = meta.get("year", "")
    btype = meta.get("type", "")
    pagemap = meta.get("pagemap_desc", "EPUB 无印刷页，锚为阅读序 SEC idx（phys=book=SEC idx）。")
    ocr_note = meta.get("ocr_note",
        "正文逐字；非平凡 OCR 错字见 04_改错留痕.md（台账主源）。")

    # ---- 03 头部（SEC 体系）----
    # v1.14.0：03 头部不写通改清单（通改登记入 04 台账），tongai_block 死代码已删。
    pending_block = ""
    if pending:
        rows = "\n".join(f"> | {esc_cell(old)} | {esc_cell(cands)} |" for old, cands in pending)  # #24：转义（原裸插值）
        pending_block = (f"\n> **存疑待核（{len(pending)} 处，需手动核实）**：\n"
                         f"> | 原文 | 候选 |\n> |---|---|---|\n{rows}\n")
    fulltext_clean_stripped = strip_correction_traces(fulltext_clean)
    hdr03 = (
        f"# 《{book}》· 带页码全文（权威纠错底本 · EPUB·SEC 锚点）\n\n"
        f"> **来源**：{meta.get('source','')}\n"
        f"> **作者/译注**：{author}"
        + (f"（{translator} 译注）" if translator else "")
        + (f"\n> **出版**：{publisher}，{year}" if publisher else "")
        + f"\n> **类型**：{btype}\n"
        f"> **规模**：{meta.get('total_phys','?')} 个阅读序单元（SEC）/ 约 {len(fulltext_clean_stripped)//10000} 万字符 / {len(ch)} 篇正文\n"
        f"> **锚点体系（关键）**：{pagemap}\n"
        f"> **OCR / 通改规则**：{meta.get('ocr_note','正文逐字；非平凡 OCR 错字见 04_改错留痕.md（台账主源）。')}\n"
        + f"> **锚点格式**：每单元 `<<<PAGE phys=N book=N title=篇名>>>`，可在 03 中 `grep \"<<<PAGE phys=N\"` 直达原文；引用以（SEC idx=N）为准。\n"
        f"> **本文件性质**：本技能『唯一权威纠错底本』——03 为干净可引用/可检索全文，**正文不再内嵌〔〕留痕**；"
        f"所有纠错已全量应用并登记入 `{F03}` 同名 04_改错留痕.md（台账主源 + diff 兜底，§6.3），"
        f"由闸B 书账互释双向校验（§6.1.2）。\n\n---\n\n"
    )
    safe_write(os.path.join(OUT, F03), hdr03 + fulltext_clean_stripped)  # P0 #3：覆盖保护 + 原子写
    safe_write(os.path.join(B, "fulltext_clean.txt"), fulltext_clean_stripped)  # N3（2026-10-04 审查）：走 safe_write 原子写
    print(f"03 完成：{anchor} 锚点，{len(fulltext_clean_stripped)} 字符（干净正文，留痕见 04_改错留痕.md）")

    # ---- v1.14.0：patch_manifest → 04_改错留痕.md → 应用报告（闸2 回放结论并入 00 审计节）----
    # N2（2026-10-04 审查）：manifest / 04_改错留痕 是交付链的一环，生成失败必须阻断，
    # 不得 check=False 静默继续（否则可能缺 04 仍交付，违反 SKILL「六份必须交付」）。
    _sd = os.path.dirname(os.path.abspath(__file__))
    for _cmd, _name in (
        ([sys.executable, os.path.join(_sd, "gen_patch_manifest.py"), "--work", B], "gen_patch_manifest.py"),
        ([sys.executable, os.path.join(_sd, "gen_correction_ledger.py"),
          "--work", B, "--out", OUT, "--meta", args.meta, "--prefix", PREFIX], "gen_correction_ledger.py"),
    ):
        if subprocess.run(_cmd).returncode != 0:
            print(f"❌ {_name} 失败（manifest / 04_改错留痕 未生成），拒交付；请修复后重跑 assemble。")
            sys.exit(1)
    apply_summary, apply_bad, residual_n = emit_apply_report(B, OUT, args.meta, pending)
    print("应用报告：" + apply_summary)
    # ---- 交付前自检：check_book 闸A/闸B（§6.1），**只报告不阻断**（与 PDF 路径同口径）----
    # residual 的来源多是台账（补丁没生效 / old 写成通改前形态 / phys 记错页），硬闸会把人逼回
    # 「改台账→重跑」；书行不行由 check_book 独立判定，处置是「定点改 03 / 改台账」。
    subprocess.run([sys.executable, os.path.join(_sd, "check_book.py"),
                    "--work", B, "--out", OUT, "--meta", args.meta,
                    "--prefix", PREFIX, "--no-delivery"], check=False)
    # ---- 闸1 检索可达性（检索空间 = 03 正文 + 03 头部书目行 + 编译笔记）----
    gate1 = {"terms": 0, "miss": []}
    if args.retrieval:
        terms = [l.strip() for l in open(args.retrieval, encoding="utf-8") if l.strip()]
        gate_src = "外部清单"
    else:
        terms = build_gate1_terms(meta, struct, meta.get("type", ""))
        gate_src = "自动生成（章名 + proper_names + 词典正确形态）"
    gate1["terms"] = len(terms)
    if terms:
        hdr03_gate = "\n".join(
            l for l in hdr03.split("\n")
            if l.startswith("# 《") or l.startswith("> **来源") or l.startswith("> **作者")
            or l.startswith("> **出版") or l.startswith("> **类型"))
        notes_text = "\n".join(compiled)   # 与 PDF 侧 pipeline_assemble.py 同口径（🟡11 行为对齐）
        search_space = fulltext_clean_stripped + "\n" + hdr03_gate + "\n" + notes_text
        gate1["miss"] = [t for t in terms if search_space.count(t) == 0]
        print(f"闸1 检索（{gate_src}）：{len(terms)} 词，缺失 {len(gate1['miss'])}：{gate1['miss']}")
        if len(terms) < 5:
            print(f"⚠️ 闸1 检索词仅 {len(terms)} 个（<5），检索面偏窄，建议补充 book_meta.proper_names 或 --retrieval 清单。")
    else:
        print("⚠️ 闸1：未生成任何检索词（无章名片段 / proper_names / 词典命中），跳过（建议提供 --retrieval 关键专名清单）")

    # ---- 错字清单复扫（与闸1 互补；报告项、不阻断，与 pipeline_assemble.py 同口径）----
    # 检索空间 = fulltext_clean（03 正文，不含头部通改清单——头部清单会记录错误形态，包含则永远报残留）。
    # 作用：把人工确认过的错形清单在 03 里过一遍，有残留就点名。清单缺失或为空只记报告，不拦交付。
    gate1b = {"items": 0, "residual": [], "status": "missing", "bare_note": 0}
    ef_items = []   # 交付级全件错形复扫复用（错误形态|正确形态 对）
    if args.no_gate1b:
        gate1b["status"] = "skipped"
        print("错字清单复扫：已用 --no-gate1b 省略（本项为报告项，省略不影响交付）")
    else:
        ef_path = args.error_forms or os.path.join(B, "error_forms.txt")
        if os.path.isfile(ef_path):
            ef_items = []
            for raw in open(ef_path, encoding="utf-8"):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("|")
                if len(parts) >= 2 and parts[0].strip():
                    ef_items.append((parts[0].strip(), parts[1].strip()))
            gate1b["items"] = len(ef_items)
            # 2026-10-08 适配性修复：清单"存在但无有效条目"（空文件/仅注释）时，ef_items=[] →
            # 残留循环会空转并判 pass（假通过），且交付级复扫因 `if ef_items` 一并跳过。
            # 故显式打一行「本项跳过」，不留「检查通过」的错觉。
            if not ef_items:
                print("错字清单复扫：error_forms.txt 存在但无有效条目（空文件或仅注释行），本项跳过。")
            # ---- P1 改造5（方向校验）+ 改造7（裸单字覆盖告警）----
            gate1b_dir, bare_ef = [], [w for w, _ in ef_items if len(w) == 1]
            sys_olds = collect_systematic_olds(B)
            for wrong, right in ef_items:
                if wrong == right:
                    gate1b_dir.append(f"{wrong}：wrong==right，方向无效（该条无意义，建议删除）")
                    continue
                if struct and wrong not in struct:
                    gate1b_dir.append(f"{wrong}：错误形态在底本(struct) 0 命中——可能凭空制造或方向反了")
                if right and right not in fulltext_clean_stripped:
                    gate1b_dir.append(f"{right}：正确形态在 03 干净正文 0 命中——修正方向可能有误")
            bare_uncovered = [w for w in bare_ef if w not in sys_olds]
            gate1b["direction"] = gate1b_dir
            gate1b["bare_uncovered"] = bare_uncovered
            if bare_ef:
                _cv = [w for w in bare_ef if w in sys_olds]
                print(f"闸1b 裸单字告警：{len(bare_ef)} 条裸单字"
                      + (f"，{len(_cv)} 条已由台账 systematic 覆盖" if _cv else "")
                      + (f"，{len(bare_uncovered)} 条无台账覆盖（建议改复合补丁）" if bare_uncovered else ""))
                for w in bare_uncovered:
                    print(f"   ⚠️ 裸单字「{w}」无台账 systematic 覆盖，漏检风险")
            if gate1b_dir:
                print(f"闸1b 方向校验告警 {len(gate1b_dir)} 条：")
                for _d in gate1b_dir:
                    print("   ⚠️ " + _d)
            # 2026-10-08 适配性修复：与 PDF 路径（pipeline_assemble.py 的闸1b 裸单字豁免段）同口径——
            # 裸单字（如 入/人）在全书必然命中，直接 count 必误报，且会导致"清单含裸单字即永不通过"；
            # 其清零由带上下文串的 per-phys 补丁保证（已由闸2 逐补丁全量校验覆盖），此处只计数不阻断。
            for wrong, right in ef_items:
                if len(wrong) >= 2:
                    cnt = fulltext_clean_stripped.count(wrong)
                    if cnt > 0:
                        gate1b["residual"].append(f"{wrong}({cnt}处→{right})")
                else:
                    gate1b["bare_note"] += 1
            if gate1b["residual"]:
                gate1b["status"] = "fail"
                print(f"错字清单复扫：{len(ef_items)} 项（其中裸单字 {gate1b['bare_note']} 条靠台账通改保证、不计），残留 {len(gate1b['residual'])}：{gate1b['residual']}（报告项：按清单补改 03 并在 04 §四 留痕；不阻断交付）")
            else:
                gate1b["status"] = "pass"
                print(f"错字清单复扫：{len(ef_items)} 项（其中裸单字 {gate1b['bare_note']} 条靠台账通改保证、不计），残留 0（清单所列错形在 03 中已全部清零）")
        else:
            gate1b["status"] = "missing"
            print(f"错字清单复扫：未提供 error_forms.txt（默认 {ef_path}），本项跳过。"
                  f"如需此项，用 gen_error_forms.py --work <work> 生成候选、人工确认后改名 error_forms.txt。")

    # 有残留只报告（与 PDF 路径同口径）：清单是人工确认过的错形，残留说明该补的还没补，
    # 记一笔不拦交付。判据在书不在清单——真残留会被交付前 check_book 的闸B 抓出来。
    if gate1b["status"] == "fail":
        print("错字清单复扫：有残留（见上），属报告项，不阻断；按清单补改 03 并在 04 §四 留痕即可。")

    # ---- 01 章节详解（编译 notes）----
    total_cite = sum(len(re.findall(r"SEC idx=\d+", open(f, encoding="utf-8").read())) for f in notes)
    toc = "\n".join(f"{c['no']}. {c['name']}（SEC idx {c.get('phys_start')}）" for c in ch)
    intro01 = (
        f"# 《{book}》章节详解（结构化梳理 · EPUB·SEC 锚点）\n\n"
        f"> **书类型**：{btype}\n"
        f"> **引用锚点**：（SEC idx=N），{pagemap.rstrip('。')}。{{_anchor_claim}}。\n"
        f"> **OCR 纠错**：正文逐字；已裁定错字在 03 直接改正，逐条改错记录见 `04_改错留痕.md`；年代/数字核对原文不臆造。\n\n"
        f"## 目录（{len(ch)} 篇）\n\n{toc}\n\n---\n\n"
    )
    _ok_anchor, _off, _anchor_claim = note_anchor_range_report(compiled, ch, kind="SEC")
    if not _ok_anchor:
        print(f"⚠️ 01 锚点区间核验：{_anchor_claim}")
    intro01 = intro01.replace("{_anchor_claim}", _anchor_claim)
    safe_write(os.path.join(OUT, F01), intro01 + "\n\n---\n\n".join(compiled))  # P0 #3：覆盖保护 + 原子写
    print(f"01 完成：{len(notes)} 份笔记编译，SEC 引文锚点约 {total_cite} 处")

    # ---- 02 速查卡（类型自适应：概念/要点/名句）----
    no2name = {c["no"]: c["name"] for c in ch}
    no2sec = {c["no"]: c.get("phys_start") for c in ch}
    # 单元分组
    if books:
        units = []
        for bk in books:
            members = [c for c in ch if bk["sec_start"] <= c.get("phys_start", -1) <= bk["sec_end"]]
            units.append((bk["name"], members, bk.get("sec_start"), bk.get("sec_end")))
    elif any("book" in c for c in ch):
        from collections import OrderedDict
        d = OrderedDict()
        for c in ch:
            d.setdefault(c.get("book"), []).append(c)
        units = [(k, v, None, None) for k, v in d.items()]
    else:
        units = [(None, ch, None, None)]

    # 解析所有笔记的章 -> 概念 / 要点 / 名句
    chap_card = {}
    for nf in notes:
        # B4（2026-09-29）：先 clean_note 再切分——02 卡片抽取此前读原始笔记，
        # 章头若写作 `# N.`（H1）则 CHAP_RE 不中、卡片静默全空（C2 报 12 篇全空的实例）。
        # clean_note 已把 `# N.` 归一为 `## N.`，与 01 编译口径一致。
        # 2026-10-05 P0：此处原为重新读原始笔记——01 用的是同步后文本而 02 用原始笔记，
        # 同一份交付里 01 已改正、02 仍留错形，02 的名句到 03 检索 0 命中。
        t = _synced_by_file.get(nf) or clean_note(open(nf, encoding="utf-8").read(), nf)
        for gno, name, block in split_chapters(t):
            if gno not in no2name:
                continue
            chap_card.setdefault(gno, {"concept": [], "point": [], "quote": []})
            chap_card[gno]["concept"] += extract_card(block, "concept")
            chap_card[gno]["point"] += extract_card(block, "point")
            chap_card[gno]["quote"] += extract_card(block, "quote")

    show_point = is_narrative(btype)
    # 渲染各单元
    sec_concept, sec_point, sec_quote = [], [], []
    for uname, members, s0, s1 in units:
        label = f"### 《{uname}》（SEC {s0}–{s1}）\n" if (uname and s0 is not None) else (
            f"### 《{uname}》\n" if uname else "")
        if label:
            sec_concept.append(label); sec_point.append(label); sec_quote.append(label)
        for c in members:
            no = c["no"]; sec = no2sec.get(no)
            sec_concept.append(f"#### {no}. {no2name[no]}（SEC {sec}）\n")
            for cb in chap_card.get(no, {}).get("concept", []):
                sec_concept.append(cb)
            sec_concept.append("")
            if show_point:
                sec_point.append(f"#### {no}. {no2name[no]}（SEC {sec}）\n")
                for pb in chap_card.get(no, {}).get("point", []):
                    sec_point.append(pb)
                sec_point.append("")
            sec_quote.append(f"#### {no}. {no2name[no]}（SEC {sec}）\n")
            for q in chap_card.get(no, {}).get("quote", []):
                sec_quote.append(f"> {q}")
            sec_quote.append("")
    n_concept = sum(len(v.get("concept", [])) for v in chap_card.values())
    n_point = sum(len(v.get("point", [])) for v in chap_card.values())
    n_quote = sum(len(v.get("quote", [])) for v in chap_card.values())
    # C2 软告警：某章 concept+point+quote 全空 → 02 静默缺章（多为笔记没严格按 §4 模板），待复核（非硬失败）
    empty_chaps = [c for c in ch
                   if not (chap_card.get(c["no"], {}).get("concept")
                           or chap_card.get(c["no"], {}).get("point")
                           or chap_card.get(c["no"], {}).get("quote"))]

    # §一 架构/全篇一览
    if books and len(books) > 1:
        arch_rows = "\n".join(
            f"| 《{esc_cell(bk['name'])}》 | {esc_cell(bk.get('chapter_count','?'))} | SEC {bk['sec_start']}–{bk['sec_end']} | "  # #24
            + "、".join(no2name[c['no']] for c in ch if bk['sec_start'] <= c.get('phys_start', -1) <= bk['sec_end'])
            + " |" for bk in books)
        sec_one = (f"## 一、{len(books)} 单元 × {len(ch)} 篇架构一览\n"
                   f"| 单元 | 篇数 | SEC 区间 | 篇目 |\n|---|---|---|---|\n{arch_rows}\n")
    else:
        sec_one = (f"## 一、全书 {len(ch)} 篇一览\n"
                   + "\n".join(f"- {c['no']}. {no2name[c['no']]}（SEC {no2sec.get(c['no'])}）" for c in ch) + "\n")

    sections = []
    sections.append(sec_one)
    # 2026-10-08（f·两路径适配性对齐）：读法建议按 meta.type 分流，与 PDF 路径 pipeline_assemble.py 同口径。
    # 旧版此处硬编码通用读法，④制度/沿革型书（叙事型之一种）拿不到制度史专属读法 → 两路径 02 读法漂移。
    _tl = btype.split('（')[0].strip() if btype else ''
    _is_inst = ("制度" in _tl or "沿革" in _tl or "④" in (btype or ""))
    _reading_hint = ("先抓每篇制度源流与职官设置主线（沿革脉络 / 机构职官 / 核心概念）"
                     if _is_inst else "先抓每篇主线与「核心概念 / 论证要点」")
    sections.append("## 二、读法建议\n"
                    f"1. {_reading_hint}（见 `{F01}`）；\n"
                    f"2. 用（SEC idx=N）在 `{PREFIX}03_带页码全文.md` `grep \"<<<PAGE phys=N\"` 直达原文；\n"
                    f"3. 非平凡错字已在 03 改正，逐条改错记录见 04_改错留痕.md 台账。\n")
    nsec = 3
    if n_concept:
        sections.append(f"## {nsec}. 核心概念速查（按单元·篇，附 SEC idx）\n"
                        f"> 取自各章笔记「核心概念/关键概念」小节，逐条保留（SEC idx=N）锚点。共 {n_concept} 条。\n\n" + "\n".join(sec_concept) + "\n")
        nsec += 1
    if show_point and n_point:
        sections.append(f"## {nsec}. 关键要点速查（按单元·篇，附 SEC idx）\n"
                        f"> 取自各章笔记「论证/过程/大事/纪事要点」小节，便于事件/脉络速查。共 {n_point} 条。\n\n" + "\n".join(sec_point) + "\n")
        nsec += 1
    if n_quote:
        sections.append(f"## {nsec}. 关键名句速查（按单元·篇，附 SEC idx）\n"
                        f"> 取自各章笔记「关键原句摘录」小节，逐条保留（SEC idx=N）锚点，可直接引用。共 {n_quote} 条。\n\n" + "\n".join(sec_quote) + "\n")
        nsec += 1
    card_kind = "概念+名句" if not show_point else "要点+名句"
    hdr02 = (
        f"# 《{book}》脉络与速查卡（EPUB·SEC 锚点 · {btype.split('（')[0].strip()}）\n\n"
        f"> 本书锚点体系：{pagemap}\n"
        f"> 速查卡形态（类型自适应）：{card_kind}。\n\n"
        + "\n".join(sections)
    )
    safe_write(os.path.join(OUT, F02), hdr02)  # P0 #3
    point_rep = f"，要点 {n_point} 条" if (show_point and n_point) else ""
    print(f"02 完成：概念 {n_concept} 条{point_rep}，名句 {n_quote} 条（{len(units)} 单元分组，类型={btype.split('（')[0].strip()}）")

    # ---- 00 总览与目录对照 + 设闸审计报告：见本函数后段 ----
    # 2026-10-08 适配性整改：00 的构建与写入由原位**下移**到审计数据（cited/hallu/missing/
    # quotes/hit/rate）算毕之后——目的是让 00 第五节与 work/audit_report.md 同源同字段集。

    # ---- 设闸审计（基于 book_meta.chapters 权威篇目，规避按章名匹配对「始计第一」式章名的误判）----
    # 前置缺失硬校验：EPUB 路径这两件由前置脚本产出，缺任一件后面的锚点审计无从谈起。
    # 缺失即明确报错退出（不降级、不静默跳过），并点名本次已落盘件——半成品不是交付。
    for _f, _src in (("fulltext_raw.txt", "01b_extract_epub.py / 00_ingest_bookocr.py"),
                     ("page_index.json", "01b_extract_epub.py / 02_ncx_split_epub.py --apply")):
        if not os.path.exists(os.path.join(B, _f)):
            _done = sorted(x for x in os.listdir(OUT) if x.endswith(".md")) if os.path.isdir(OUT) else []
            print("[闸0 前置缺失] work 目录缺少 %s（由 %s 产出），无法做锚点有效性审计。" % (_f, _src))
            print("  处理：补跑前置脚本生成该文件后重跑 assemble——前置不全不放行。")
            print("  注意：本次已写入的交付件不构成有效交付，重跑前请清空交付目录：%s" % OUT)
            print("        本次已落盘 md：%s" % ("、".join(_done) if _done else "（无）"))
            sys.exit(1)

    raw = read_text_cn(os.path.join(B, "fulltext_raw.txt"))          # #24：单一入口（原裸 open，带 BOM 会炸）
    idx = read_json_cn(os.path.join(B, "page_index.json"))           # #24：同上
    # 兼容两种 page_index 格式：① list[dict] 含 idx 键（标准 EPUB）；② dict 含 pages 键（book_ocr ingest，每页含 phys/book）
    if isinstance(idx, dict):
        idx = idx.get("pages", [])
    # idx_set：page_index.json ∪ struct 实有锚 phys（NCX 细化后 phys 连续重排、page_index 已重建；
    # 双重并集使幻觉锚点判定对重排鲁棒，不影响既有无 NCX 书）。
    struct_phys = set(int(m) for m in re.findall(r"<<<PAGE phys=(\d+)", struct))
    idx_set = {x.get("idx", x.get("phys")) for x in idx} | struct_phys
    cited = set()
    for nf in notes:
        t = open(nf, encoding="utf-8").read()
        for m in re.finditer(r"（SEC idx=(\d+)）", t):
            cited.add(int(m.group(1)))
    hallu = sorted(s for s in cited if s not in idx_set)
    # 章节覆盖率：按篇 SEC 区间 [phys_start, 下一 phys_start) 判定是否至少命中一处
    max_sec = max(idx_set) if idx_set else 0
    missing = []
    for i, c in enumerate(ch):
        start = c.get("phys_start")
        if start is None:
            missing.append(c); continue
        end = ch[i + 1].get("phys_start", max_sec + 1) if i + 1 < len(ch) else (max_sec + 1)
        if not any(start <= s < end for s in cited):
            missing.append(c)
    # 引文逐字率（**C 口径**，2026-10-19 定）：只核笔记「关键原句摘录」小节，不扫全篇引号。
    # 旧口径扫全篇引号句，把标题、术语、译者按、自造框架名一并当引文——实测两本真书
    # 68% / 74.8%，未命中项大量不是引文；其中「03 逐字」只覆盖 C 口径的 29%。
    # 笔记里唯一**声称是原书原话**的地方就是这一小节，核它才有意义。
    # 四类判定（✅03逐字 / ⚠️仅raw / ⚠️03疑似残留 / ❌均无）全部**只报告、不阻断**：
    # 明细落 work/citation_verbatim_report.md，由人逐条核。不再以「≥95%」判 PASS——
    # 笔记的压缩改写会让该值天然低于 95%，拿它当闸只会逼人删合法引文去凑数。
    cite_summary = citation_verbatim_summary(B, notes, fulltext_clean, raw)
    print("\n=== 设闸审计（EPUB 适配）===")
    print(f"[锚点有效性] 笔记引用 SEC 数={len(cited)}；幻觉锚点(不在 page_index)={hallu if hallu else '无'}")
    print(f"[章节覆盖率] 权威 {len(ch)} 篇，缺失引用={[ (c['no'],c['name']) for c in missing ] if missing else '无（全覆盖）'}")
    print(f"[引文逐字率] {cite_summary}")
    print(f"[纠错留痕] 03 已为干净全文（留痕见 04_改错留痕.md）；补丁未命中 {n_miss}；存疑 {len(pending)}；已应用留痕型补丁 {applied_trace}")
    print(f"[补丁回放] 03 对 active 补丁 old 回放：残留={residual_n} → {'一致（无静默改动）' if residual_n == 0 else '⚠️ 不一致（由 check_book 闸B 判处置）'}")
    if empty_chaps:
        print(f"[02 完整性 C2] ⚠️ {len(empty_chaps)} 篇 concept+要点+名句全空（待复核）：{[(c['no'],c['name']) for c in empty_chaps]}")
    print(f"[闸1 检索] {gate1['terms']} 词，缺失 {len(gate1['miss'])}（多为凝练标签/带标点短语，见 01 笔记）")
    # 引文逐字率不进判定：它是报告项（SKILL §6.1）。PASS 只看锚点有效性与全篇覆盖。
    if not hallu and not missing:
        print("设闸结论：PASS（锚点有效 / 全篇覆盖 / 引文逐字率见报告，不阻断）")
    else:
        print("设闸结论：需复核（见上）")
    # ---- 00 总览与目录对照 + 设闸审计报告（统一构建，2026-10-08）----
    # 本段置于审计数据（cited/hallu/missing/cite_summary）算毕之后：00 第五节与
    # work/audit_report.md 同源同字段集（build_audit_report），仅 heading 取值不同。
    _g1b_clear = (
        (f"{gate1b['items']} 项（其中裸单字 {gate1b.get('bare_note', 0)} 条靠台账通改保证、不计），残留 {len(gate1b['residual'])} 项"
         + (f"（⚠️ 有残留：{gate1b['residual']}，按清单补改 03 并在 04 §四 留痕；本项不阻断）"
            if gate1b['residual'] else "（全部清零）"))
        if gate1b['status'] in ("pass", "fail") else
        ("未提供 error_forms.txt（本项跳过；如需此项，用 gen_error_forms.py 生成候选并人工确认后改名）"
         if gate1b['status'] == "missing" else "已用 --no-gate1b 省略")
    )
    _audit_fields = {
        # 如实报告（2026-10-18）：覆盖率原先不在任何报告里，「没读完」交付时不可见，
        # 且会被闸B 的「残留 0 处」伪装成健康。此处三态如实，无记录明写「未执行」。
        "reading_coverage": reading_coverage_summary(B),
        "gate0_anchor": ((f"引用 SEC {len(cited)} 个；幻觉锚点={'无' if not hallu else hallu}；"
                          f"权威 {len(ch)} 篇，缺失引用={[(c['no'], c['name']) for c in missing] if missing else '无（全覆盖）'}")
                         if cited else
                         "0 个引用锚点——**本项无样本**，检索与引用均不可判（不等于通过）"),
        "gate1_retrieval": (f"提供检索词 {gate1['terms']} 个，缺失 {len(gate1['miss'])} 个"
                            + (f"（{gate1['miss']}，多为凝练四字标签/带标点短语，原文分述其详，概念已在 01 笔记覆盖）"
                               if gate1['miss']
                               else ("（无检索词，本项跳过）" if not gate1['terms'] else "（全部可检索）"))),
        "gate1b_clear": _g1b_clear,
        "gate1b_direction": (f"{len(gate1b.get('direction', []))} 条告警（{gate1b['direction']}）"
                             if gate1b.get('direction')
                             else ("无告警（方向均合理）" if gate1b['status'] in ("pass", "fail")
                                   else "— **未执行**（错字清单未提供，本项无对象可查；不代表通过）")),
        "gate1b_bare": (f"{len(gate1b.get('bare_uncovered', []))} 条无台账 systematic 覆盖（漏检风险，建议改复合补丁）"
                        if gate1b.get('bare_uncovered')
                        else ("无" if gate1b['status'] in ("pass", "fail")
                              else "— **未执行**（错字清单未提供；不代表通过）")),
        "gate2_content": "本路径不自动做词典普查（报告项）；需要时跑 `ocr_confusion_dict.py` 全套扫描，或由 `check_book.py` 闸A 抽检页 × 混淆词典独立复核",
        "gate2_replay": (f"assemble 后回放 active 补丁 old 对 03 干净正文，真实残留 = {residual_n} 处 → "
                         + ("PASS（03 与 corrections 台账一致，无静默改动）" if residual_n == 0
                            else "⚠️ 残留>0：台账与 03 不一致，由 check_book 闸B 判处置（补改 03 / 修台账），不整本重算")),
        "gate3_completeness": (f"⚠️ {len(empty_chaps)} 篇 concept+要点+名句全空"
                               f" {[(c['no'], c['name']) for c in empty_chaps]}，02 静默缺章，请复核笔记是否严格按 §4 模板（思想书 concept 为 0 但名句非空属正常，不在此列）"
                               if empty_chaps else "全部篇章均有内容（无 concept+要点+名句全空章）"),
        "gate4_verbatim": cite_summary,
        "trace_ledger": (f"03 已为干净全文（正文不再内嵌〔〕留痕，§3.1）；本次应用 trace 型纠错 {applied_trace} 处 + "
                         f"系统噪声通改 {len(tongai.splitlines()) if tongai else 0} 类 + 转录层纠错 {applied_ocr} 处，"
                         f"已全量登记入 `{F03}` 同名 04_改错留痕.md（台账主源 + diff 兜底，§6.3）；"
                         f"页末残句标注：按约定不插入〔页末残句〕标记"),
        "pending_conflict": (f"{len(pending)} 处"
                             + ("（见 work/apply_report.md，须人工选边，Stage 4「assemble 应用报告规范」）"
                                if pending else "（无冲突）")),
        "patch_apply": ((_no_corr_note + "；") if _no_corr_note else "")
                       + (f"corrections/ 共 {len(corr_files)} 份；未命中 {n_miss} 处（报告项、不阻断；"
                          f"另 冗余 per-phys {n_redundant} / 无效 systematic 通改 {n_invalid_sys} / 回退成功 {n_fallback} 仅记录，见 correction_miss_log.md）"),
        "silent_delete": (f"{applied_silent} 处（有原图或 region_noise_log 可核）" if applied_silent else "0 处"),
        "zero_fabrication": (f"引文逐字 + SEC 锚 + 纠错留痕；{len(notes)} 份笔记合计约 {total_cite} 处 SEC 引文锚点；"
                             # 原为硬编码「均落入对应篇 SEC 区间」：0 锚点时是空真假话，
                             # 有越界锚时与 01 头部实测结论自相矛盾。改用实算结论。
                             + ("**本项无样本，不可判**（0 处引文锚点 ≠ 通过）" if total_cite == 0
                                else _anchor_claim)),
    }
    # 终端也打一行：师傅多半只看装配输出，不翻 work 目录。报告里写了不等于他看见了。
    print(f"精读覆盖率：{_audit_fields['reading_coverage']}")
    gate_report = build_audit_report("EPUB·SEC 体系", book, "assemble_epub.py", _audit_fields,
                                     heading=False, b2_warn=warn_block)
    hdr00 = (
        f"# 《{book}》梳理总览与目录对照（EPUB·SEC 锚点）\n\n"
        f"> **书名**：《{book}》\n> **作者/译注**：{author}"
        + (f"（{translator} 译注）" if translator else "")
        + (f"\n> **出版**：{publisher}，{year}" if publisher else "")
        + f"\n> **类型**：{btype}\n> **规模**：{meta.get('total_phys','?')} 个阅读序单元 / 约 {len(fulltext_clean)//10000} 万字符 / {len(ch)} 篇正文\n\n"
        f"## 一、锚点体系（关键）\n{pagemap}\n\n"
        f"## 二、目录 ↔ SEC 对照（{len(ch)} 篇）\n| 篇 | 篇名 | SEC idx |\n|---|---|---|\n"
        + "\n".join(f"| {esc_cell(c['no'])} | {esc_cell(c['name'])} | {esc_cell(c.get('phys_start'))} |" for c in ch)  # #24
        + f"\n\n## 三、全书总览\n{meta.get('overview','（见 01 章节详解）')}\n\n"
        f"## 四、标注与 OCR 规则\n{ocr_note}\n\n"
        f"## 五、设闸审计报告\n{gate_report}"
        f"- 概念/名句速查见 `{F02}`；原文检索见 `{F03}`（权威纠错底本）。\n"
    )
    safe_write(os.path.join(OUT, F00), hdr00)  # P0 #3
    print("00 完成")
    # audit_report.md 落盘：N3（2026-10-04）走 safe_write 原子写；与 00 第五节同源，仅 heading=True
    # 多出报告标题与书名引言——两路径 audit_report.md 字段集自此完全一致（2026-10-08 适配性整改）。
    safe_write(os.path.join(B, "audit_report.md"),
               build_audit_report("EPUB·SEC 体系", book, "assemble_epub.py", _audit_fields,
                                  heading=True, b2_warn=warn_block))

    # ---- 交付级全件错形复扫（2026-10-05 P0）：00/01/02/03 落盘后再扫一遍 ----
    # 错字清单复扫只扫 03 正文；01/02 由 notes 编译、00 由章名与统计汇总生成，都不在扫描空间里。
    # 实测：01/02 全篇保留已判定错形（「磨宋」「待臣」），02 的名句到 03 grep 命中 0，
    # 「可检索/可回溯」契约在派生件上断裂。故四件落盘后复扫：读入失败阻断（覆盖不可证），
    # 有残留只报告——真残留由 check_book 闸B 以「记录→书」方向判处置。
    if ef_items and not args.no_gate1b:
        _gi = scan_general_items(meta.get("type", ""))
        _fh, _skipped = final_error_form_scan(OUT, ef_items, general_items=_gi)
        if _skipped:
            print("❌ 交付级错形复扫：以下交付件读入失败，扫描覆盖不可证，拒交付：")
            for _fn, _why in _skipped:
                print(f"   {_fn}：{_why}")
            print("   处理：修复文件编码/权限后重跑 assemble（幂等）。")
            sys.exit(1)
        if _fh:
            print("⚠️ 交付级错形复扫：以下交付件仍含已判定的错误形态（报告项，不阻断）：")
            for _fn, _bad in _fh.items():
                print(f"   {_fn}：" + "、".join(f"「{w}」{n}处→「{r}」" for w, r, n in _bad))
            print("   处理：按清单补改 03 并在 04 §四 留痕，或同步 notes 后重出 01/02；"
                  "确认是正文讨论该错形本身（如校勘记）的，用 --no-gate1b 省略本项。")
        else:
            print(f"交付级错形复扫：00/01/02/03 全部 {len(ef_items)} 项错误形态清零 → 无残留")

    # B2 未命中告警：报告项，不阻断（与 PDF 路径同口径）。未命中多源于台账而非书，
    # 拦交付只会逼人绕过去；真实影响由 check_book 闸B 体现。
    if should_fail:
        print(f"⚠️ 补丁未命中 {n_miss} 处超过阈值 {args.miss_threshold}（报告项，不阻断）："
              f"按 correction_miss_log.md 逐条处置台账即可。")
    print("audit_report.md 已落盘")

    # P0 改造14：逃生口聚合告警——同时省略多道检查时汇总提醒并写入交付件（与 PDF 路径同款块对齐）。
    # 只统计确实起作用的两个旗；--no-fail 已无实际作用（未命中恒为报告项），不列入。
    _disabled = []
    if args.no_gate1b:
        _disabled.append("错字清单复扫(--no-gate1b)")
    if args.force_systematic:
        _disabled.append("单字通改护栏(--force-systematic)")
    if len(_disabled) >= 2:
        _agg = ("\n⚠️⚠️ **本次运行已禁用 %d 道质量闸**：%s。"
                "交付件质量未经完整校验，请人工复核后再入仓。\n" % (len(_disabled), " / ".join(_disabled)))
        print(_agg)
        with open(os.path.join(B, "audit_report.md"), "a", encoding="utf-8", newline="\n") as f:
            f.write(_agg)
        _p00 = os.path.join(OUT, F00)
        if os.path.isfile(_p00):
            with open(_p00, "a", encoding="utf-8", newline="\n") as f:
                f.write(_agg)

    # ---- 交付指纹（v1.34.0）：00/01/02/03 的 sha256 追加到 04 末尾 ----
    # 目的：check_book 据此分辨「assemble 的确定性产物」与「assemble 之后的手改」。
    # 04 自身不参与（自指无意义）。
    _fp = []
    for _k, _fn in (("00", F00), ("01", F01), ("02", F02), ("03", F03)):
        _p = os.path.join(OUT, _fn)
        if os.path.isfile(_p):
            _fp.append(f"{_k}=sha256:{sha256_file(_p)}")
    if _fp:
        with open(os.path.join(OUT, PREFIX + "04_改错留痕.md"), "a", encoding="utf-8", newline="\n") as _f:
            _f.write("\n## 五、交付指纹\n\n> " + "；".join(_fp) + "\n>\n"
                     "> 由 assemble 定稿前计算；`check_book.py` 逐件比对，不一致即说明这四件在"
                     "assemble 之后被改过（改动须写回 corrections 台账并重跑 assemble）。"
                     "04 自身不参与。\n")
        print("交付指纹：00/01/02/03 已记入 04（check_book 据此识别 assemble 后手改）")

    # ---- 定稿交付（2026-10-05 P1）：staging → 真实交付目录，整体切换 ----
    if os.path.isdir(REAL_OUT):
        # 旧交付件先归档到备份根（可恢复性：staging 每轮重建，safe_write 的覆盖保护
        # 无旧文件可触发，改由定稿时把上一轮交付件整体归档到 work/_bak）
        os.makedirs(os.path.join(B, "_bak"), exist_ok=True)
        for _fn in os.listdir(REAL_OUT):
            _src = os.path.join(REAL_OUT, _fn)
            if os.path.isfile(_src):
                shutil.move(_src, os.path.join(B, "_bak", _fn + ".prev.bak"))
        shutil.rmtree(REAL_OUT)
    shutil.move(OUT, REAL_OUT)
    print(f"装配定稿：{REAL_OUT}（五件 00–04 齐出；旧交付件已归档 work/_bak）")
    print("  书对不对由 check_book.py 判（闸A 可读性抽检出清单、闸B 书账互释与交付完整性为阻断项）；"
          f"本次各设闸审计结论见 {os.path.join(B, 'audit_report.md')}，均为报告项、不参与退出码。")

if __name__ == "__main__":
    main()
