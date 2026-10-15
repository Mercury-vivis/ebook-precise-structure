#!/usr/bin/env python3
# gen_brief.py —— 自动生成派工公共规范 _brief_common.md（P1-A1，v1.27.0）
#
# 用法：python gen_brief.py <WORK目录> [--meta book_meta.json]
# 读取 book_meta + chapters/_chapters.json + simplify_leak_report + error_forms.candidates，
# 输出 work/_brief_common.md（格式/口径/铁律/章节切分/通改候选/简繁结论）。
#
# 设计原则：纯增量、不臆造——book_meta 缺的字段留占位（执行者补），不编默认值。

import os, sys, json, argparse, re
from _common import read_text_cn, read_json_cn, safe_write, load_proper_names_lib


def load_chapters(work):
    """读 chapters/_chapters.json，返回 [{no,name,phys_start,phys_end,...}]"""
    p = os.path.join(work, "chapters", "_chapters.json")
    if not os.path.isfile(p):
        return []
    return read_json_cn(p)


def count_chapter_lines(work, ch):
    """统计章节文件实际行数和字符数（_chapters.json 无 chars 字段，需自行统计）"""
    import glob
    pat = os.path.join(work, "chapters", f"{ch['no']:02d}_*.txt")
    files = glob.glob(pat)
    if not files:
        return 0, 0
    text = read_text_cn(files[0])
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    return lines, len(text)


def load_simplify_leak(work):
    """读 simplify_leak_report.md，提取通改候选/简形独占/混用占比三档结论"""
    p = os.path.join(work, "simplify_leak_report.md")
    if not os.path.isfile(p):
        return None
    text = read_text_cn(p)
    # 提取关键统计行
    result = {"raw": text[:2000]}
    for key in ["通改候选", "简形独占", "混用占比", "建议"]:
        m = re.search(rf"{key}[：:]\s*(.+)", text)
        if m:
            result[key] = m.group(1).strip()[:200]
    return result


def load_error_candidates(work):
    """读 error_forms.candidates.txt，提取前20条高频候选"""
    p = os.path.join(work, "error_forms.candidates.txt")
    if not os.path.isfile(p):
        return []
    text = read_text_cn(p)
    cands = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) >= 2 and parts[0].strip() and parts[1].strip():
            cands.append((parts[0].strip(), parts[1].strip()))
        if len(cands) >= 20:
            break
    return cands


def load_proper_names():
    """P1-E1：读跨卷专名标准库，返回已确认条目。

    v1.28.0：路径与 schema 归一收口到 `_common.load_proper_names_lib`（单一真相源）——
    此前本函数与 gen_dispatch_brief 各自拼路径、且 references/ 与 resources/ 两份库
    schema 分裂互不通用。现在两脚本同读一份、同归一化。
    """
    return load_proper_names_lib(confirmed_only=True)["names"]


