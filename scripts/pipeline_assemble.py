#!/usr/bin/env python3
# pipeline_assemble.py —— Stage 4/6 组装
#   读取 fulltext_struct.txt + corrections/*.json（纠错补丁）-> 施加 -> fulltext_clean.txt -> <书名>03_带页码全文.md（权威纠错活底本）
#   设闸审计（写进 work/audit_report.md 与 00 第五节，**全是报告项、不参与退出码**）：
#     闸0 锚点有效性 / 闸1 检索可达性 / 错字清单复扫 / 闸2 内容正确性与补丁回放
#     / 闸3 02 完整性 / 闸4 引文逐字率（PDF 侧不自动统计）+ 交付级全件复扫
#   notes/*.md -> <书名>01_章节详解.md
#   自动抽取要点/概念/原句（类型自适应） -> <书名>02_脉络与速查卡.md
#   book_meta.json -> <书名>00_梳理总览与目录对照.md
#
# 退出码：0 = 五件齐出（00–04）并已定稿；1 = 装不出来（前置缺件 / 台账 schema ERROR / notes 为空或
#   解析不出章节 / meta 章名在 03 全不匹配 / manifest 与 04 生成失败）。
#   书对不对不归本脚本判——由交付前的 check_book.py 判（闸A 可读性抽检出清单、闸B 书账互释 +
#   交付完整性为阻断项）。本脚本只保证「有东西可交、格式干净」。
#   （下游判断「本次是否成功交付」以此为准，不得按交付目录文件存在性判断。）
#
# 用法：python pipeline_assemble.py --work <工作目录> --out <交付目录> --meta <book_meta.json> [--retrieval <检索清单.txt>]
#
# corrections/NN_corrections.json 条目：
#   {"phys":<N>,"old":"<错讹片段>","new":"<修正>","reason":"<为何>","systematic":false}
#   {"phys":"ALL","old":"儿何","new":"几何","reason":"系统噪声","systematic":true}
#   {"phys":<N>,"merge":true,"note":"<跨页合并说明>"}

import re, json, glob, os, argparse, sys, subprocess, shutil
from _common import (build_audit_report,             # 设闸审计报告统一构建（2026-10-08：与 EPUB 路径字段集对齐）
                     report_corrections_ledger,       # 分析①：corrections 台账为空的显式感知（打印强告警 + 返回 N/A 说明，2026-10-08）
                     miss_alarm,                      # PDF/EPUB 共用确定性匹配 + miss 两级告警（见 §6.3 / §6.1 / scripts/_common.py）
                     classify_miss,                  # P2 新-1：miss 分四类，仅 hard 计失败阈值
                     clean_note, strip_leaked_headers, # B2/B3：笔记清洗 + 漏剥页眉清理（原本地副本已删，统一走 _common）
                     sync_text_with_corrections,       # 派生件错形同步（01/02 与 03 一致性，2026-10-05 P0）
                     final_error_form_scan,            # 交付级全件错形复扫（00/01/02/03，2026-10-05 P0）
                     note_anchor_range_report,         # 01 头部区间断言按实算渲染（2026-10-05 P1）
                     reading_coverage_summary,         # 精读覆盖率如实结论（无记录须明写「未执行」，2026-10-18）
                     check_prereq_files,               # 前置文件硬校验（写任何交付件之前，2026-10-05 P0）
                     apply_corrections_core,          # R5：补丁施加核心（2026-09-08 抽单一真相源，本文件只留薄壳）
                     count_traces, strip_correction_traces, _page_blocks, # C1 留痕计数；strip_correction_traces=clean-03 单一真相源（v1.14.0）；_page_blocks=逐页定位（v1.15.3）
                     trace_balance,                   # 留痕定界自检（2026-09-22）：引述文本里的〔〕须转写为⟦⟧
                     safe_write,                      # P0 #3：交付件落盘（覆盖保护 + 原子写），勿再 open(...,"w", newline="\n").write()
                     read_text_cn, read_json_cn,      # #24：文本/JSON 读取（统一 utf-8-sig 容错）
                     esc_cell,                        # #24：表格单元格转义（勿再本地写 .replace("|","\\|")）
                     check_anchors,                   # #24：R4 锚点硬闸（原仅 EPUB 侧有，PDF 侧补上）
                     reset_staging_dir,               # 交付暂存区落点与清理（2026-10-14 抽单一真相源，PDF/EPUB 共用，避同步盘文件锁）
                     derive_prefix,                  # v1.15.1：交付件前缀=目录名（去_OCR/_文字版标注），五件统一
                     set_backup_root,                # 备份落点隔离：.bak 落 work 目录，不与交付五件并列
                     scan_confusion_dict,            # P0 改造1：OCR 混淆词典内容扫描（闸报告展示）
                     scan_general_items,             # 通用混淆条目（交付级复扫的独立基准，2026-10-17）
                     sha256_file,                    # 交付指纹（04 末尾记 00/01/02/03 摘要，2026-10-17）
                     build_gate1_terms, collect_systematic_olds,  # P1 改造8/改造7：闸1 检索词构造 / 台账 systematic old 集合
                     emit_apply_report, _FORM_NEAR_SET, is_narrative)  # v1.18.1 改造13 统一 apply-report；P1-4 收敛 is_narrative 到 _common

# ---------- 工具 ----------
# clean_note 已抽到 _common.py（B2，2026-09-05）：此前本文件与 assemble_epub.py 各存一份
# 逐字相同的副本（相似度 1.000）。统一走 _common.clean_note，默认参数 = 本文件原行为。
# 切勿在此文件再定义同名函数，否则与 EPUB 路径双路径漂移。

# ---------- 02 速查卡：类型自适应（与 assemble_epub.py 对齐）----------
# 叙事型书（①事件/战役、③人物/生平、④制度/沿革、⑤地理/空间、⑥通史/断代、⑧编年/纪事、⑩文献选编）
#   → 02 显示「要点+名句」，第三节标题用「关键要点速查」
# 非叙事型书（②思想/理论、⑦方法论/史学、⑨带注古籍）
#   → 02 显示「概念+名句」，第三节标题用「核心概念与原句速查」
# NARRATIVE_TYPE_PREFIX / is_narrative 已收敛到 _common（2026-10-04 审查 P1-4）

