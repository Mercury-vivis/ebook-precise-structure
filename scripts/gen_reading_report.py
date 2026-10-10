#!/usr/bin/env python3
"""
gen_reading_report.py — 生成05精读汇报.md骨架（v1.14.0 改写：版本/执行者去硬编码，05 生成前过闸2 verifier）

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
from _common import derive_prefix, safe_write  # v1.15.1：交付件前缀=目录名（去_OCR/_文字版标注），六件统一；safe_write=N7 原子写（2026-10-04）


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
        L.append(f"| 作者 | {meta.get('author', meta.get('译者', '<!-- 待补充 -->'))} |")
        L.append(f"| 出版社 | {meta.get('publisher', '<!-- 待补充 -->')} |")
        L.append(f"| 出版时间 | {meta.get('pub_date', meta.get('date', '<!-- 待补充 -->'))} |")
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
    L.append(f"- 已读行数：<!-- 待补充（执行者据 reading_progress.md 确认实际已读行数，禁止默认填 {struct_lines}） -->")
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
    L.append("| 组号/笔记 | 负责章节 | phys范围 | 行数 | 补丁数 |")
    L.append("|----------|---------|---------|------|--------|")
    for nf in notes_files:
        L.append(f"| {nf} | <!-- 待补充 --> | <!-- 待补充 --> | <!-- 待补充 --> | <!-- 待补充 --> |")
    L.append(f"| 合计 | | | {struct_lines} | {corr_stats['total']} |")
    L.append("")

    L.append("### 4.2 补丁合并与校准")
    L.append("- 05b_calibrate_phys.py 校准结果：<!-- 待补充：归位N条、声明页已命中N条、多页命中N条、未命中N条、空操作N条、通改覆盖N条 -->")
    L.append("- check_patch.py 预检结果：<!-- 待补充：error=N、conflict=N -->")
    L.append(f"- gen_error_forms.py 生成候选：error_forms.txt 最终 {len(error_forms)} 项")
    L.append("")
    L.append("---")
    L.append("")

    # ===== 第五节：所有闸的验证过程（核心）=====
    L.append("## 五、所有闸的验证过程（核心）")
    L.append("")
    L.append("### 5.1 闸0：锚点有效性")
    L.append(f"- struct 锚点数：{struct_anchors}")
    L.append("- 03 锚点数：<!-- 待补充（从audit_report.md提取） -->")
    L.append("- 锚格式异常：<!-- 待补充：无 / 有（列出） -->")
    L.append("- 判定：<!-- 通过 / 未通过 -->")
    L.append("")

    L.append("### 5.2 闸1：检索可达性")
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
    L.append("- 首次 assemble 残留检测：<!-- 待补充：残留N项（列出具体错字和出现次数） -->")
    L.append("- 报警后的核实和修正过程：<!-- 待补充：逐条说明错字核实、补丁补充、重新assemble结果 -->")
    L.append("- 最终 assemble 残留：<!-- 0（全部清零）/ N -->")
    L.append("- 判定：<!-- 通过 / 未通过 -->")
    L.append("")

    L.append("### 5.4 闸2 补丁生效（原 C1）")
    L.append("- 已施用留痕型补丁数：<!-- 待补充 -->")
    L.append("- 03 留痕数：<!-- 待补充 -->")
    L.append("- 不等式：03留痕 ≥ 已施用留痕型补丁（<!-- N ≥ N，通过 -->）")
    L.append(f"- 静默删除 {corr_stats['silent']} 处（结构性噪音，不计入不等式）")
    L.append(f"- ocr台账 {corr_stats['ocr']} 处（转录层，不计入不等式）")
    L.append("")

    L.append("### 5.5 闸3 02 完整性（原 C2）")
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
    for i in range(1, 11):
        L.append(f"| {i} | <!-- 待补充 --> | <!-- 正确形态N处，错误形态0处 --> | <!-- 待补充 --> |")
    L.append("")

    L.append("### 6.2 高频错字验证")
    L.append(f"- error_forms.txt 全部 {len(error_forms)} 项在03中搜索：错误形态残留 <!-- 待补充（执行者回读后填实际残留数，禁止预填 0） -->")
    L.append("- 判定：<!-- 待补充 -->")
    L.append("")

    L.append("### 6.3 书名号验证")
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
    L.append(f"| {file_prefix}00_梳理总览与目录对照.md | - | <!-- 待补充 --> |")
    L.append(f"| {file_prefix}01_章节详解.md | - | <!-- 待补充 --> |")
    L.append(f"| {file_prefix}02_脉络与速查卡.md | - | <!-- 待补充 --> |")
    L.append(f"| {file_prefix}03_带页码全文.md | <!-- 待补充 --> | <!-- 待补充 --> |")
    L.append(f"| {file_prefix}04_改错留痕.md | - | <!-- 待补充 --> |")
    L.append(f"| {file_prefix}05_精读汇报.md | - | {len(L)}行（骨架，补充后增加） |")
    L.append("")

    L.append("### 7.2 纠错统计")
    L.append("")
    L.append("| 指标 | 数量 |")
    L.append("|------|------|")
    L.append(f"| 补丁总数 | {corr_stats['total']} |")
    L.append("| 生效数 | <!-- 待补充 --> |")
    L.append("| 有效未命中数 | <!-- 待补充 --> |")
    L.append("| 经核实删除数 | <!-- 待补充 --> |")
    L.append("| 回退成功数 | <!-- 待补充 --> |")
    L.append("| 通改覆盖数 | <!-- 待补充 --> |")
    L.append("")

    L.append("### 7.3 已知遗留问题")
    L.append("- <!-- 无 / 有（列出，说明原因和后续计划） -->")
    L.append("")

    L.append("### 7.4 自我审查声明（§1 纪律五纪律五）")
    L.append("")
    L.append(f"- [ ] 是否真正逐字阅读了全文？已读行数 <!-- 待补充 --> / 总行数 {struct_lines}（执行者据 reading_progress.md 确认覆盖率，禁止预勾 [x]）")
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