def gen_brief(work, meta_path=None):
    meta_path = meta_path or os.path.join(work, "book_meta.json")
    if not os.path.isfile(meta_path):
        print(f"❌ book_meta.json 不存在：{meta_path}", file=sys.stderr)
        sys.exit(2)
    meta = read_json_cn(meta_path)
    chapters = load_chapters(work)
    simplify = load_simplify_leak(work)
    candidates = load_error_candidates(work)
    proper_names = load_proper_names()

    # 书籍信息
    title = meta.get("title", "（待填书名）")
    author = meta.get("author", "（待填作者）")
    publisher = meta.get("publisher", "（待填出版社）")
    book_type = meta.get("book_type", "（待填书类型）")
    total_phys = meta.get("total_pages", "")
    offset = meta.get("offset", "")

    lines = []
    lines.append("# 公共精读规范（所有组必须遵守）")
    lines.append("")
    lines.append("## 书籍信息")
    lines.append(f"- 书名：《{title}》")
    lines.append(f"- 作者：{author}")
    lines.append(f"- 出版社：{publisher}")
    lines.append(f"- 书类型：{book_type}")
    lines.append(f"- 底本：{'繁体' if meta.get('is_traditional') else '简体/待确认'}，OCR/提取产出")
    if total_phys:
        lines.append(f"- 总篇幅：{total_phys}页")
    if offset:
        lines.append(f"- 页码锚：`<<<PAGE phys=N book=M>>>`，phys=物理页序，book=原书印页(book=phys-{offset})")
    lines.append("")

    # 章节切分
    lines.append("## 章节切分")
    if chapters:
        for ch in chapters:
            no = ch.get("no", 0)
            name = ch.get("name", "")
            ps = ch.get("phys_start", "")
            pe = ch.get("phys_end", "")
            cl, cc = count_chapter_lines(work, ch)
            lines.append(f"- ch{no:02d} {name}：phys{ps}-{pe}（{cl}行/{cc}字）")
    else:
        lines.append("- （chapters/_chapters.json 不存在，请先跑 pipeline_split.py）")
    lines.append("")

    # 双产出要求
    lines.append("## 双产出要求")
    lines.append("")
    lines.append("### 产出一：纠错补丁 corrections/NN_corrections.json")
    lines.append("JSON数组，每条补丁字段：")
    lines.append('```json')
    lines.append('{"phys": 123, "old": "上下文≥6字的错误原文", "new": "修正后文字", "reason": "判据说明", "type": "ocr转录层/底本层/存疑", "source": "形近可证/语境可证/版本学可证"}')
    lines.append('```')
    lines.append("- 转录层错误（OCR误读）必须加 `\"ocr\": true`")
    lines.append("- old 带≥6字上下文防偏页；per-phys 补丁 old 写 body0 形态（先施 systematic 通改后）")
    lines.append("- 拿不准的标 type=存疑，不臆改")
    lines.append("")
    lines.append("### 产出二：分析笔记 notes/NN.md")
    lines.append("- 忠实原文，数字/人名/年代/术语不臆造")
    lines.append("- 每条带原书页码锚点（原书页 M 或 SEC idx=N）")
    lines.append("- 正文叙述段严守铁律：转述、禁整段原文搬运、金句每节≤2句、占原篇幅≤40%")
    lines.append("")

    # 通改候选
    if candidates:
        lines.append("## 高频通改候选（请确认是否升级为 systematic 全局通改）")
        for wrong, right in candidates:
            lines.append(f"- `{wrong}` → `{right}`")
        lines.append("")

    # 跨卷专名标准库（P1-E1：提示性、人工确认，不自动改字）
    if proper_names:
        lines.append("## 跨卷专名标准（已确认，仅供参考·不自动改字）")
        lines.append("> 以下专名标准来自已完成卷的跨卷验证，OCR常见误读形态已标注。纠错时请参考，但须结合本书语境确认，不臆改。")
        for pn in proper_names:
            wrongs = "、".join(f"「{w}」" for w in pn.get("wrong_forms", []))
            note = f"（{pn['note']}）" if pn.get("note") else ""
            lines.append(f"- **{pn['name']}**：常见误读 {wrongs}{note}")
        lines.append("")

    # 简繁抖动结论
    if simplify:
        lines.append("## 简繁码位抖动结论")
        for key in ["通改候选", "简形独占", "混用占比", "建议"]:
            if key in simplify:
                lines.append(f"- {key}：{simplify[key]}")
        lines.append("")

    # P2-F1（v1.27.0）：第二轮软提示——注入八卷精读实战中常见的漏校模式（软提示，不强制）
    lines.append("## 常见漏校模式提示（来自八卷精读实战·软提示·不强制）")
    lines.append("> 以下为多卷精读中反复出现的漏校模式，纠错时请特别留意，但须结合本书语境确认，不臆改。")
    lines.append("1. **专名多变体**：同一专名可能有多种OCR错形（如「奕訢」→奕诉/奕忻、「吴大澂」→吴大澄/吴大徵），线性阅读易漏，建议对高频专名做全书grep")
    lines.append("2. **曰/日混用**：OCR常把「曰」识别为「日」（如「李鸿章电总理衙门日」应为「曰」），高频者列入通改清单")
    lines.append("3. **书名号缺失**：古籍/文献名常缺《》，须补全书名号（但原书无书名号的底本层错误不臆补）")
    lines.append("4. **形近字成族**：「轇轕」常误为「缪轼/缪辑/轸稿/纠葛」，「蹂躏」常误为「蹂躙」，成族错形建议合并通改")
    lines.append("5. **数字脱漏**：年份/编号中的「〇」常脱漏（如「一九〇五年」→「一九五年」），须核对")
    lines.append("6. **衍字删除**：OCR常多出字（如「了」「的」「一」），删除型补丁new为空，须确认非原书所有")
    lines.append("7. **跨页断词**：old跨页边界时全书0命中，须缩短old到单页内子串（05b_calibrate_phys会提示跨页断裂）")
    lines.append("8. **phys误用印页**：补丁phys字段须填物理页序（phys=N），非原书印页（book=M），offset≠0时易混淆")
    lines.append("")

    # 铁律
    lines.append("## 执行铁律")
    lines.append("1. 逐字阅读与纠错同步进行，不得先纠错后补笔记、不得跳过阅读")
    lines.append("2. 阅读进度记 reading_progress.md（行范围+发现错误数），覆盖率须100%")
    lines.append("3. 03正文一律不留记号（〔〕留痕不写入正文）")
    lines.append("4. 绝不无声改字、绝不臆改年代数字（疑则标不猜）")
    lines.append("5. 高频系统噪声列通改清单，正文直接写修正后字")
    lines.append("6. 古籍集注本务必保留夹注，不可当噪声删")
    lines.append("")

    content = "\n".join(lines) + "\n"
    out = os.path.join(work, "_brief_common.md")
    safe_write(out, content)
    print(f"✅ 已生成 {out}（{len(content)}字，{len(chapters)}章，{len(candidates)}条通改候选）")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--meta", default=None)
    args = ap.parse_args()
    gen_brief(args.work, args.meta)


if __name__ == "__main__":
    main()