def extract_events(notes):
    """从各章笔记抽取『关键要点/概念/原句』条目（逐条带原书页锚），类型自适应。
    返回 [{"no":int,"name":str,"events":[(entry,page,section)]}]，按章顺序。
    笔记结构：## N. 章名（时期） 或 ## 六、章名（时期）下分 **小节** 标题，其下列 - 条目（原书页 N）。
    兼容阿拉伯数字序号（## 6.）与中文数字序号（## 六、 / ## 第六章）。
    另兼容「无章号」笔记（如战史/史书单卷拆分后的 `## 关键作战与事件` / `## 基本经验之N：…` 二级标题）：
    以含标记词的 `## X` 二级标题作为小节，条目归入「最近出现的章节标题」；若无任何章标题则归入单章。"""
    CAPTURE = ["战争", "战役", "转折", "技术", "体制", "战术", "变革", "思想", "人物", "概念", "要点", "论题",
               # 2026-09-18 补：② 思想/理论型笔记的「关键原句摘录」「篇间逻辑关系」两节此前不在捕获表内，
               # 导致 02 速查卡漏掉最可引用的原句与篇间脉络（理论型书 5 节只进 3 节）。补后两路径同源。
               "摘录", "逻辑",
               # 2026-10-04 补（v1.16.2，Fix 6）：④ 制度/沿革型书的笔记小节（沿革脉络/机构职官/俸禄/选任等）
               # 此前不在捕获表内，导致中/高层这类书的 02 速查卡漏掉最该直查的条目。现统一补入（与 H2_SEC_MARK 同源）。
               "沿革", "职官", "机构", "俸禄", "选任", "源流", "设置",
               # v1.32.1 补：「几点认识」是 §4 事件/战役型①的法定小节（核心观点 + 关键引句），
               # 原词表不含「认识」，该节整体并入上一捕获小节「过程要点」，02 里作者判断被
               # 当成作战经过呈现（实测本书 19 章全部如此）。补「认识」使其独立成组。
               "认识"]
    # 非捕获词小节的「排除表」：属说明/旁注性质，其下条目不计入 02。
    # 未命中此表又非捕获词的小节（如 `**一、xxx**` 条目标题）则沿用上一捕获词小节。
    SEC_DROP = ["概况", "说明", "提示", "凡例", "读法", "底本", "自检", "结构", "校勘",
                "目录", "索引", "注释", "出处", "附录"]
    # 2026-09-25 补（通典支）：原 H2_SEC_MARK 与 CAPTURE 是两套各自维护的词表，
    # 长期不同步——`## 论题`／`## 可引用原句`／`## 存疑待核` 在 H2 形态下整节漏抽，
    # 而同样的词写成 `**论题**` 却能抽到。改为以 CAPTURE 为基、并入原 H2 专有词，
    # 两种标题形态共用一套判据。
    H2_SEC_MARK = list(dict.fromkeys(
        CAPTURE + ["作战", "事件", "决策", "经验", "制度", "建设", "编写", "出版"]))
    CN = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10,"百":100,"千":1000}
    def cn2int(s):
        tot=0; cur=0
        for ch in s:
            if ch=="十": tot += (cur if cur else 1)*10; cur=0
            elif ch=="百": tot += (cur if cur else 1)*100; cur=0
            elif ch=="千": tot += (cur if cur else 1)*1000; cur=0
            else: cur = CN.get(ch,0)
        return tot+cur
    def parse_ch(l):
        m = re.match(r"^##\s+(\d+)\.\s+(.+?)\s*$", l)
        if m: return int(m.group(1)), m.group(2).strip()
        m = re.match(r"^##\s+第?\s*([一二三四五六七八九十百千]+)[、章]\s*(.+?)\s*$", l)
        if m: return cn2int(m.group(1)), m.group(2).strip()
        return None
    chapters = []
    for f in notes:
        # 2026-10-05 P0（复审）：支持 (fname, text) 元组——02 速查卡抽取须与 01 用同一份
        # 同步后文本（_synced_by_file），否则 notes 援引的未修正错形在 02 复活成孤岛，
        # 交付级复扫整单拒付，PDF 路径标准场景无法出件。
        if isinstance(f, tuple):
            f, t = f
        else:
            t = open(f, encoding="utf-8").read()
        cur = None
        cur_sec = None
        for l in t.splitlines():
            pc = parse_ch(l)
            if pc:
                cur = {"no": pc[0], "name": pc[1], "events": []}
                chapters.append(cur)
                cur_sec = None
                continue
            # **小节** 三级标题
            # 2026-09-25 补（通典支）：原逻辑「不含捕获词即置 None」会把**条目标题型小节**
            # （如 `**一、黃帝之號**`）整段打断，其下的 `> 引文` 引用块随之全部丢弃——
            # 而那正是逐字带锚、最需按页直查的内容。改为三分：
            #   含捕获词 → 开新小节；命中排除词（概况/说明/底本…） → 置 None；
            #   其余（条目标题）→ 沿用上一捕获词小节，其下引用块归为该条目内容。
            msec = re.match(r"^\*\*\s*(.+?)\s*\*\*", l)
            if msec and cur is not None:
                sec = msec.group(1)
                if any(k in sec for k in CAPTURE):
                    cur_sec = sec
                elif any(k in sec for k in SEC_DROP):
                    cur_sec = None
                continue
            # `## X` 二级标题（Vol3 无章号格式）：含标记词则视为小节；不含则视为"分隔说明"，不打断 cur_sec
            mh2 = re.match(r"^##\s+(.+?)\s*$", l)
            if mh2 and cur is not None:
                sec = mh2.group(1)
                if any(k in sec for k in H2_SEC_MARK):
                    # 🟡13（2026-09-08 剩余批）：原 `sec if any(CAPTURE) else sec` 两分支恒等，
                    # 属笔误遗留——等价简化为 sec（行为不变）。若原意是"非捕获词小节不打标"，
                    # 应改为 else None，属行为变更需另行裁定。
                    cur_sec = sec
                else:
                    cur_sec = None  # 概况/分级标注说明等，不捕获其下条目
                continue
            # 无章号笔记（仅 `## X` 无 `## N.`）：以文件名为隐式单章
            if mh2 and cur is None:
                sec = mh2.group(1)
                fbase = re.sub(r"^\d+[_-]?\s*", "", os.path.splitext(os.path.basename(f))[0]).strip()
                # 文件名为纯序号时，回退到笔记首个 `## X` 标题作为章名
                if not fbase or fbase.isdigit():
                    fbase = sec
                cur = {"no": len(chapters) + 1, "name": fbase or "未命名", "events": []}
                chapters.append(cur)
                if any(k in sec for k in H2_SEC_MARK):
                    cur_sec = sec  # 🟡13：原两分支恒等的三元已等价简化（同上）
                else:
                    cur_sec = None
                continue
            # 2026-09-18 补：`>` 引用行也计入（② 思想/理论型笔记的「关键原句摘录」用引用块而非
            # 子弹列出原句，此前整节进不了 02 速查卡——而原句恰是最需按页直查的条目）。
            # 2026-09-25 放宽：引用块不再限于「摘录」小节——凡当前处于捕获词小节内，
            # `> ` 行即作为条目收（引用块常是逐字带锚的原句，正是 02 需要直查的内容；
            # 未带锚者 page 留空，仍保留文本价值）。
            mbul = re.match(r"^\s*[-*]\s+(.+?)\s*$", l) or re.match(r"^\s*>\s*(.+?)\s*$", l)
            if mbul and cur is not None and cur_sec is not None:
                txt = mbul.group(1).strip()
                # 页码：优先行尾（原书页 N）。(或,或；)后缀；其次行内首个（原书页 N）
                # 2026-09-25 补：古籍支（meta 给 juan_map/vol_map、不设原书印页）的笔记条目
                # 行尾写的是 `<<<PAGE phys=N juan=J vol=V>>>`，原两条正则全部落空，
                # 导致 02 速查卡整节丢失。此处一并识别，page 存 phys 号。
                # v1.32.1：页码支持区间「（原书页码 12–15）」。此前只认单个 \d+，
                # 笔记按区间标注时 mpage 全部落空、02「定位」列整列变「—」（实测
                # 中国军事通史_第8卷 279 行、附卷年表 438 行因此空白）。
                _PGN = r"(\d+(?:\s*[–—~-]\s*\d+)?)"
                mpage = re.search(r"[（(]\s*原书页[码]?\s*" + _PGN + r"\s*[)）]\s*[。，,；;]?\s*$", txt) \
                        or re.search(r"[（(]\s*原书页[码]?\s*" + _PGN + r"\s*[)）]", txt) \
                        or re.search(r"[（(]\s*SEC idx=\s*" + _PGN + r"\s*[)）]\s*[。，,；;]?\s*$", txt) \
                        or re.search(r"[（(]\s*SEC idx=\s*" + _PGN + r"\s*[)）]", txt) \
                        or re.search(r"<<<PAGE phys=(\d+)(?:\s+juan=\d+(?:\s+vol=\d+)?)?>>>"
                                     r"\s*[。，,；;]?\s*$", txt) \
                        or re.search(r"<<<PAGE phys=(\d+)(?:\s+juan=\d+(?:\s+vol=\d+)?)?>>>", txt)
                page = mpage.group(1) if mpage else ""
                page = re.sub(r"\s*([–—~-])\s*", "–", page)  # 区间号统一为 en dash 无空格
                entry = re.sub(r"\s*[（(]\s*原书页[码]?\s*\d+(?:\s*[–—~-]\s*\d+)?\s*[)）].*$", "", txt).strip()
                entry = re.sub(r"\s*[（(]\s*SEC idx=\s*\d+(?:\s*[–—~-]\s*\d+)?\s*[)）].*$", "", entry).strip()
                entry = re.sub(r"\s*<<<PAGE phys=\d+(?:\s+juan=\d+(?:\s+vol=\d+)?)?>>>.*$",
                               "", entry).strip()
                if entry:
                    cur["events"].append((entry, page, cur_sec))
    return chapters

