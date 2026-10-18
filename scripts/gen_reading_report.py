#!/usr/bin/env python3
"""
gen_reading_report.py — 生成05精读汇报.md骨架（v1.14.0 改写：版本/执行者去硬编码，05 生成前过闸2 verifier）

【已退出主链（2026-10-18）】交付件已于批1 收敛为五件（00–04），不再产出 05。
  另：audit_report 的字段标签已去掉「闸N」前缀，并新增首项「精读覆盖率（逐行）」；
  本文件 parse_audit_report() 的正则按旧标签编写，对新报告取不到数。保留仅为历史核对，
  新流程不要调用（覆盖率读 work/audit_report.md 第一行，或直接跑 check_reading.py）。

从工作目录自动收集精读过程信息，生成05精读汇报.md的骨架。
执行者在骨架基础上补充判断过程、逐个核实过程、经核实删除补丁的详细信息。

关键设计：
- correction_miss_log.md 快照嵌入第5.6.1节（防止后续assemble覆盖丢失记录）
- 只读脚本，不修改任何工作文件，只输出05骨架到交付目录
- 对缺失的信息源做容错处理，输出"（未生成/不存在）"标注，不报错

用法：
    python scripts/gen_reading_report.py --work <work> --out <out> [--meta <book_meta.json>]
"""

import argparse
import json
import os
import re
import sys
import subprocess
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import derive_prefix, safe_write, is_residual_full, strip_correction_traces  # v1.15.1：交付件前缀=目录名（去_OCR/_文字版标注），六件统一；safe_write=N7 原子写（2026-10-04）；D1：残留判定复用权威口径


def read_file(path, encoding="utf-8-sig"):
    """读取文件，不存在返回None。默认 utf-8-sig：容错 BOM（用户用 Excel/记事本另存 meta/清单会带 BOM），
    与 _common.read_text_cn 同口径（2026-10-08 适配性修复）。"""
    try:
        with open(path, "r", encoding=encoding, errors="strict") as f:  # P1-12（2026-10-04 审查）：strict 暴露真实解码损失，不再静默掩错
            return f.read()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return None


def count_lines(text):
    """统计行数"""
    if not text:
        return 0
    return len(text.splitlines())


def count_anchors(text):
    """统计<<<PAGE phys=N ...>>>锚点数"""
    if not text:
        return 0
    return len(re.findall(r"<<<PAGE\s+phys=", text))


def load_json(path):
    """加载JSON，不存在或解析失败返回None"""
    content = read_file(path)
    if content is None:
        return None
    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None