# 表格单元格转义（🟡15，2026-09-08 剩余批）：条目含 `|` 会截断 Markdown 表格，统一转义。
def _esc_cell(v):
    # #24 横切收敛：实现已收进 _common.esc_cell（相对原版增加换行归整）。
    # 保留本别名仅因调用点较多，勿在此处再写第二份实现。
    return esc_cell(v)

# ---------- 漏剥页眉清理：页首"第X章/节/编…"运行书眉（OCR 歪导致 01b 漏剥）----------
# strip_leaked_headers 已抽到 _common.py（B3，2026-09-05），锚正则参数化后 PDF/EPUB 共用一份；
# 原本地副本已删，改由文件头 import 提供。
# 切勿在此文件再定义同名函数。

# ---------- 施加纠错补丁 ----------
def apply_corrections(struct_text, corr_files, force_systematic=False,
                      juan_map=None, vol_map=None, work_dir=None):
    """PDF 路径薄壳（R5，2026-09-08）：锚解析 + 回写序列化在此，施加逻辑统一走
    _common.apply_corrections_core（单一真相源，勿在此再实现施加细节）。
    签名与 8 元组返回保持不变（黄金值契约；第 7 位 applied_silent 为静默施用留痕、第 8 位 applied_ocr 为转录层台账纠错数，2026-10-01）；frag_count 恒 0（页末残句标注已按老陈
    2026-09-05 裁定默认关闭，原死代码块随之移除，行为无变化）。
    force_systematic：🟡10 单字通改护栏放行开关。
    work_dir：v1.14.0 P0-1——传给 apply_corrections_core，过滤 patch_manifest 中 status=deleted 的补丁。"""
    # 解析为有序块（book=任意串：PDF struct 的 book 字段不做取值假设，黄金用例含 book=B）
    blocks = []  # [phys, book, title(恒空), body]
    for m in re.finditer(r"<<<PAGE phys=(\d+) book=([^>]*)>>>\n(.*?)(?=\n<<<PAGE|\Z)", struct_text, re.S):
        blocks.append([int(m.group(1)), m.group(2), "", m.group(3)])
    miss, pending, applied_trace, tongai, applied_silent, applied_ocr = apply_corrections_core(
        blocks, corr_files, merge_label="物理页", force_systematic=force_systematic, work_dir=work_dir)
    # 锚形可切换：若 meta 给出 juan_map / vol_map，则锚改用
    # `<<<PAGE phys=N juan=J vol=V>>>`（用于原书印页不可考、改以卷/册为主坐标的
    # 古籍）。未给则维持 `<<<PAGE phys=N book=M>>>` 旧形，行为与从前一致。
    _jm, _vm = juan_map or {}, vol_map or {}
    if _jm and _vm:
        def _anchor(p, b):
            return (f"<<<PAGE phys={p} juan={_jm[str(p)]} vol={_vm[str(p)]}>>>"
                    if str(p) in _jm and str(p) in _vm
                    else f"<<<PAGE phys={p} book={b}>>>")
        fulltext = "\n".join(f"{_anchor(b[0], b[1])}\n{b[3]}" for b in blocks)
    else:
        fulltext = "\n".join(f"<<<PAGE phys={b[0]} book={b[1]}>>>\n{b[3]}" for b in blocks)
    frag_count = 0
    return fulltext, tongai, miss, pending, frag_count, applied_trace, applied_silent, applied_ocr

# ---------- v1.14.0：组装应用报告（Stage 4「assemble 应用报告规范」 / 动作6）----------
def auto_retrieval_terms(meta):
    """闸1 无外部清单时的兜底检索词：从 book_meta 篇名抽 ≥4 字实词片段（去书名号/括号后按标点切）。

    篇名是 03 正文 `# ` 标题原文；若 OCR 把篇名某字读错，该片段在 03 搜不到即暴露漏改——这正是闸1 价值。
    整段篇名切出的长片段在 03 必然出现（标题即原文），仅 OCR 错讹处才 miss，故 gate 默认全过、错讹可见。
    """
    terms, seen = [], set()
    for ch in meta.get("chapters", []):
        name = ch.get("name", "")
        name = re.sub(r"[〈〉《》「」『』“”\"'（）()\[\]【】]", " ", name)
        for seg in re.split(r"[、，。；：\s／/]+", name):
            seg = seg.strip()
            if len(seg) >= 4 and seg not in seen:
                seen.add(seg)
                terms.append(seg)
    return terms


# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--retrieval", default="")
    ap.add_argument("--error-forms", default="",
                    help="错字清单复扫用的错形清单（每行 错误形态|正确形态|判定依据，# 注释）；与闸1（正确可达）互补。"
                         "参数可省（默认探测 work/error_forms.txt）；清单缺失或为空则本项跳过并记入报告，不阻断交付")
    ap.add_argument("--no-fail", action="store_true",
                    help="历史兼容参数：补丁未命中属报告项，已恒不阻断，本旗无实际作用，保留仅为兼容既有命令行。")
    ap.add_argument("--miss-threshold", type=int, default=10,
                    help="补丁未命中的告警阈值：超过则在 00 插 ⚠️ 块并点名（报告项，不阻断、不改退出码）")
    ap.add_argument("--tolerate-miss", action="store_true",
                    help="历史兼容参数：同 --no-fail（未命中已恒不阻断），保留仅为兼容既有命令行。")
    ap.add_argument("--force-systematic", action="store_true",
                    help="🟡10 护栏放行：允许单字 systematic 通改（默认拦截——单字通改误标即全书静默损坏）")
    ap.add_argument("--no-gate1b", action="store_true",
                    help="跳过错字清单复扫（本项已是报告项，默认不阻断；此旗仅省略扫描）")
    args = ap.parse_args()
    B, OUT = args.work, args.out
    os.makedirs(OUT, exist_ok=True)
    # 备份隔离：交付目录只应含五件（00–04），旧件备份一律落 work 目录，避免幂等重跑持续堆积
    set_backup_root(os.path.join(B, "_bak"))
    # 缺陷件不落盘（2026-10-05 P1，复审实测确认）：全部交付件先写**系统临时暂存区**
    # （落点与清理见 `_common.reset_staging_dir`——避开 OneDrive/同步盘的文件锁），
    # main 末尾才整体定稿到真实交付目录。中途 sys.exit（前置缺件 / 台账 schema ERROR /
    # notes 为空或解析不出章节 / meta 章名全不匹配 / manifest 与 04 生成失败）时，
    # 真实交付目录不被触碰——上次成功交付不被破坏。
    REAL_OUT = OUT
    _staging = reset_staging_dir(B)
    OUT = _staging
    PREFIX = derive_prefix(REAL_OUT)  # v1.15.1：前缀=真实交付目录名（staging 重定向不影响命名）
    meta = read_json_cn(args.meta)  # #24：统一 utf-8-sig 容错（原裸 json.load，带 BOM 的 meta 会炸）
    ch = meta["chapters"]

    # v1.33.0：source 路径一致性校验——防止 03 头部「来源」字段写错路径（如 OCR-PDF 书写成「路径三」）。
    # 通过 work 目录特征文件推断实际路径，与 meta.source 比对；不一致报强告警（不阻断，属元数据问题）。
    _src = meta.get("source", "") or ""
    _actual_path = "unknown"
    # 路径三特征：book_ocr 三件套转换后会留 _版式区块.md 或 book_ocr 相关文件
    if any(os.path.isfile(os.path.join(B, f)) for f in ["_版式区块.md", "book_ocr_blocks.jsonl", "book_ocr_meta.json"]):
        _actual_path = "路径三"
    # EPUB 特征：epub 解包目录
    elif os.path.isdir(os.path.join(B, "epub_unzipped")):
        _actual_path = "EPUB"
    else:
        _actual_path = "OCR-PDF"
    _path_keywords = {
        "OCR-PDF": ["OCR-PDF", "01a_raw", "文字层", "PDF路径"],
        "EPUB": ["EPUB", "epub", "01b_extract"],
        "路径三": ["路径三", "PaddleOCR", "book_ocr", "三件套", "00_ingest"],
    }
    _expected = _path_keywords.get(_actual_path, [])
    if _expected and not any(k in _src for k in _expected):
        print(f"⚠️ source 路径一致性告警：meta.source=\"{_src[:60]}\" 与实际路径「{_actual_path}」不匹配"
              f"（期望含 {_expected}）；03 头部来源字段将写错，请修正 book_meta.json 的 source 字段。")

    # 前置缺失硬校验（2026-10-05 P0：写任何交付件之前做，与 EPUB 路径同口径）
    check_prereq_files(B, ["fulltext_struct.txt"])
    struct = read_text_cn(os.path.join(B, "fulltext_struct.txt"))  # #24：单一入口
    # 自动补全每章的 phys_end / book_start / book_end（2026-10-04 修复：book_meta 惯例只填 phys_start，
    # 导致 00 目录对照表的结束页全为"？"。现从 struct 锚点推导 phys→book 映射，自动计算上下界）。
    _phys_to_book = {}
    for _m in re.finditer(r'<<<PAGE phys=(\d+)(?:\s+book=(\d+|None))?', struct):
        _p = int(_m.group(1)); _b = _m.group(2)
        if _b and _b != 'None':
            _phys_to_book[_p] = int(_b)
    _total_phys = max(_phys_to_book.keys()) if _phys_to_book else 0
    for _i, _c in enumerate(ch):
        _ps = _c.get('phys_start')
        if _ps is not None:
            _c.setdefault('book_start', _phys_to_book.get(_ps, '？'))
            _pe = (ch[_i+1].get('phys_start', _total_phys) - 1) if (_i < len(ch)-1) else _total_phys
            _c.setdefault('phys_end', _pe)
            _c.setdefault('book_end', _phys_to_book.get(_pe, '？'))
    corr_files = sorted(glob.glob(os.path.join(B, "corrections", "*.json")))
    # 2026-10-08（分析①）：台账为空的显式感知（两路径同源 _common.report_corrections_ledger）——
    # 打印强告警并在 audit_report「补丁施用」落 N/A 说明，免得「真漏台账」与「首轮无台账」不可区分。
    _no_corr_note = report_corrections_ledger(corr_files)
    # ---- 台账校验（check_ledger，§6.1.3 闸前移；2026-10-18 取代 validate_corrections + check_patch）----
    # 只拦「会让施加器崩溃」的 schema ERROR（缺 new 键 → KeyError，脏数据静默混入 03）。
    # 可落地性问题（补丁 old 对不上正文 / 0 命中 / 多命中 / 加字通改误伤）**只报告不阻断**：
    # 那是台账要修的事，把整本书拦下来只会逼人想办法让报错闭嘴。
    # 历史教训：grounding 做成硬闸后，一批书积压上千条错误，执行者为了交付只能绕过它。
    # 书本身好不好，由交付前的 check_book（闸A/闸B）判定，不在这里判。
    _sd = os.path.dirname(os.path.abspath(__file__))
    _rc_ledger = subprocess.run([sys.executable, os.path.join(_sd, "check_ledger.py"), B, "--strict"],
                                check=False).returncode
    if _rc_ledger != 0:
        print("❌ 台账 schema ERROR（缺 new 键等会让施加器崩溃），须先修台账再 assemble。")
        sys.exit(1)
    fulltext_clean, tongai, miss, pending, frag_count, applied_trace, applied_silent, applied_ocr = apply_corrections(
        struct, corr_files, force_systematic=args.force_systematic,
        juan_map=meta.get("juan_map"), vol_map=meta.get("vol_map"), work_dir=B)
    # 漏剥页眉清理（01b 因 OCR 歪/频率阈值漏剥的"第X章/节"行）
    fulltext_clean, hdr_stripped = strip_leaked_headers(fulltext_clean)
    # 把 miss 落盘备查（2026-10-08 适配性修复：改走 safe_write，与 EPUB 路径 assemble_epub.py 的 safe_write 落盘同口径——
    # 原裸 open 无原子写/覆盖保护，幂等重跑直接覆盖、崩溃留半截）
    safe_write(os.path.join(B, "correction_miss_log.md"), "\n".join(miss) or "（无未命中）")
    # 🟡6（2026-09-08）：剔除「回退成功」类条目后的真实未命中数——告警/统计/退出判定一律用 n_miss；
    # miss 全量仍落 correction_miss_log.md 备查（回退成功属留痕信息，非失败）。
    # P2 新-1：n_miss 只计 hard（真实内容风险：全文未找到 / phys 不在全文 / 脏值 / 正则非法 /
    # 系统替换残留）；冗余 per-phys、无效 systematic 通改、护栏拦截、重叠均不计入失败阈值，
    # 避免良性 miss 把 assemble 顶到 rc=1 反而掩盖真漏改。各类别仍落日志备查。
    n_miss_hard, n_redundant, n_invalid_sys = classify_miss(miss)
    n_miss = n_miss_hard  # 阈值 / 退出判定沿用 n_miss（=hard）
    n_fallback = sum(1 for m in miss if "回退成功" in m)
    # R4 锚点硬闸（#24）：原仅 EPUB 侧有，PDF 侧完全缺失 → struct 空/锚格式异常时
    # 静默产出空或半截的 03。两条路径现共用 _common.check_anchors（写任何交付件之前）。
    struct_anchors, anchor = check_anchors(struct, fulltext_clean)
    traces = count_traces(fulltext_clean)  # P0 #1（2026-09-12）：三族留痕全计入；原内联公式漏掉删除型〔原书OCR衍…〕，合法删字被误报为静默改动
    # 留痕定界自检（2026-09-22）：软告警，不阻断。引述文本若自带 〔〕（古籍题注），留痕定界
    # 会被内层括号破坏、下游剥不干净；生成侧已由 _common.esc_trace_text 转写为 ⟦⟧，此处复查。
    n_nest, n_unbal, nest_samples = trace_balance(fulltext_clean)
    if n_nest or n_unbal:
        print(f"⚠️ 留痕定界自检：疑似嵌套 {n_nest} 处、〔〕计数差 {n_unbal}（样例：{nest_samples[:2]}）")
    print(f"（漏剥页眉清理：剥除 {hdr_stripped} 行）")

    # ---- B2 两级告警 / C1 补丁生效审计（前置计算，结果在 00 报告 + 末尾退出使用）----
        # ---- B2 两级告警（保留）；C1 补丁生效审计已并入闸2（§6.1 / §6.1.3）----
    warn_block, should_fail = miss_alarm(n_miss, args.miss_threshold)
    if should_fail:
        print(f"⚠️ 补丁未命中 {n_miss} 处（>{args.miss_threshold} 阈值，仅计真实内容风险）："
              f"真实风险 {n_miss} / 冗余 per-phys {n_redundant} / 无效通改 {n_invalid_sys} / 回退成功 {n_fallback}，"
              f"属报告项、不阻断交付；逐条处置台账见 correction_miss_log.md")
    # 书账是否互释由交付前的 check_book 闸B 判定（§6.1.2），此处仅保留信息打印。
    if applied_silent:
        print(f"（静默删除 {applied_silent} 处：结构性噪音，有原图/region_noise_log 可核）")
    if applied_ocr:
        print(f"（转录层纠错 {applied_ocr} 处：错在我方 OCR，已施加进 03 正文、不留〔〕记号，§3.1；完整台账见 04_改错留痕.md）")
    # ---- 03 ----
    # v1.14.0：03 头部不写通改清单（通改登记入 04 台账），tongai_block 死代码已删。
    pending_block = ""
    if pending:
        rows = "\n".join(f"> | {_esc_cell(old)} | {_esc_cell(cands)} |" for old, cands in pending)  # 🟡15 转义 |
        pending_block = (
            f"\n> **存疑待核（{len(pending)} 处，需手动核实）**：\n"
            f"> | 原文 | 候选 |\n> |---|---|---|\n"
            f"{rows}\n"
        )
    ocr_block = (f"\n> **转录层纠错（{applied_ocr} 处）**：逐处 OCR 纠错**已施加进 03 正文、不留〔〕记号**"
                 f"（§3.1：转录层写「原书作」会冤枉古籍——我方 OCR 错讹不应被记为原书之误）；完整台账见 `{PREFIX}04_改错留痕.md`。\n" if applied_ocr else "")
    fulltext_clean_stripped = strip_correction_traces(fulltext_clean)
    hdr03 = (
        f"# 《{meta['book']}》· 带页码全文（权威纠错底本）\n\n"
        f"> **来源**：{meta.get('source','')}\n"
        f"> **作者**：{meta.get('author','')}"
        + (f"（{meta.get('translator','')} 译）" if meta.get('translator') else "")
        + (f"\n> **出版**：{meta.get('publisher','')}，{meta.get('year','')}" if meta.get('publisher') else "")
        + f"\n> **类型**：{meta.get('type','')}\n"
        f"> **规模**：{meta.get('total_phys','?')} 物理页 / 约 {len(fulltext_clean_stripped)//10000} 万字符 / {len(ch)} 章\n"
        f"> **页码体系**：{meta.get('pagemap_desc','')}\n"
        f"> **OCR / 通改规则**：{meta.get('ocr_note','正文逐字；非平凡 OCR 错字见 04_改错留痕.md（台账主源）。')}\n"
        f"> **锚点**：{meta.get('anchor_desc', '每页 `<<<PAGE phys=N book=M>>>` 可 grep 定位原文。引用请以（原书页 M）为准。')}\n"
        f"> **本文件性质**：本技能『唯一权威纠错底本』——03 为干净可引用/可检索全文，**正文不再内嵌〔〕留痕**；"
        f"所有纠错（含 systematic 通改、转录层纠错）已全量应用并登记入 `{PREFIX}04_改错留痕.md`（台账主源 + diff 兜底，§6.3），"
        f"由闸B 书账互释双向校验（§6.1.2）。\n\n---\n\n"
    )
    safe_write(os.path.join(OUT, PREFIX + "03_带页码全文.md"), hdr03 + fulltext_clean_stripped)  # P0 #3：覆盖保护 + 原子写
    safe_write(os.path.join(B, "fulltext_clean.txt"), fulltext_clean_stripped)  # N3（2026-10-04 审查）：走 safe_write 原子写
    print(f"03 完成：{anchor} 锚点，{len(fulltext_clean_stripped)} 字符（干净正文，留痕见 04_改错留痕.md），"
          f"补丁未命中 {n_miss}（真实风险；另 冗余 per-phys {n_redundant} / 无效通改 {n_invalid_sys} / 回退成功 {n_fallback}），存疑待核 {len(pending)}")

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
    # P0 改造1：pipeline 侧独立算词典内容扫描（与 verifier 同源 _common.scan_confusion_dict），
    # 用于 00 审计报告展示——交付件本身记录「内容正确性」结果，而非仅依赖 verifier 退出码。
    # v1.33.0：传入 meta.proper_names 白名单，避免地名/专名（如「四盘磨」）被 ctx 正则误报。
    _wl = meta.get("proper_names", []) or []
    dict_hits = scan_confusion_dict(fulltext_clean_stripped, meta.get("type", ""), whitelist=_wl)
    if dict_hits is None:
        # N10（2026-10-04 审查）：内容正确性扫描未执行，00 审计报告内容正确性项不可信。
        print("⚠️ 混淆词典内容扫描未执行（异常），00 报告的内容正确性项不可信，须人工补扫。")
    # ---- 交付前自检：check_book 闸A/闸B（§6.1），**只报告不阻断** ----
    # 这里以前是 pre_delivery_verifier 硬闸（residual>0 即拒交付）。问题在于 residual 的来源
    # 往往是台账而非书：补丁没生效、old 写成通改前形态、phys 记错页。硬闸一拦，执行者只剩
    # 「改台账→重跑」一条路，而重跑会用坏账覆盖好书。故改为生成时如实报告；
    # 书行不行由 check_book 独立判定，它给的处置是「定点改 03 / 改台账」，不是「整本重算」。
    subprocess.run([sys.executable, os.path.join(_sd, "check_book.py"),
                    "--work", B, "--out", OUT, "--meta", args.meta,
                    "--prefix", PREFIX, "--no-delivery"], check=False)
    # ---- notes 编译（提前至闸1 之前；🟡11 行为对齐 2026-09-08 剩余批）----
    # EPUB 侧闸1 检索空间含编译笔记（凝练标签/带标点短语在 01 笔记可达），PDF 侧旧版不含，
    # 同一份检索清单两路径结论会不同（审查报告 🟡11 行为漂移）。现对齐：检索空间 = 03 正文
    # + 03 头部书目行 + 编译笔记。
    notes = sorted(glob.glob(os.path.join(B, "notes", "*.md")))
    # ---- 派生件错形同步（2026-10-05 P0）：notes 写于台账定稿前，援引未修正原文；
    # 不同步则 01/02 留着已判定的错形，02 名句去 03 grep 命中 0，「可检索/可回溯」断裂，
    # 而闸1b/闸2 只扫 03、闸4 以 raw∪clean 为底本，全闸无感（实测假通过）。
    # 用同一份已裁定台账同步，改动逐条落 work/derived_sync_log.json → 04 改错留痕 §四。
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
    notes_text = "\n".join(compiled)   # 与 EPUB 侧 assemble_epub.py 同口径（🟡11 行为对齐）

    # ---- v1.16.2 装配前硬校验（Fix 5）：meta / notes 与 03 一致性，错误须阻断，不静默渲染"？" ----
    if not notes:
        print("❌ 装配前硬校验失败：notes/ 为空，无法生成 01（须先由分章 Agent 产出笔记）。")
        sys.exit(1)
    chapters_ev = extract_events([(f, _synced_by_file.get(f) or clean_note(open(f, encoding="utf-8").read(), f)) for f in notes])
    if not chapters_ev:
        print("❌ 装配前硬校验失败：notes 编译后未解析出任何章节（笔记格式不符 §4 模板），01 将空白，须修正笔记。")
        sys.exit(1)
    # meta 全部章名在 03 正文均未出现 → meta 章节名写错（高层旧 case：章名对不上真书），整套 00/01/02 建立在幻影目录上。
    # P2-C1/C2（v1.27.0）：大事记体自动识别与兼容——章名含年份（如"1920 年"）或"附录"时，
    # 匹配容忍空格/前缀（"1920 年"匹配"1920年"或正文标题"## 1920 年"）。
    def _name_match(name, text):
        """章名匹配：精确匹配 + 大事记体兼容（去空格后子串匹配）。"""
        if name in text:
            return True
        # 大事记体兼容：去空格后匹配（"1920 年"→"1920年"）
        name_nospace = re.sub(r"\s+", "", name)
        if name_nospace in re.sub(r"\s+", "", text):
            return True
        # 年份型章名：提取年份数字，在正文中找"## YYYY"或"YYYY年"标题
        year_match = re.search(r"(\d{4})", name)
        if year_match:
            year = year_match.group(1)
            if re.search(rf"#{{1,3}}\s*{year}\s*年", text) or f"{year}年" in text:
                return True
        return False

    def _is_chronicle(chapters):
        """P2-C1：自动识别大事记体——≥3章章名含4位年份，或含"附录"且章名非"第N章"格式。"""
        year_chapters = sum(1 for c in chapters if c.get("name") and re.search(r"\d{4}\s*年", c["name"]))
        has_appendix = any(c.get("name") and "附录" in c["name"] for c in chapters)
        no_chapter_prefix = all(c.get("name") and not c["name"].startswith("第") for c in chapters if c.get("name"))
        return (year_chapters >= 3) or (has_appendix and no_chapter_prefix)

    is_chronicle_book = _is_chronicle(ch) if ch else False
    if ch:
        meta_in_03 = [c["name"] for c in ch if c.get("name") and _name_match(c["name"], fulltext_clean_stripped)]
        if not meta_in_03:
            miss_names = [c["name"] for c in ch if c.get("name")]
            # P1-F2（v1.27.0）：章名不匹配时给模糊候选建议（从03正文提取 ## 标题，difflib 相似度排序）
            import difflib
            heading_candidates = re.findall(r"^#{1,3}\s+(.+)$", fulltext_clean_stripped, re.M)
            heading_candidates = [h.strip() for h in heading_candidates if len(h.strip()) >= 2]
            suggestion_lines = []
            for mn in miss_names[:5]:
                close = difflib.get_close_matches(mn, heading_candidates, n=3, cutoff=0.4)
                if close:
                    suggestion_lines.append(f"  「{mn}」→ 候选：{' / '.join(close)}")
            suggestion_text = "\n" + "\n".join(suggestion_lines) if suggestion_lines else ""
            print(f"❌ 装配前硬校验失败：book_meta 全部 {len(ch)} 章章名在 03 正文均未出现，疑似 meta 章节名错误：{miss_names[:5]}（须修正 book_meta 后重跑）")
            if suggestion_lines:
                print(f"💡 模糊匹配候选（P1-F2）：{suggestion_text}")
            sys.exit(1)
    # notes 章数远少于 meta 章数 → 静默缺章风险（软告警，不阻断，因 notes 可合并章节）
    if len(chapters_ev) < max(1, len(ch) // 2):
        print(f"⚠️ 装配前硬校验：notes 解析出 {len(chapters_ev)} 章，远少于 meta 的 {len(ch)} 章，可能静默缺章，请复核笔记 §4 模板。")

    # ---- 闸1 检索可达性 ----
    # 检索空间 = 正文 + 03 头部「书目信息行」（书名/来源/作者/出版/类型）+ 编译笔记（🟡11 对齐 EPUB）。
    # 排除通改清单/存疑待核/OCR规则/锚点等说明文字——否则头部"注水"（搜到清单里的说明而非正文）造成假阳性。
    # 保留书目行是为避免"前辅文为图片页时书名/作者在正文不可达"的误报。
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
            or l.startswith("> **出版") or l.startswith("> **类型")
        )
        search_space = fulltext_clean_stripped + "\n" + hdr03_gate + "\n" + notes_text
        gate1["miss"] = [t for t in terms if search_space.count(t) == 0]
        print(f"闸1 检索（{gate_src}）：{len(terms)} 词，缺失 {len(gate1['miss'])}：{gate1['miss']}（检索空间含 03 正文+书目行+01 笔记）")
        if len(terms) < 5:
            print(f"⚠️ 闸1 检索词仅 {len(terms)} 个（<5），检索面偏窄，建议补充 book_meta.proper_names 或 --retrieval 清单。")
    else:
        print("⚠️ 闸1：未生成任何检索词（无章名片段 / proper_names / 词典命中），跳过（建议提供 --retrieval 关键专名清单）")

    # ---- 错字清单复扫（报告项，§6.1 报告项表：不阻断、不进退出码）----
    # 作用：把 error_forms.txt（人工确认过的错形清单）在 03 正文里过一遍，有残留就报出来。
    # 检索空间 = fulltext_clean（03 正文，不含头部通改清单——清单会记录「荀或→荀彧」，含进去永远报残留）。
    # **不再阻断**：这是一份「自己检查自己有没有检查」的清单，缺了不等于书坏了，有残留也只说明要补改。
    # 书到底行不行，由交付前的 check_book.py（闸A 抽检 + 闸B 书账互释）判定。
    # 清单缺失时静默跳过并记入报告，不要求先造清单才能交付。
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
            # 2026-10-08 适配性修复：清单"存在但无有效条目"（空/仅注释）与缺失同等对待——不假装清零。
            # 两路径同口径：跳过并记入报告，不阻断交付。
            if not ef_items:
                print("错字清单复扫：error_forms.txt 存在但无有效条目（空文件或仅注释行），本项跳过。")
            # ---- P1 改造5（方向校验）+ 改造7（裸单字覆盖告警）----
            # 方向校验：wrong 应在底本(struct)出现、right 应在 03 出现；wrong==right 无意义。
            # 裸单字告警：闸1b 只查 ≥2 字复合，裸单字静默跳过——若台账也无 systematic 覆盖则漏检风险。
            gate1b_dir, bare_ef = [], [w for w, _ in ef_items if len(w) == 1]
            sys_olds = collect_systematic_olds(B)
            for wrong, right in ef_items:
                if wrong == right:
                    gate1b_dir.append(f"{wrong}：wrong==right，方向无效（该条无意义，建议删除）")
                    continue
                if struct and wrong not in struct:
                    gate1b_dir.append(f"{wrong}：错误形态在底本(struct) 0 命中——可能凭空制造或方向反了（应 wrong 在底本出现、right 在 03 出现）")
                if right and right not in fulltext_clean_stripped:
                    gate1b_dir.append(f"{right}：正确形态在 03 干净正文 0 命中——修正方向可能有误（请核对）")
            bare_uncovered = [w for w in bare_ef if w not in sys_olds]
            gate1b["direction"] = gate1b_dir
            gate1b["bare_uncovered"] = bare_uncovered
            if bare_ef:
                _cv = [w for w in bare_ef if w in sys_olds]
                print(f"闸1b 裸单字告警：{len(bare_ef)} 条裸单字"
                      + (f"，{len(_cv)} 条已由台账 systematic 覆盖" if _cv else "")
                      + (f"，{len(bare_uncovered)} 条无台账覆盖（闸1b 不查裸单字、闸2 仅查台账补丁，可能漏检，建议改复合补丁）" if bare_uncovered else ""))
                for w in bare_uncovered:
                    print(f"   ⚠️ 裸单字「{w}」无台账 systematic 覆盖，漏检风险")
            if gate1b_dir:
                print(f"闸1b 方向校验告警 {len(gate1b_dir)} 条：")
                for _d in gate1b_dir:
                    print("   ⚠️ " + _d)
            # v1.16.2 修复：区分裸词/复合词，杜绝「闭环自证」与「裸单字永不通过」两种假象。
            #  - 复合/有意义错形（≥2 字）：直接计数，残留即未清零（≥2 字串几乎不会是合法正字误报）。
            #  - 裸单字常用字（如 入/人、唐/磨）：直接 count 必误报（正字在全书到处都是），
            #    其清零由「带上下文串的 per-phys 补丁」保证，已由闸2 逐补丁全量校验覆盖，此处只计数不阻断。
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
    # 有残留只报告：清单是「人工确认过的错形」，残留说明该补的补丁还没补——记一笔，不拦交付。
    # 判据在书不在清单：这些残留若真在 03 里，check_book 的闸B 会以「记录→书」的方向抓出来。
    if gate1b["status"] == "fail":
        print("错字清单复扫：有残留（见上），属报告项，不阻断；按清单补改 03 并在 04 §四 留痕即可。")

    # ---- 01 章节详解 ----
    # （notes / compiled 已提前至闸1 之前定义，🟡11 行为对齐——此处直接复用）
    # 🟡12（2026-09-08 剩余批）：旧正则含 `M\d{2,}`，会把军事书的武器型号（M16/M1A2/M109）
    # 全数计入"引文锚点"，统计虚高失真——删除，引文锚点只认「原书页 N」。
    # 古籍支的笔记条目带 `<<<PAGE phys=N juan=J vol=V>>>`，旧式只认「原书页 N」会全程计 0。
    total_cite = sum(len(re.findall(r"原书页[码]?\s*\d+|<<<PAGE phys=\d+|SEC idx=\d+",
                                    _synced_by_file.get(f) or clean_note(
                                        open(f, encoding="utf-8").read(), f))) for f in notes)
    toc = "\n".join(f"{c['no']}. {c['name']}（原书页 {c.get('book_start')}–{c.get('book_end')}）" for c in ch)
    intro01 = (
        f"# 《{meta['book']}》章节详解（结构化梳理）\n\n"
        f"> **书类型**：{meta.get('type','')}\n"
        f"> **引用锚点**：（原书页 M），{meta.get('pagemap_desc','')}。{{_anchor_claim}}。\n"
        f"> **OCR 纠错**：正文逐字；已裁定错字在 03 直接改正，逐条改错记录见 `04_改错留痕.md`；年代/数字核对原文不臆造。\n\n"
        f"## 目录（{len(ch)} 章）\n\n{toc}\n\n---\n\n"
    )
    _ok_anchor, _off, _anchor_claim = note_anchor_range_report(compiled, ch, kind="PHYS")
    if not _ok_anchor:
        print(f"⚠️ 01 锚点区间核验：{_anchor_claim}")
    intro01 = intro01.replace("{_anchor_claim}", _anchor_claim)
    safe_write(os.path.join(OUT, PREFIX + "01_章节详解.md"), intro01 + "\n\n---\n\n".join(compiled))  # P0 #3
    print(f"01 完成：{len(notes)} 份笔记编译，引文锚点约 {total_cite} 处")

    # ---- 02 脉络与速查卡 ----
    # 🟡16（2026-09-08 剩余批）：phys_start/phys_end 旧用硬索引 c['phys_start']（meta 缺字段即
    # KeyError 崩），而 book_start/book_end 用 .get()——两处行为不一致；统一改 .get 缺失显 "？"。
    # 🟡15：章名含 `|` 会截断表格，转义。
    overview_rows = "\n".join(
        f"| {c['no']:>2} | {_esc_cell(c['name'])} | phys {c.get('phys_start', '？')}–{c.get('phys_end', '？')} | 原书页 {c.get('book_start', '？')}–{c.get('book_end', '？')} |"
        for c in ch)
    # 第三节：按章分组渲染子弹条目（逐条带原书页锚）
    sec3 = []
    total_ev = 0
    for c in chapters_ev:
        if not c["events"]:
            continue
        sec3.append(f"### 第{c['no']}章 {c['name']}\n")
        by_sec = {}
        for entry, page, sec in c["events"]:
            by_sec.setdefault(sec, []).append((entry, page))
        for sec, items in by_sec.items():
            sec3.append(f"**{sec}**\n")
            sec3.append("| 条目 | 定位 |")
            sec3.append("|---|---|")
            for entry, page in items:
                sec3.append(f"| {_esc_cell(entry)} | {page or '—'} |")  # 🟡15 转义 |
            sec3.append("")
            total_ev += len(items)
    sec3_txt = "\n".join(sec3)
    # 02 类型自适应（与 assemble_epub.py 对齐）：叙事型→要点+名句，非叙事型→概念+名句
    btype = meta.get('type', '')
    show_point = is_narrative(btype)
    type_label = btype.split('（')[0].strip() if btype else '未标注'
    card_kind = "要点+名句" if show_point else "概念+名句"
    sec3_title = "关键要点速查" if show_point else "核心概念与原句速查"
    # v1.16.2（Fix 6）：读法建议按 meta.type 分流，不再硬编码"战争/技术变革"等军事用语。
    # ④ 制度/沿革型书给制度史专属读法；其余书给通用读法。
    is_inst = ("制度" in type_label or "沿革" in type_label or "④" in (btype or ""))
    reading_hint = ("先抓每章制度源流与职官设置主线（沿革脉络 / 机构职官 / 核心概念）"
                    if is_inst else
                    "先抓每章主线与「核心概念 / 论证要点」")
    hdr02 = (
        f"# 《{meta['book']}》脉络与速查卡（{type_label}）\n\n"
        f"> 本卡为「读法 + 索引」辅助。{meta.get('pagemap_desc','')}\n"
        f"> 速查卡形态（类型自适应）：{card_kind}。\n\n"
        f"## 一、全书章节一览（{len(ch)} 章）\n| 章 | 章名/主题 | 物理页 | 原书页 |\n|---|---|---|---|\n{overview_rows}\n\n"
        f"## 二、读法建议\n"
        f"1. {reading_hint}（见 `{PREFIX}01_章节详解.md`）；\n"
        f"2. 用（原书页 M）在 `{PREFIX}03_带页码全文.md` grep `<<<PAGE phys=<M偏移>>` 直达原文；\n"
        f"3. OCR 错讹已就地改正（03 为干净底本、正文不内嵌记号），逐条见 `{PREFIX}04_改错留痕.md` 台账。\n\n"
        f"## 三、{sec3_title}（按章·原书页定位）\n"
        f"> 取自各章笔记各小节条目（制度概说/沿革脉络/机构职官/论证要点/关键原句摘录等），逐条保留（原书页）锚点。\n"
        f"> 按章顺序编排；定位列：印页可考的书显示原书印页，"
        f"古籍（不设印页）显示 `phys` 物理页，均可在 `{PREFIX}03_带页码全文.md` 直达原文。\n\n"
        f"{sec3_txt}\n\n（共 {total_ev} 条；逐章完整梳理见 `{PREFIX}01_章节详解.md`。）\n"
    )
    safe_write(os.path.join(OUT, PREFIX + "02_脉络与速查卡.md"), hdr02)  # P0 #3
    print(f"02 完成：抽取 {total_ev} 条（{len(chapters_ev)} 章分组，类型={type_label}，形态={card_kind}）")

    # ---- 00 总览与目录对照 ----
    # C2 软告警（PDF 路径类比）：meta 章在笔记中无对应标题/事件 → 02 静默缺章，待复核（非硬失败）
    ev_nos = {c["no"] for c in chapters_ev}
    pdf_empty = [c for c in ch if c["no"] not in ev_nos]
    # ---- 设闸审计报告（统一构建，2026-10-08：字段集与顺序同 EPUB 路径对齐）----
    _g1b_clear = (
        (f"{gate1b['items']} 项，残留 {len(gate1b['residual'])} 项"
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
        "gate0_anchor": (f"03 含 `<<<PAGE` 锚 {anchor} 个" if anchor
                         else "0 个——**本项无样本**，页码定位不可用（不等于通过）"),
        "gate1_retrieval": (f"提供检索词 {gate1['terms']} 个，缺失 {len(gate1['miss'])} 个"
                            + (f"（{gate1['miss']}）" if gate1['miss']
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
        "gate2_content": (f"混淆词典扫描命中 = {len(dict_hits)} 类"
                          + (f" → ⚠️ 命中：{dict_hits}，须逐条补改并留痕" if dict_hits
                             else " → PASS（03 内容无词典命中错字）")),
        "gate2_replay": (f"assemble 后回放 active 补丁 old 对 03 干净正文，真实残留 = {residual_n} 处 → "
                         + ("PASS（03 与 corrections 台账一致，无静默改动）" if residual_n == 0
                            else "⚠️ 残留>0：台账与 03 不一致，由 check_book 闸B 判处置（补改 03 / 修台账），不整本重算")),
        "gate3_completeness": (f"⚠️ {len(pdf_empty)} 章在笔记中无对应标题/事件"
                               f" {[(c['no'], c['name']) for c in pdf_empty]}，可能静默缺章，请复核笔记是否严格按 §4 模板"
                               if pdf_empty else "全部章节均在笔记中有对应标题/事件（无静默缺章）"),
        "gate4_verbatim": "未自动统计（EPUB 路径自动统计）；PDF 路径用 `04_check_citations.py` 回查 01/02 的引文锚点",
        "trace_ledger": (f"03 已为干净全文（正文不再内嵌〔〕留痕，§3.1）；本次应用 trace 型纠错 {applied_trace} 处 + "
                         f"系统噪声通改 {len(tongai.splitlines()) if tongai else 0} 类 + 转录层纠错 {applied_ocr} 处，"
                         f"已全量登记入 `{PREFIX}04_改错留痕.md`（台账主源 + diff 兜底，§6.3）；"
                         f"页末残句标注：按用户要求关闭（03 不插〔页末残句〕标记）"),
        "pending_conflict": (f"{len(pending)} 处，见 work/apply_report.md（须人工选边，Stage 4「assemble 应用报告规范」）"
                             if pending else "0 处（无冲突）"),
        "patch_apply": ((_no_corr_note + "；") if _no_corr_note else "")
                       + (f"corrections/ 共 {len(corr_files)} 份；未命中 {n_miss} 处（报告项、不阻断；"
                          f"另 冗余 per-phys {n_redundant} / 无效 systematic 通改 {n_invalid_sys} / 回退成功 {n_fallback} 仅记录，见 correction_miss_log.md）"),
        "silent_delete": (f"{applied_silent} 处（错乱表格/图谱碎片/表图文字层，有原图或 region_noise_log 可核）"
                          if applied_silent else "0 处"),
        "zero_fabrication": (f"引文逐字 + 原书页锚 + OCR 留痕；{len(notes)} 份笔记合计约 {total_cite} 处引文锚点；"
                             # 原为硬编码「均落入对应章定位区间」：0 锚点时是空真假话，
                             # 有越界锚时与 01 头部实测结论自相矛盾。改用 note_anchor_range_report 的实算结论。
                             + ("**本项无样本，不可判**（0 处引文锚点 ≠ 通过）" if total_cite == 0
                                else _anchor_claim)),
    }
    # 终端也打一行：师傅多半只看装配输出，不翻 work 目录。报告里写了不等于他看见了。
    print(f"精读覆盖率：{_audit_fields['reading_coverage']}")
    gate_report = build_audit_report("PDF·phys 体系", meta["book"], "pipeline_assemble.py", _audit_fields,
                                     heading=False, b2_warn=warn_block)
    hdr00 = (
        f"# 《{meta['book']}》梳理总览与目录对照\n\n"
        f"> **书名**：《{meta['book']}》\n> **作者**：{meta.get('author','')}"
        + (f"（{meta.get('translator','')} 译）" if meta.get('translator') else "")
        + (f"\n> **出版**：{meta.get('publisher','')}，{meta.get('year','')}" if meta.get('publisher') else "")
        + f"\n> **类型**：{meta.get('type','')}\n> **规模**：{meta.get('total_phys','?')} 物理页 / 约 {len(fulltext_clean)//10000} 万字符 / {len(ch)} 章\n\n"
        f"## 一、页码体系（关键）\n{meta.get('pagemap_desc','')}\n\n"
        f"## 二、目录 ↔ 原书页对照（{len(ch)} 章）\n| 章 | 章名 | 物理页范围 | 原书页范围 |\n|---|---|---|---|\n{overview_rows}\n\n"
        f"## 三、全书总览\n{meta.get('overview','（见 01 章节详解）')}\n\n"
        f"## 四、标注与 OCR 规则\n{meta.get('ocr_note','正文逐字；非平凡 OCR 错字以〔原书作「X」〕留痕。')}\n\n"
        f"## 五、设闸审计报告\n{gate_report}\n"
        f"- 可引用原句/事件速查见 `{PREFIX}02_脉络与速查卡.md`；原文检索见 `{PREFIX}03_带页码全文.md`（权威纠错底本）。\n"
    )
    safe_write(os.path.join(OUT, PREFIX + "00_梳理总览与目录对照.md"), hdr00)  # P0 #3
    print(f"00 完成")

    # ---- audit_report.md 落盘（2026-10-08 适配性整改：改走 safe_write；与 00 第五节同源）----
    # EPUB 侧一直把设闸审计落盘 work/audit_report.md，PDF 侧旧版只写进 00 的第五节——主控方
    # 自动化读报告时两路径入口不同。现完全对齐：同一 _audit_fields 经 build_audit_report 渲染，
    # 仅 heading=True 多出报告标题与书名引言；落盘位置（work 目录）与写法（safe_write 原子写）均与 EPUB 相同。
    safe_write(os.path.join(B, "audit_report.md"),
               build_audit_report("PDF·phys 体系", meta["book"], "pipeline_assemble.py", _audit_fields,
                                  heading=True, b2_warn=warn_block))
    print("audit_report.md 已落盘")

    # ---- 交付级全件错形复扫（2026-10-05 P0，与 EPUB 路径同口径）----
    # 01/02 由 notes 编译、00 由章名与统计汇总生成，都不在闸1b 的扫描空间（03 正文）里；
    # 它们保留已判定错形会让 02 名句在 03 检索 0 命中，契约断裂而全闸无感。
    if ef_items and not args.no_gate1b:
        _gi = scan_general_items(meta.get("type", ""))
        _fh, _skipped = final_error_form_scan(OUT, ef_items, general_items=_gi)
        if _skipped:
            print("⚠️ 交付级错形复扫：以下交付件读入失败，扫描覆盖不可证（报告项，不阻断）：")
            for _fn, _why in _skipped:
                print(f"   {_fn}：{_why}")
        if _fh:
            print("⚠️ 交付级错形复扫：以下交付件仍含已判定的错误形态（报告项，不阻断）：")
            for _fn, _bad in _fh.items():
                print(f"   {_fn}：" + "、".join(f"「{w}」{n}处→「{r}」" for w, r, n in _bad))
            print("   处理：按清单补改 03 并在 04 §四 留痕，或同步 notes 后重出 01/02。")
        else:
            print(f"交付级错形复扫：00/01/02/03 全部 {len(ef_items)} 项错误形态清零 → 无残留")

    # B2 未命中告警：**报告项，不阻断**。未命中的来源通常是台账（old 写成通改前形态、phys 记错页），
    # 而不是书坏了。拦下交付只会逼人加 --no-fail 绕过去，反而让阈值失去意义。
    # 这些 entry 的真实影响由 check_book 的闸B 以「记录→书」方向体现（03 里 old 是否仍在）。
    if should_fail:
        print(f"⚠️ 补丁未命中 {n_miss} 处超过阈值 {args.miss_threshold}（报告项，不阻断）："
              f"按 correction_miss_log.md 逐条处置台账即可，不必整本重算。")

    # P0 改造14：逃生口聚合告警——同时省略多道检查时汇总提醒并写入交付件。
    # 只统计确实起作用的两个旗；--no-fail / --tolerate-miss 已无实际作用（未命中恒为报告项），不列入。
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
        _p00 = os.path.join(OUT, PREFIX + "00_梳理总览与目录对照.md")
        if os.path.isfile(_p00):
            with open(_p00, "a", encoding="utf-8", newline="\n") as f:
                f.write(_agg)

    # ---- 交付指纹（v1.34.0）：00/01/02/03 的 sha256 追加到 04 末尾 ----
    # 目的：verify_delivery 据此分辨「assemble 的确定性产物」与「assemble 之后的手改」。
    # 04 自身不参与（自指无意义）。
    _fp = []
    for _k, _suf in (("00", "00_梳理总览与目录对照.md"), ("01", "01_章节详解.md"),
                     ("02", "02_脉络与速查卡.md"), ("03", "03_带页码全文.md")):
        _p = os.path.join(OUT, PREFIX + _suf)
        if os.path.isfile(_p):
            _fp.append(f"{_k}=sha256:{sha256_file(_p)}")
    if _fp:
        with open(os.path.join(OUT, PREFIX + "04_改错留痕.md"), "a", encoding="utf-8", newline="\n") as _f:
            _f.write("\n## 五、交付指纹\n\n> " + "；".join(_fp) + "\n>\n"
                     "> 由 assemble 定稿前计算；`verify_delivery.py` 逐件比对，不一致即说明这四件在"
                     "assemble 之后被改过（改动须写回 corrections 台账并重跑 assemble）。"
                     "04 自身不参与。\n")
        print("交付指纹：00/01/02/03 已记入 04（verify_delivery 据此识别 assemble 后手改）")

    # ---- 定稿交付（2026-10-05 P1）：staging → 真实交付目录，整体切换 ----
    if os.path.isdir(REAL_OUT):
        # 旧交付件先归档到备份根（可恢复性：staging 每轮重建，safe_write 的覆盖保护
        # 无旧文件可触发，改由定稿时把上一轮交付件整体归档到 work/_bak）
        os.makedirs(os.path.join(B, "_bak"), exist_ok=True)
        for _fn in os.listdir(REAL_OUT):
            _src = os.path.join(REAL_OUT, _fn)
            if os.path.isfile(_src):
                try:
                    shutil.move(_src, os.path.join(B, "_bak", _fn + ".prev.bak"))
                except (PermissionError, OSError):
                    pass  # OneDrive锁定时跳过归档，直接覆盖
    else:
        os.makedirs(REAL_OUT, exist_ok=True)
    # 直接复制覆盖，不删除目录（避免OneDrive锁定）
    for _fn in os.listdir(OUT):
        _src = os.path.join(OUT, _fn)
        if os.path.isfile(_src):
            shutil.copy2(_src, os.path.join(REAL_OUT, _fn))
    # 清理staging
    try:
        shutil.rmtree(OUT)
    except (PermissionError, OSError):
        pass
    print(f"装配定稿：{REAL_OUT}（五件 00–04 齐出；旧交付件已归档 work/_bak）")
    print("  书对不对由 check_book.py 判（闸A 可读性抽检出清单、闸B 书账互释与交付完整性为阻断项）；"
          f"本次各设闸审计结论见 {os.path.join(B, 'audit_report.md')}，均为报告项、不参与退出码。")

if __name__ == "__main__":
    main()