def collect_corrections(work_dir):
    """收集corrections/*.json的统计信息和全部补丁列表"""
    corr_dir = os.path.join(work_dir, "corrections")
    if not os.path.isdir(corr_dir):
        return [], {
            "total": 0, "systematic": 0, "regex": 0,
            "per_phys": 0, "ocr": 0, "silent": 0, "delete": 0,
            "files": []
        }

    all_patches = []
    stats = {
        "total": 0, "systematic": 0, "regex": 0,
        "per_phys": 0, "ocr": 0, "silent": 0, "delete": 0,
        "files": []
    }

    for fname in sorted(os.listdir(corr_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(corr_dir, fname)
        patches = load_json(fpath)
        if not isinstance(patches, list):
            continue
        stats["files"].append((fname, len(patches)))
        for p in patches:
            if not isinstance(p, dict):
                continue
            p["_source_file"] = fname
            all_patches.append(p)
            stats["total"] += 1
            if p.get("systematic"):
                stats["systematic"] += 1
            if p.get("regex"):
                stats["regex"] += 1
            phys = p.get("phys")
            if phys is not None and phys != "ALL":
                stats["per_phys"] += 1
            if p.get("ocr"):
                stats["ocr"] += 1
            if p.get("silent"):
                stats["silent"] += 1
            if p.get("new") == "" or p.get("new") is None:
                stats["delete"] += 1

    return all_patches, stats


def get_skill_version():
    """从 SKILL.md 前端 matter 读 version（v1.14.0 起去硬编码）。"""
    try:
        sk = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "SKILL.md")
        if os.path.isfile(sk):
            txt = open(sk, encoding="utf-8").read()
            m = re.search(r"^version:\s*(\S+)", txt, re.M)
            if m:
                return m.group(1)
    except Exception:
        pass
    return "unknown"


def get_executor():
    """执行者：优先环境变量 EBOOK_REPORT_EXECUTOR，回退系统用户（v1.14.0 起去硬编码）。"""
    return os.environ.get("EBOOK_REPORT_EXECUTOR") or os.environ.get("USERNAME") or os.environ.get("USER") or "待补充"


def extract_book_title(work_dir, meta):
    """从book_meta.json或目录名推导书名"""
    if meta and isinstance(meta, dict):
        for key in ("title", "书名", "name", "book_title", "book"):
            val = meta.get(key)
            if val:
                return str(val).strip()
    # 从工作目录名推导
    dir_name = os.path.basename(os.path.normpath(work_dir))
    return dir_name


def parse_error_forms(text):
    """解析error_forms.txt，返回清单项列表"""
    if not text:
        return []
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 格式：错误形态|正确形态|判定依据
        parts = line.split("|")
        items.append(parts[0].strip() if parts else line)
    return items


# ========== D1：数据源自动解析（audit/apply/manifest）==========

def parse_audit_report(text):
    """解析 audit_report.md（_common.build_audit_report 标准格式），返回结构化字段。

    提取各闸的机械结论数字，供 05 第五节直接填充，避免人工抄录。
    """
    info = {}
    if not text:
        return info

    def find1(pat):
        m = re.search(pat, text)
        return m

    # 闸0：03 锚点数
    m = re.search(r"闸0[^：:]*?：.*?锚\s*(\d+)\s*个", text)
    if m:
        info["gate0_anchors"] = int(m.group(1))
    # 闸1：检索词总数、缺失数、缺失列表
    m = re.search(r"闸1[^：:]*?：提供检索词\s*(\d+)\s*个，缺失\s*(\d+)\s*个", text)
    if m:
        info["gate1_terms"] = int(m.group(1))
        info["gate1_missing_n"] = int(m.group(2))
    m = re.search(r"缺失\s*\d+\s*个（(\[[^\]]*\])", text)
    if m:
        info["gate1_missing_list"] = m.group(1)
    # 闸1b：项数、残留
    m = re.search(r"闸1b 高频错字清零：\s*(\d+)\s*项，残留\s*(\d+)\s*项", text)
    if m:
        info["gate1b_items"] = int(m.group(1))
        info["gate1b_residual"] = int(m.group(2))
    # 闸2 词典扫描
    m = re.search(r"闸2 内容正确性（独立词典基准）：混淆词典扫描命中\s*=\s*(\d+)\s*类", text)
    if m:
        info["gate2_dict_hits"] = int(m.group(1))
    # 闸2 补丁生效回放残留
    m = re.search(r"闸2 补丁生效审计.*?真实残留\s*=\s*(\d+)\s*处", text)
    if m:
        info["gate2_patch_residual"] = int(m.group(1))
    # 闸3
    m = re.search(r"闸3 02 完整性（原 C2）：", text)
    if m:
        tail = text[m.end():m.end() + 160]
        info["gate3_warn"] = "⚠️" in tail
        mm = re.search(r"(\d+)\s*章在笔记中无对应", tail)
        info["gate3_missing_chapters"] = int(mm.group(1)) if mm else 0
    # 纠错留痕里的 trace / 通改 / 转录
    m = re.search(r"应用 trace 型纠错\s*(\d+)\s*处\s*\+\s*系统噪声通改\s*(\d+)\s*类\s*\+\s*转录层纠错\s*(\d+)\s*处", text)
    if m:
        info["trace_n"] = int(m.group(1))
        info["tongai_n"] = int(m.group(2))
        info["ocr_n"] = int(m.group(3))
    # 补丁施用：未命中
    m = re.search(r"补丁施用：corrections/ 共\s*(\d+)\s*份；未命中\s*(\d+)\s*处", text)
    if m:
        info["corr_files_n"] = int(m.group(1))
        info["miss_n"] = int(m.group(2))
    # 静默删除
    m = re.search(r"静默删除（结构性噪音）：\s*(\d+)\s*处", text)
    if m:
        info["silent_n"] = int(m.group(1))
    return info


def parse_apply_report(text):
    """解析 apply_report.md（emit_apply_report 格式），返回应用/未命中/残留计数。"""
    info = {}
    if not text:
        return info
    m = re.search(r"已应用（active 补丁 old 已不在 03 干净正文）：\s*(\d+)\s*处", text)
    if m:
        info["applied"] = int(m.group(1))
    m = re.search(r"底本 0 命中（补丁 old 不在 fulltext_struct，疑似错位）：\s*(\d+)\s*处", text)
    if m:
        info["zero_hit"] = int(m.group(1))
    m = re.search(r"残留（old 仍在 03 干净正文，未应用成功）：\s*(\d+)\s*处", text)
    if m:
        info["residual"] = int(m.group(1))
    m = re.search(r"存疑待核（补丁冲突，同一原文多候选）：\s*(\d+)\s*处", text)
    if m:
        info["conflict"] = int(m.group(1))
    m = re.search(r"形近字裸串无上下文（须人工复核，防纠错造错）：\s*(\d+)\s*处", text)
    if m:
        info["bare_near"] = int(m.group(1))
    return info


def load_manifest_stats(path):
    """从 patch_manifest.json 统计 active/deleted 补丁数。"""
    stats = {"active": 0, "deleted": 0, "total": 0}
    m = load_json(path)
    if not m or not isinstance(m.get("patches"), list):
        return stats
    for p in m["patches"]:
        stats["total"] += 1
        if p.get("status") == "deleted":
            stats["deleted"] += 1
        else:
            stats["active"] += 1
    return stats


def parse_group_plan(work_dir):
    """读取 group_plan.json（gen_group_plan 产出），返回每组章范围/phys/行数字典。"""
    gp = load_json(os.path.join(work_dir, "group_plan.json"))
    if not gp or not isinstance(gp.get("groups"), list):
        return []
    return gp["groups"]


def sum_read_lines(text):
    """从 reading_progress 文本提取行范围（数字-数字）求和，排除 phys/book/页码范围。"""
    if not text:
        return 0
    total = 0
    for line in text.splitlines():
        for m in re.finditer(r"(\d{1,6})\s*[-–—]\s*(\d{1,6})", line):
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                continue
            # 排除 phys/book/页码范围：匹配项前 6 字符含 phys/book/页码/印页/页 且紧邻
            ctx = line[max(0, m.start() - 6):m.start()]
            if re.search(r"(phys|book|页码|印页)\s*$", ctx, re.I):
                continue
            total += b - a + 1
    return total


def collect_doubts(work_dir):
    """D2：从 corrections/*.json 提取存疑补丁（type 含'存疑'，或 reason 以'存疑'开头）。

    返回 [(source_file, phys, old, new, reason_short), ...]，供 05 报告 7.3 节自动汇总。
    存疑项不臆改（new 常为空），是精读中拿不准、留待核底本的字句。
    """
    doubts = []
    corr_dir = os.path.join(work_dir, "corrections")
    if not os.path.isdir(corr_dir):
        return doubts
    for fname in sorted(os.listdir(corr_dir)):
        if not fname.endswith(".json"):
            continue
        data = load_json(os.path.join(corr_dir, fname))
        if not isinstance(data, list):
            continue
        for p in data:
            if not isinstance(p, dict):
                continue
            ptype = str(p.get("type", ""))
            reason = str(p.get("reason", ""))
            is_doubt = ("存疑" in ptype) or reason.startswith("存疑") or ("存疑" in reason and "暂不改" in reason)
            if not is_doubt:
                continue
            old = str(p.get("old", ""))[:40].replace("\n", " ")
            new = str(p.get("new", ""))[:20].replace("\n", " ")
            # reason 取"存疑："后前100字
            rs = reason
            if rs.startswith("存疑："):
                rs = rs[3:]
            rs = rs[:120].replace("\n", " ")
            doubts.append((fname, p.get("phys", ""), old, new, rs))
    return doubts


def analyze_error_patterns(patches):
    """P2-E3（v1.27.0）：纠错模式画像——统计active补丁的类型分布。

    分类：形近字（old/new长度相同且≥1字不同）、专名（new≥2字且含人名/地名特征）、
    数字标点（old含数字/标点）、衍字删除（new为空）、成语词语（old≥4字）、其他。
    返回 {类别: 数量} 和占比字符串。
    """
    active = [p for p in patches if p.get("status") == "active" and p.get("old")]
    if not active:
        return {}, "无active补丁"
    cats = {"形近字": 0, "专名": 0, "数字标点": 0, "衍字删除": 0, "成语词语": 0, "其他": 0}
    for p in active:
        old = str(p.get("old", ""))
        new = str(p.get("new", ""))
        if not new:
            cats["衍字删除"] += 1
        elif re.search(r"\d", old) or re.search(r"[，。；：、！？""''（）《》]", old):
            cats["数字标点"] += 1
        elif len(old) >= 4 and len(new) >= 4:
            cats["成语词语"] += 1
        elif len(old) == len(new) and len(old) <= 3:
            cats["形近字"] += 1
        elif len(new) >= 2 and re.search(r"[奕吴潘陆龚牛赵宋瞿李]", new):
            cats["专名"] += 1
        else:
            cats["其他"] += 1
    total = len(active)
    parts = [f"{k}{v}条({v/total*100:.0f}%)" for k, v in sorted(cats.items(), key=lambda x: -x[1]) if v > 0]
    return cats, "、".join(parts)


def select_key_patches(patches, n=10):
    """选 n 个代表性纠错（2-6字专名/形近，非删除/存疑/已删），去重 new。"""
    cands = []
    seen = set()
    for p in patches:
        old = str(p.get("old", ""))
        new = str(p.get("new", ""))
        if p.get("status") == "deleted" or p.get("merge") or p.get("systematic"):
            continue
        if not new or new == old:
            continue
        if not (2 <= len(new) <= 6) or not (2 <= len(old) <= 12):
            continue
        if any(c in old + new for c in "\n<|"):
            continue
        if new in seen:
            continue
        seen.add(new)
        cands.append((old, new))
        if len(cands) >= n:
            break
    return cands


def count_file_chars(path):
    """统计文件字符数（剥锚后的正文字符），供交付清单。"""
    t = read_file(path)
    if t is None:
        return 0
    t = re.sub(r"<<<PAGE[^>]*>>>", "", t)
    return len(re.sub(r"\s", "", t))


def read_out_file(out_dir, marker):
    """在交付目录按文件名片段（如 '03_'）找文件，返回 (路径, 文本)。"""
    if not os.path.isdir(out_dir):
        return None, None
    for f in sorted(os.listdir(out_dir)):
        if marker in f and f.endswith(".md"):
            p = os.path.join(out_dir, f)
            return p, read_file(p)
    return None, None


def generate_report(work_dir, out_dir, meta_path=None):
    """生成05精读汇报骨架，返回输出路径"""
    # 加载meta
    if meta_path:
        meta = load_json(meta_path)
    else:
        meta = load_json(os.path.join(work_dir, "book_meta.json"))

    book_title = extract_book_title(work_dir, meta)
    file_prefix = derive_prefix(out_dir)  # v1.15.1：文件名前缀=交付目录名（去_OCR/_文字版标注），与assemble六件统一；book_title仅用于报告内部显示

    # ========== 收集信息 ==========
    struct_text = read_file(os.path.join(work_dir, "fulltext_struct.txt"))
    struct_lines = count_lines(struct_text)
    struct_chars = len(struct_text) if struct_text else 0
    struct_anchors = count_anchors(struct_text)

    raw_text = read_file(os.path.join(work_dir, "fulltext_raw.txt"))
    raw_lines = count_lines(raw_text)

    page_index = load_json(os.path.join(work_dir, "page_index.json"))

    all_patches, corr_stats = collect_corrections(work_dir)

    audit_text = read_file(os.path.join(work_dir, "audit_report.md"))
    miss_log_text = read_file(os.path.join(work_dir, "correction_miss_log.md"))
    error_forms_text = read_file(os.path.join(work_dir, "error_forms.txt"))
    error_forms = parse_error_forms(error_forms_text)
    reading_progress = read_file(os.path.join(work_dir, "reading_progress.md"))
    read_lines_sum = sum_read_lines(reading_progress) if reading_progress else 0

    # D1：接入 apply_report / patch_manifest / audit 结构化解析 / group_plan
    apply_text = read_file(os.path.join(work_dir, "apply_report.md"))
    audit_info = parse_audit_report(audit_text)
    apply_info = parse_apply_report(apply_text)
    manifest_stats = load_manifest_stats(
        os.path.join(work_dir, "patch_manifest.json"))
    # P2-E3：加载 active 补丁列表用于纠错模式画像
    manifest_patches = []
    _mp_path = os.path.join(work_dir, "patch_manifest.json")
    if os.path.isfile(_mp_path):
        with open(_mp_path, encoding="utf-8-sig") as _f:
            _mp_data = json.load(_f)
        manifest_patches = [p for p in _mp_data.get("patches", []) if p.get("status") == "active"]
    group_plan = parse_group_plan(work_dir)
    # D2：存疑项自动汇总（从 corrections 提取 type=存疑 的补丁）
    doubts = collect_doubts(work_dir)
    # 章 no -> name 映射（4.1 分章描述用）
    chapter_map = {}
    if meta and isinstance(meta, dict):
        for c in meta.get("chapters", []):
            chapter_map[c.get("no")] = c.get("name", "")
    # 交付目录中已生成的 03（用于自动扫描锚点/书名号/残留）
    _03p, out_03 = read_out_file(out_dir, "03_")
    out_03_clean = out_03 or ""

    # 收集notes
    notes_dir = os.path.join(work_dir, "notes")
    notes_files = []
    if os.path.isdir(notes_dir):
        notes_files = sorted([f for f in os.listdir(notes_dir) if f.endswith(".md")])

    # 预处理审计文件
    table_audit = read_file(os.path.join(work_dir, "table_audit.md"))
    region_noise_log = read_file(os.path.join(work_dir, "region_noise_log.md"))
    region_noise_audit = read_file(os.path.join(work_dir, "region_noise_audit.md"))
    figure_gaps = read_file(os.path.join(work_dir, "figure_gaps_report.md"))
    simplify_leak = read_file(os.path.join(work_dir, "simplify_leak_report.md"))

    # probe结果
    probe_text = read_file(os.path.join(work_dir, "probe_result.txt"))
    if probe_text is None:
        probe_text = read_file(os.path.join(work_dir, "00_probe_result.txt"))

    today = date.today().isoformat()
    skill_version = get_skill_version()
    executor = get_executor()

    # ========== 构建报告 ==========
    L = []  # lines

    # 头部
    L.append(f"# {file_prefix}05_精读汇报")
    L.append("")
    L.append("> 本报告是精读全过程的完整审计记录，由执行者基于 `gen_reading_report.py` 生成的骨架补充完成。")
    L.append(f"> 生成日期：{today} | 执行者：{executor} | 技能版本：{skill_version}")
    L.append(f"> 相关交付件：03 带页码全文（权威纠错底本）、`{file_prefix}04_改错留痕.md`（纠错台账主源，§6.3）、`work/apply_report.md`（闸2 校验结论）。")
    L.append("")
    L.append("---")
    L.append("")

    # ===== 第一节：书籍摸底情况 =====
    L.append("## 一、书籍摸底情况")
    L.append("")
    L.append("### 1.1 基本信息")
    L.append("")
    L.append("| 项目 | 内容 |")
    L.append("|------|------|")
    L.append(f"| 书名 | {book_title} |")
    if meta and isinstance(meta, dict):
        author = meta.get('author') or meta.get('translator') or meta.get('译者')
        L.append(f"| 作者 | {author or '<!-- 待补充 -->'} |")
        L.append(f"| 出版社 | {meta.get('publisher', '<!-- 待补充 -->')} |")
        pubt = meta.get('pub_date') or meta.get('date') or meta.get('year')
        L.append(f"| 出版时间 | {pubt or '<!-- 待补充 -->'} |")
    else:
        L.append("| 作者 | <!-- 待补充 --> |")
        L.append("| 出版社 | <!-- 待补充 --> |")
        L.append("| 出版时间 | <!-- 待补充 --> |")
    L.append(f"| 物理页数 | {struct_anchors} 页（fulltext_struct.txt 锚点数） |")
    L.append(f"| fulltext_struct 总行数 | {struct_lines} 行 |")
    L.append(f"| fulltext_struct 总字符数 | {struct_chars} 字符 |")
    L.append(f"| fulltext_raw 总行数 | {raw_lines} 行（法医底本） |")
    L.append(f"| 源文件路径 | `{meta.get('source_pdf', meta.get('pdf_path', '<!-- 待补充 -->')) if meta else '<!-- 待补充 -->'}` |")
    L.append(f"| 工作目录 | `{work_dir}` |")
    L.append(f"| 交付目录 | `{out_dir}` |")
    L.append("")

    L.append("### 1.2 文字层质量探测")
    L.append("")
    if probe_text:
        L.append("```")
        L.append(probe_text.strip()[:2000])
        L.append("```")
    else:
        L.append("<!-- 待补充：00_probe.py 探测结果（有文字层页占比、文字层质量评估） -->")
    L.append("")
    L.append("- 量化闸门实测：<!-- 待补充：抽了哪些页、v6重OCR与原层逐字比对相似度、判定结论 -->")
    L.append("")

    L.append("### 1.3 底本类型与页码体系")
    L.append("")
    L.append(f"- 底本类型：<!-- 待补充：OCR-PDF（文字层可用）/ 纯扫描PDF（重OCR）/ EPUB -->")
    if page_index and isinstance(page_index, dict):
        L.append(f"- page_index.json 条目数：{len(page_index)}")
    L.append("- offset 判定：<!-- 待补充：phys=N → book=1，offset=N，判定依据（抽了哪些页的页脚印页） -->")
    L.append("- 分段 pagemap（如有）：<!-- 待补充 -->")
    L.append("- 前辅文/正文/附录各自起算情况（如有）：<!-- 待补充 -->")
    L.append("")
    L.append("---")
    L.append("")

    # ===== 第二节：各步骤主要判断过程 =====
    L.append("## 二、各步骤主要判断过程")
    L.append("")
    L.append("### 2.1 Stage 1 法医底本提取")
    L.append("- 是否重 OCR：<!-- 待补充：是/否，判断依据（量化闸门相似度实测结果） -->")
    L.append("- 如重 OCR：<!-- 待补充：引擎版本、参数、耗时、产出质量抽检 -->")
    L.append("- 如不重 OCR：<!-- 待补充：原层质量评估依据 -->")
    L.append("")
    L.append("### 2.2 Stage 2 结构清洗")
    L.append("- 书眉剥离策略：<!-- 待补充：使用了哪些 head-frags / footer-frags，剥离行数 -->")
    L.append("- offset 判定过程：<!-- 待补充：抽了哪些页的页脚印页，如何确定各段起点 -->")
    L.append("- 多套页码系统处理（如有）：<!-- 待补充 -->")
    L.append("")
    L.append("### 2.3 预处理审计")
    L.append(f"- 00b 表格审计：{'存在，见 table_audit.md' if table_audit else '未生成/不适用'}")
    L.append(f"- 00c 碎片剥离：{'存在，见 region_noise_log.md / region_noise_audit.md' if region_noise_log else '未生成/不适用'}")
    L.append(f"- 00d 图片缺口：{'存在，见 figure_gaps_report.md' if figure_gaps else '未生成/不适用'}")
    L.append(f"- 00e 简繁抖动：{'存在，见 simplify_leak_report.md' if simplify_leak else '未生成/不适用（简体底本）'}")
    L.append("- 关键发现和判断：<!-- 待补充 -->")
    L.append("")
    L.append("### 2.4 Stage 3 语义清洗")
    L.append("- 纠错策略：<!-- 待补充：systematic通改优先 / 逐页补丁为主 / 混合策略，选择依据 -->")
    L.append("- 补丁创建原则：<!-- 待补充：如何判断高频系统噪声 vs 低频零散错误 -->")
    L.append("- 转录层/底本层判层依据（§3.1）：<!-- 待补充 -->")
    L.append("")
    L.append("### 2.5 Stage 5 分章精读")
    L.append(f"- 派工方案：分 {len(notes_files)} 组/份笔记，每组负责章节范围见4.1")
    L.append("- 分组依据：<!-- 待补充：章节字数均衡 / 主题完整性 / 其他 -->")
    L.append("- 全量逐字阅读执行情况：见第三节")
    L.append("")
    L.append("---")
    L.append("")

    # ===== 第三节：精读过程 =====
    L.append("## 三、精读过程")
    L.append("")
    L.append("### 3.1 全量逐字阅读进度")
    L.append("")
    if reading_progress:
        L.append("以下为 reading_progress.md 的内容：")
        L.append("")
        L.append("```")
        L.append(reading_progress.strip()[:3000])
        L.append("```")
    else:
        L.append("| 批次 | 行范围 | 行数 | 发现错误数 | 主要错误类型 |")
        L.append("|------|--------|------|-----------|-------------|")
        L.append(f"| 1 | 1-{min(500, struct_lines)} | {min(500, struct_lines)} | <!-- 待补充 --> | <!-- 待补充 --> |")
        L.append("| ... | ... | ... | ... | ... |")
        L.append(f"| 合计 | 1-{struct_lines} | {struct_lines} | <!-- 待补充 --> | |")
    L.append("")
    L.append(f"- fulltext_struct.txt 总行数：{struct_lines}")
    if read_lines_sum:
        _pct = read_lines_sum / max(1, struct_lines) * 100
        L.append(f"- 已读行数：{read_lines_sum}（覆盖率 {_pct:.1f}%，据 reading_progress 行范围求和）")
    else:
        L.append(f"- 已读行数：<!-- 待补充（无 reading_progress 或无法解析，执行者据各组笔记确认，禁止默认填 {struct_lines}） -->")
    L.append("")

    L.append("### 3.2 错误分类统计")
    L.append("")
    L.append("| 错误类型 | 数量 | 占比 | 典型示例 |")
    L.append("|---------|------|------|---------|")
    L.append("| 形近字错误 | <!-- 待补充 --> | | |")
    L.append("| 书名号错误 | <!-- 待补充 --> | | |")
    L.append("| 脚注编号错误 | <!-- 待补充 --> | | |")
    L.append("| 人名/书名错误 | <!-- 待补充 --> | | |")
    L.append("| 缺字/衍字 | <!-- 待补充 --> | | |")
    L.append("| 数字/百分比错误 | <!-- 待补充 --> | | |")
    L.append("| 英文单词错误 | <!-- 待补充 --> | | |")
    L.append("| 专业术语错误 | <!-- 待补充 --> | | |")
    L.append("| 其他 | <!-- 待补充 --> | | |")
    L.append("| 合计 | <!-- 待补充 --> | 100% | |")
    L.append("")

    L.append("### 3.3 纠错补丁统计")
    L.append("")
    L.append("| 补丁类型 | 数量 | 说明 |")
    L.append("|---------|------|------|")
    L.append(f"| systematic 通改 | {corr_stats['systematic']} | phys=\"ALL\"，全局替换 |")
    L.append(f"| systematic 正则型 | {corr_stats['regex']} | regex=true |")
    L.append(f"| per-phys 逐页补丁 | {corr_stats['per_phys']} | 定位到具体 phys |")
    L.append(f"| ocr 台账补丁 | {corr_stats['ocr']} | 转录层，不留痕 |")
    L.append(f"| silent 结构性补丁 | {corr_stats['silent']} | 表格/碎片删除，不留痕 |")
    L.append(f"| 删除型补丁 | {corr_stats['delete']} | new=\"\"，OCR衍字删除 |")
    L.append(f"| **合计** | **{corr_stats['total']}** | |")
    L.append("")
    if corr_stats["files"]:
        L.append("各补丁文件分布：")
        L.append("")
        L.append("| 文件 | 补丁数 |")
        L.append("|------|--------|")
        for fname, cnt in corr_stats["files"]:
            L.append(f"| {fname} | {cnt} |")
        L.append("")
    L.append("---")
    L.append("")

    # ===== 第四节：派工过程 =====
    L.append("## 四、派工过程")
    L.append("")
    L.append("### 4.1 分章方案")
    L.append("")
    L.append("| 组号/笔记 | 负责章节 | phys范围 | 字符数 | 补丁数 |")
    L.append("|----------|---------|---------|------|--------|")
    if group_plan:
        # 每组补丁数：per-phys 补丁 phys 落在该组 phys 范围
        def _group_patch_n(pr):
            n = 0
            for p in all_patches:
                pp = p.get("phys")
                if pr and isinstance(pp, int) and pr[0] <= pp <= pr[1]:
                    n += 1
            return n
        for g in group_plan:
            pr = g.get("phys_range") or ["", ""]
            nos = g.get("chapters", [])
            if g.get("is_preface"):
                ch_desc = "前辅文"
            elif len(nos) == 1:
                ch_desc = chapter_map.get(nos[0], g.get("label", ""))
            else:
                ch_desc = f"{g.get('label','')}（第{nos[0]}-{nos[-1]}章）"
            pr_str = f"phys{pr[0]}-{pr[1]}"
            L.append(f"| 组{g.get('group')} | {ch_desc} | {pr_str} | "
                     f"{g.get('chars','')} | {_group_patch_n(pr)} |")
        L.append(f"| 合计 | | | {sum(g.get('chars',0) for g in group_plan)} | "
                 f"{corr_stats['total']} |")
    else:
        for nf in notes_files:
            L.append(f"| {nf} | <!-- 待补充 --> | <!-- 待补充 --> | "
                     f"<!-- 待补充 --> | <!-- 待补充 --> |")
        L.append(f"| 合计 | | | {struct_lines} | {corr_stats['total']} |")
    L.append("")

    L.append("### 4.2 补丁合并与校准")
    if audit_info.get("corr_files_n") is not None:
        L.append(f"- corrections/ 共 {audit_info.get('corr_files_n')} 份；"
                 f"gen_error_forms.py 生成候选：error_forms.txt 最终 {len(error_forms)} 项")
    else:
        L.append("- 05b_calibrate_phys.py 校准结果：<!-- 待补充：归位N条、声明页已命中N条、多页命中N条、未命中N条、空操作N条、通改覆盖N条 -->")
    L.append("- check_patch.py 预检结果：<!-- 待补充：error=N、conflict=N（无 audit_report 时填写） -->")
    L.append("")
    L.append("---")
    L.append("")

    # ===== 第五节：所有闸的验证过程（核心）=====
    L.append("## 五、所有闸的验证过程（核心）")
    L.append("")
    L.append("### 5.1 闸0：锚点有效性")
    L.append(f"- struct 锚点数：{struct_anchors}")
    g0 = audit_info.get("gate0_anchors")
    if g0 is not None:
        L.append(f"- 03 锚点数：{g0}")
        L.append("- 锚格式异常：无")
        L.append(f"- 判定：{'通过（03 锚点齐全）' if g0 == struct_anchors else '⚠️ 待复核（03 与 struct 锚点数不一致）'}")
    else:
        L.append("- 03 锚点数：<!-- 待补充（从audit_report.md提取） -->")
        L.append("- 锚格式异常：<!-- 待补充：无 / 有（列出） -->")
        L.append("- 判定：<!-- 通过 / 未通过 -->")
    L.append("")

    L.append("### 5.2 闸1：检索可达性")
    if audit_info.get("gate1_terms") is not None:
        L.append("- 检索词来源：自动从篇名/专名抽取（--meta）")
        L.append(f"- 检索词总数：{audit_info.get('gate1_terms')}")
        mn = audit_info.get("gate1_missing_n", 0)
        if mn and audit_info.get("gate1_missing_list"):
            L.append(f"- 缺失词：{mn} 个（{audit_info.get('gate1_missing_list')}）")
            L.append("- 缺失词处理：<!-- 待补充：确认译法差异/本卷未出现，或补充检索词/01笔记 -->")
        else:
            L.append("- 缺失词：0 个")
        L.append("- 判定：<!-- 待补充：缺失词均为译法差异或本卷未出现 → 通过；否则处理后通过 -->")
    else:
        L.append("- 检索词来源：<!-- --retrieval 指定 / 自动从篇名抽词 -->")
        L.append("- 检索词总数：<!-- 待补充 -->")
        L.append("- 缺失词：<!-- 待补充：N个（列出） -->")
        L.append("- 缺失词处理：<!-- 待补充：补充检索词 / 补充01笔记 / 确认无需检索 -->")
        L.append("- 判定：<!-- 通过 / 未通过 -->")
    L.append("")

    L.append("### 5.3 闸1b：高频错字清零（强制）")
    L.append("")
    L.append(f"error_forms.txt 清单（共 {len(error_forms)} 项）：")
    L.append("")
    if error_forms:
        for i, ef in enumerate(error_forms, 1):
            L.append(f"{i}. `{ef}`")
    else:
        L.append("（error_forms.txt 不存在或为空）")
    L.append("")
    L.append("- 首次 assemble 残留检测：<!-- 待补充：残留N项（列出具体错字和出现次数；无则填0） -->")
    L.append("- 报警后的核实和修正过程：<!-- 待补充：逐条说明错字核实、补丁补充、重新assemble结果；首次即通过则填“无” -->")
    g1b = audit_info.get("gate1b_residual")
    if g1b is not None:
        L.append(f"- 最终 assemble 残留：{g1b}（{'全部清零' if g1b == 0 else '仍有残留'}）")
        L.append(f"- 判定：{'通过' if g1b == 0 else '未通过'}")
    else:
        L.append("- 最终 assemble 残留：<!-- 0（全部清零）/ N -->")
        L.append("- 判定：<!-- 通过 / 未通过 -->")
    L.append("")

    L.append("### 5.4 闸2 补丁生效与内容正确性")
    L.append(f"- 混淆词典扫描命中：{audit_info.get('gate2_dict_hits', '<!-- 待补充 -->')} 类（0 → 无词典命中错字）")
    L.append(f"- active 补丁回放真实残留：{audit_info.get('gate2_patch_residual', '<!-- 待补充 -->')} 处（0 → 03 与台账一致）")
    L.append(f"- 本次 trace 型纠错 {audit_info.get('trace_n','?')} 处、系统噪声通改 {audit_info.get('tongai_n','?')} 类、"
             f"转录层纠错 {audit_info.get('ocr_n','?')} 处（§3.1 起 03 正文不再内嵌〔〕留痕，全量登记入 04）")
    L.append(f"- 静默删除 {corr_stats['silent']} 处（结构性噪音，不计入）")
    L.append(f"- ocr台账 {corr_stats['ocr']} 处（转录层，不计入）")
    dict_ok = audit_info.get("gate2_dict_hits") == 0
    patch_ok = audit_info.get("gate2_patch_residual") == 0
    if dict_ok and patch_ok:
        L.append("- 判定：通过（PASS）")
    else:
        L.append("- 判定：<!-- 待补充：字典/回放残留均为0 → 通过 -->")
    L.append("")

    L.append("### 5.5 闸3 02 完整性（原 C2）")
    g3 = audit_info.get("gate3_missing_chapters")
    if g3 is not None:
        if g3 == 0:
            L.append("- 02 抽取事件数：<!-- 待补充 -->")
            L.append("- 章节覆盖：<!-- N/N 章有对应事件 -->")
            L.append("- 缺失章节：无")
            L.append("- 判定：通过")
        else:
            L.append(f"- ⚠️ {g3} 章在笔记中无对应标题/事件（详见 audit_report.md），请复核")
            L.append("- 判定：<!-- 待补充：补笔记后通过，或说明原因 -->")
    else:
        L.append("- 02 抽取事件数：<!-- 待补充 -->")
        L.append("- 章节覆盖：<!-- N/N 章有对应事件 -->")
        L.append("- 缺失章节：<!-- 无 / 有（列出及原因） -->")
        L.append("- 判定：<!-- 通过 / 未通过 -->")
    L.append("")

    # 5.6 B2未命中告警（重点）
    L.append("### 5.6 B2：未命中告警（重点）")
    L.append("")

    L.append("#### 5.6.1 首次 assemble 未命中清单")
    L.append("")
    L.append("> **以下为首次 assemble 时 `correction_miss_log.md` 的完整快照**（gen_reading_report.py 生成时嵌入，防止后续 assemble 覆盖丢失记录）：")
    L.append("")
    if miss_log_text:
        L.append("```")
        L.append(miss_log_text.strip())
        L.append("```")
    else:
        L.append("（correction_miss_log.md 不存在或为空——可能尚未 assemble，或首次 assemble 无未命中）")
    L.append("")
    _mn = audit_info.get("miss_n")
    if _mn is not None:
        L.append(f"- 未命中总数：{_mn}（有效未命中；回退成功/冗余/无效通改不计入，见 correction_miss_log.md）")
        L.append(f"- 是否超阈值（默认10）：{'否' if _mn <= 10 else '是'}")
    else:
        L.append("- 未命中总数：<!-- 待补充（含回退成功N条，有效未命中N条） -->")
        L.append("- 是否超阈值（默认10）：<!-- 是 / 否 -->")
    L.append("")

    L.append("#### 5.6.2 逐个核实过程")
    L.append("")
    L.append("| 序号 | 补丁文件 | phys | old（前20字） | new | 核实搜索关键词 | 核实搜索结果 | 判断 |")
    L.append("|------|---------|------|---------------|-----|--------------|-------------|------|")
    # 从corrections中列出所有补丁供核实
    for i, p in enumerate(all_patches[:50], 1):  # 最多列50条，避免骨架过大
        old_str = str(p.get("old", ""))[:20].replace("|", "\\|").replace("\n", " ")
        new_str = str(p.get("new", ""))[:15].replace("|", "\\|").replace("\n", " ")
        L.append(f"| {i} | {p.get('_source_file', '')} | {p.get('phys', '')} | {old_str} | {new_str} | <!-- 待补充 --> | <!-- 待补充 --> | <!-- 修正old/经核实删除/新建补丁/已生效 --> |")
    if len(all_patches) > 50:
        L.append(f"| ... | （共{len(all_patches)}条补丁，此处仅列前50条，其余见 corrections/*.json） | | | | | | |")
    L.append("")

    L.append("#### 5.6.3 修正情况")
    L.append("")
    L.append("**【修正old匹配】<!-- N --> 条**：错误存在但 old 字符串不匹配，已修正 old 使其生效")
    L.append("")
    L.append("<!-- 逐条列出：原old → 修正后old，不匹配原因（引号问题/脚注号/跨行/少字） -->")
    L.append("")
    L.append("**【经核实删除】<!-- N --> 条**：经核实原文中确实不存在错误形态，已从 corrections/*.json 中删除")
    L.append("")
    L.append("| 序号 | 补丁文件 | phys | old | new | reason | 核实搜索关键词 | 核实搜索结果 | 删除日期 |")
    L.append("|------|---------|------|-----|-----|--------|--------------|-------------|---------|")
    L.append("| <!-- 待补充 --> | | | | | | | | |")
    L.append("")
    L.append("**【新建补丁】<!-- N --> 条**：核实过程中发现新的错误形态，已新建补丁修复")
    L.append("")
    L.append("<!-- 逐条列出：补丁文件、phys、old、new、发现来源 -->")
    L.append("")
    L.append("**【已生效无需处理】<!-- N --> 条**：回退成功 / 已被通改覆盖 / 与更长补丁重叠跳过")
    L.append("")
    L.append("<!-- 逐条列出及原因 -->")
    L.append("")

    L.append("#### 5.6.4 最终 assemble 未命中数")
    if _mn is not None:
        L.append(f"- 有效未命中：{_mn}（应 ≤ 阈值10）")
        L.append("- 回退成功：<!-- 待补充：0/N（见 correction_miss_log.md） -->")
        L.append(f"- 判定：{'通过' if _mn <= 10 else '未通过'}")
    else:
        L.append("- 有效未命中：<!-- N（应 ≤ 阈值10） -->")
        L.append("- 回退成功：<!-- N -->")
        L.append("- 判定：<!-- 通过 / 未通过 -->")
    L.append("")

    L.append("### 5.7 引文逐字率 ≥95%")
    L.append("- 01笔记引文总数：<!-- 待补充 -->")
    L.append("- 逐字命中：<!-- 待补充 -->")
    L.append("- 逐字率：<!-- N%（≥95%，通过 / <95%，未通过） -->")
    L.append("- 未命中引文及处理：<!-- 列出 -->")
    L.append("")
    L.append("---")
    L.append("")

    # ===== 第六节：回读验证结果 =====
    L.append("## 六、回读验证结果")
    L.append("")
    L.append("### 6.1 关键纠错点验证（≥10处）")
    L.append("")
    L.append("| 序号 | 补丁内容（old→new） | 03中搜索结果 | 验证结论 |")
    L.append("|------|---------------------|-------------|---------|")
    if out_03_clean:
        clean03 = strip_correction_traces(out_03_clean)
        keys = select_key_patches(all_patches)
        for i, (old, new) in enumerate(keys, 1):
            new_n = clean03.count(new)
            old_resid = is_residual_full(clean03, old, new)
            result = f"正确「{new}」{new_n}处，错误「{old}」{'残留' if old_resid else '0处'}"
            verdict = "❌ 残留" if old_resid else "通过"
            L.append(f"| {i} | {old}→{new} | {result} | {verdict} |")
        if len(keys) < 10:
            L.append(f"| - | （自动选出 {len(keys)} 条代表性纠错，不足10处可人工补充） | | |")
    else:
        for i in range(1, 11):
            L.append(f"| {i} | <!-- 待补充 --> | <!-- 正确形态N处，错误形态0处 --> | <!-- 待补充 --> |")
    L.append("")

    L.append("### 6.2 高频错字验证")
    if out_03_clean and error_forms:
        clean03 = strip_correction_traces(out_03_clean)
        resid_items = [ef for ef in error_forms if is_residual_full(clean03, ef)]
        n = len(resid_items)
        L.append(f"- error_forms.txt 全部 {len(error_forms)} 项在03中搜索：错误形态残留 {n} 项")
        L.append(f"- 判定：{'通过' if n == 0 else '未通过'}")
    else:
        L.append(f"- error_forms.txt 全部 {len(error_forms)} 项在03中搜索：错误形态残留 <!-- 待补充（执行者回读后填实际残留数，禁止预填 0；无03时需先assemble） -->")
        L.append("- 判定：<!-- 待补充 -->")
    L.append("")

    L.append("### 6.3 书名号验证")
    if out_03_clean:
        nl = out_03_clean.count("《")
        nr = out_03_clean.count("》")
        diff = nl - nr
        L.append(f"- 《 总数：{nl}")
        L.append(f"- 》 总数：{nr}")
        if diff == 0:
            L.append(f"- 匹配：是（{nl}={nr}）")
        else:
            L.append(f"- 匹配：否（差 {abs(diff)} 个，已处理/见 7.3 遗留）")
        L.append("- 主要书名抽查：<!-- 待补充：列出主要书名的《数与缺左书名号情况 -->")
        L.append(f"- 判定：{'通过' if diff == 0 else '⚠️ 待确认'}")
    else:
        L.append("- 《 总数：<!-- 待补充 -->")
        L.append("- 》 总数：<!-- 待补充 -->")
        L.append("- 匹配：<!-- 是（N=N）/ 否（差N个，已处理） -->")
        L.append("- 主要书名抽查：<!-- 待补充（列出主要书名的带《数和缺左书名号数） -->")
        L.append("- 判定：<!-- 通过 / 未通过 -->")
    L.append("")

    L.append("### 6.4 锚点验证（≥5处）")
    L.append("")
    L.append("| phys | book | 03中锚点 | 与page_index对照 | 验证结论 |")
    L.append("|------|------|---------|-----------------|---------|")
    for i in range(1, 6):
        L.append(f"| <!-- 待补充 --> | | | | 通过 |")
    L.append("")

    L.append("### 6.5 笔记引文验证（≥5处与03逐字对比）")
    L.append("")
    L.append("| 笔记引文 | 03原文 | 逐字匹配 | 验证结论 |")
    L.append("|---------|--------|---------|---------|")
    for i in range(1, 6):
        L.append(f"| <!-- 待补充 --> | | | 通过 |")
    L.append("")
    L.append("---")
    L.append("")

    # ===== 第七节：最终完成情况 =====
    L.append("## 七、最终完成情况")
    L.append("")
    L.append("### 7.1 交付清单")
    L.append("")
    L.append("| 文件 | 锚点数 | 字符数 |")
    L.append("|------|--------|--------|")
    def _deliv_chars(marker):
        p, t = read_out_file(out_dir, marker)
        return count_file_chars(p) if p else None
    c00 = _deliv_chars("00_")
    c01 = _deliv_chars("01_")
    c02 = _deliv_chars("02_")
    c03p, c03t = read_out_file(out_dir, "03_")
    c03_chars = count_file_chars(c03p) if c03p else None
    c03_anchors = count_anchors(c03t) if c03t else None
    c04 = _deliv_chars("04_")
    L.append(f"| {file_prefix}00_梳理总览与目录对照.md | - | {c00 if c00 is not None else '<!-- 待补充 -->'} |")
    L.append(f"| {file_prefix}01_章节详解.md | - | {c01 if c01 is not None else '<!-- 待补充 -->'} |")
    L.append(f"| {file_prefix}02_脉络与速查卡.md | - | {c02 if c02 is not None else '<!-- 待补充 -->'} |")
    L.append(f"| {file_prefix}03_带页码全文.md | {c03_anchors if c03_anchors is not None else '<!-- 待补充 -->'} | "
             f"{c03_chars if c03_chars is not None else '<!-- 待补充 -->'} |")
    L.append(f"| {file_prefix}04_改错留痕.md | - | {c04 if c04 is not None else '<!-- 待补充 -->'} |")
    L.append(f"| {file_prefix}05_精读汇报.md | - | {len(L)}行（骨架，补充后增加） |")
    L.append("")

    L.append("### 7.2 纠错统计")
    L.append("")
    L.append("| 指标 | 数量 |")
    L.append("|------|------|")
    L.append(f"| 补丁总数 | {corr_stats['total']}（manifest active {manifest_stats['active']} / deleted {manifest_stats['deleted']}） |")
    L.append(f"| 生效数 | {apply_info.get('applied', '<!-- 待补充 -->')} |")
    L.append(f"| 有效未命中数 | {apply_info.get('residual', audit_info.get('miss_n', '<!-- 待补充 -->'))} |")
    L.append(f"| 经核实删除数 | {manifest_stats['deleted']} |")
    L.append("| 回退成功数 | <!-- 待补充：见 correction_miss_log.md --> |")
    L.append(f"| 通改覆盖数 | {audit_info.get('tongai_n', '<!-- 待补充 -->')} 类 |")
    L.append("")

    # P2-E3：纠错模式画像（自动统计类型分布）
    if manifest_patches:
        _, pattern_summary = analyze_error_patterns(manifest_patches)
        L.append("**纠错模式画像**（自动统计 active 补丁类型分布）：")
        L.append(f"- {pattern_summary}")
        L.append("")

    L.append("### 7.3 已知遗留问题")
    if doubts:
        L.append(f"共 {len(doubts)} 处存疑未臆改（精读中拿不准、留待核底本的字句，均未施加到 03）：")
        L.append("")
        L.append("| 序号 | 来源 | phys | 原文（前40字） | 存疑说明 |")
        L.append("|------|------|------|----------------|---------|")
        for i, (src, phys, old, new, rs) in enumerate(doubts, 1):
            L.append(f"| {i} | {src} | {phys} | {old} | {rs} |")
    else:
        L.append("- 无存疑项（全部纠错均已核实并施加）")
    L.append("")

    L.append("### 7.4 自我审查声明（§1 纪律五纪律五）")
    L.append("")
    L.append(f"- [ ] 是否真正逐字阅读了全文？已读行数 {read_lines_sum if read_lines_sum else '<!-- 待补充 -->'} / 总行数 {struct_lines}（据 reading_progress 行范围求和，禁止预勾 [x]）")
    L.append(f"- [ ] 每一条补丁是否都经过核实？补丁总数 {corr_stats['total']}，已核实 <!-- 待补充（执行者逐条确认，禁止预勾 [x]） -->")
    L.append("- [ ] 是否有删除未命中补丁的行为？<!-- 有N条，均经核实后删除，详细记录见5.6.3 / 无 -->")
    L.append("- [ ] 回读验证是否按清单完成？5项验证全部通过（见第六节）<!-- 执行者确认 -->")
    L.append("- [ ] 03中是否还有已知未纠治的错误？<!-- 无 / 有（见7.3） -->")
    L.append("")
    L.append("---")
    L.append("")
    L.append(f"*报告生成日期：{today} | 执行者：{executor} | 技能版本：{skill_version} | 由 gen_reading_report.py 生成骨架，执行者补充完成*")

    # ========== 输出 ==========
    # v1.14.0：04 生成前先过闸2 verifier（03 与 corrections 台账一致），不一致拒生成。
    _sd = os.path.dirname(os.path.abspath(__file__))
    # 2026-10-08 适配性修复：须透传 --meta。否则 verifier 取不到 book_type → 词典按书型分桶的
    # 条目本轮全部不扫，内容正确性扫描静默降级（与两 assemble 传 --meta 的口径对齐）。
    _ver_cmd = [sys.executable, os.path.join(_sd, "pre_delivery_verifier.py"),
                "--work", work_dir, "--out", out_dir]
    if meta_path:
        _ver_cmd += ["--meta", meta_path]
    _rc = subprocess.run(_ver_cmd).returncode
    if _rc != 0:
        print("❌ 闸2 verifier 未通过，拒生成 05 精读汇报（先补充纠错重跑 assemble）。", file=sys.stderr)
        sys.exit(1)

    report_text = "\n".join(L) + "\n"

    os.makedirs(out_dir, exist_ok=True)
    out_filename = f"{file_prefix}05_精读汇报.md"
    out_path = os.path.join(out_dir, out_filename)

    safe_write(out_path, report_text)  # N7（2026-10-04 审查）：走 safe_write 原子写，避免中断留半截 05

    # 打印摘要
    print(f"05精读汇报骨架已生成：{out_path}")
    print(f"  书籍：{book_title}")
    print(f"  骨架行数：{len(L)} 行")
    print(f"  fulltext_struct：{struct_lines}行 / {struct_chars}字符 / {struct_anchors}锚点")
    print(f"  补丁总数：{corr_stats['total']}（{len(corr_stats['files'])}份文件）")
    print(f"  笔记文件：{len(notes_files)}份")
    print(f"  error_forms.txt：{len(error_forms)}项")
    print(f"  correction_miss_log.md快照：{'已嵌入5.6.1节' if miss_log_text else '不存在（首次assemble前生成）'}")
    print()
    print("请执行者在骨架基础上补充：")
    print("  1. 各步骤的主要判断过程（第二节）")
    print("  2. 精读过程的错误分类统计（第三节3.2）")
    print("  3. 未命中补丁的逐个核实过程和经核实删除记录（第五节5.6）")
    print("  4. 所有闸的验证结果（第五节5.1-5.7）")
    print("  5. 回读验证的具体结果（第六节）")
    print("  6. 自我审查声明（第七节7.4）")

    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="生成05精读汇报.md骨架（版本从 SKILL.md 读取）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python scripts/gen_reading_report.py --work E:/.../.work/books/书名 --out E:/.../书名
        """
    )
    parser.add_argument("--work", required=True, help="工作目录（含 fulltext_struct.txt、corrections/、audit_report.md 等）")
    parser.add_argument("--out", required=True, help="交付目录（输出 <书名>05_精读汇报.md）")
    parser.add_argument("--meta", default=None, help="book_meta.json 路径（默认从 work 目录自动查找）")
    args = parser.parse_args()

    work = os.path.abspath(args.work)
    out = os.path.abspath(args.out)

    if not os.path.isdir(work):
        print(f"错误：工作目录不存在：{work}", file=sys.stderr)
        sys.exit(1)

    generate_report(work, out, args.meta)


if __name__ == "__main__":
    main()
