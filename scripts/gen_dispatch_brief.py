#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_dispatch_brief.py —— 派工 brief 自动生成（P1-A1，v1.27.0；v2.3.0 与 gen_brief 收口渲染）

消费 A2 的 group_plan.json + book_meta.json + chapters/_chapters.json，
为每个并行精读 group 渲染独立 brief，输出 work/briefs/group_NN.md。

替代主 Agent 手工拼接 brief 的重复劳动，杜绝：
  ① 章列表漏前辅文/目录页（三国志案例，SKILL.md §5.1）；
  ② 专名清单 proper_names 人工抄录遗漏；
  ③ 前卷经验跨卷传递靠记忆；
  ④ 行范围/phys 范围人工数错。

v2.3.0 收口
  - 公共段落（书籍信息 / 跨卷专名 / 通改候选 / 简繁 / 漏校模式 / 铁律）统一走
    `_common.brief_sections()`，与 gen_brief 同一真相源——此前两处各写一套，
    本脚本丢了「通改候选」与「8 条漏校模式」两段，且键名读法不一致。
  - 小节序号改**动态编号**：旧版写死「一、三、四、八、九」，中间段为空时跳号，
    子 agent 会误以为文件被截断。
  - `phys_end` 由 `_common.derive_phys_ends` 推导（schema 已标可省略）。

用法：
    python scripts/gen_dispatch_brief.py --work <work> [--meta <book_meta.json>]
        [--groups <group_plan.json>] [--prev-experience <上一卷work>]
        [--simplify-leak <simplify_leak_report.md>]
        [--proper-names off|all|topic] [--topic <词>]

输出：
    work/briefs/group_01.md ... group_NN.md
    work/briefs/_index.md（各组概览）
"""
import argparse
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _common import (read_text_cn, read_json_cn, safe_write, expand_patch_data,
                     load_proper_names_lib, derive_phys_ends, check_meta_keys,
                     brief_sections, select_cross_book_names, load_simplify_leak)

CN_NUM = "一二三四五六七八九十十一十二十三十四十五"


def load_chapters_meta(work):
    """读 chapters/_chapters.json，返回 {no: {name, phys_start, phys_end, ...}}。"""
    p = os.path.join(work, "chapters", "_chapters.json")
    if not os.path.isfile(p):
        return {}
    data = read_json_cn(p)
    return {c["no"]: c for c in data}


def load_group_plan(work, groups_path):
    """读 group_plan.json（A2 产出）。"""
    if groups_path and os.path.isfile(groups_path):
        return read_json_cn(groups_path)
    default = os.path.join(work, "group_plan.json")
    if os.path.isfile(default):
        return read_json_cn(default)
    return None


def fill_phys_end_from_meta(chapters_meta, meta):
    """_chapters.json 缺 phys_end 时，用 book_meta 章表推导值补上（原地）。"""
    by_no = {c.get("no"): c for c in (meta.get("chapters") or [])}
    for no, ch in chapters_meta.items():
        if ch.get("phys_end") is None:
            src = by_no.get(no) or {}
            if isinstance(src.get("phys_end"), int):
                ch["phys_end"] = src["phys_end"]


def extract_prev_experience(prev_work):
    """从上一卷 work 目录提取高频错形经验（提示性，不强制）。

    读 corrections/*.json 的 systematic 补丁和高频 per-phys 补丁，
    提取 old→new 对，按出现次数排序，取 top 20。
    """
    if not prev_work or not os.path.isdir(prev_work):
        return []
    corr_dir = os.path.join(prev_work, "corrections")
    if not os.path.isdir(corr_dir):
        return []
    pairs = {}  # (old, new) -> count
    for fp in sorted(glob.glob(os.path.join(corr_dir, "*.json"))):
        try:
            data = read_json_cn(fp)
        except Exception:
            continue
        if isinstance(data, dict):
            data = [data]
        for c in data:
            if not isinstance(c, dict):
                continue
            old = c.get("old", "")
            new = c.get("new", "")
            if not old or old == new:
                continue
            if len(old) < 2:  # 跳过单字，避免噪声
                continue
            key = (old, new)
            pairs[key] = pairs.get(key, 0) + 1
    top = sorted(pairs.items(), key=lambda x: -x[1])[:20]
    return [{"old": o, "new": n, "count": c} for (o, n), c in top]


def extract_round1_patterns(work, top_n=15):
    """P2-F1（v1.27.0）：从本书第一轮 corrections 提取高频漏校模式，作第二轮软提示。

    第二轮精读时，把第一轮已发现的高频错形注入 brief（软提示，不强制），
    提醒子 agent 重点核对同类错形是否还有遗漏。返回 [(old, new, count), ...]。
    """
    corr_dir = os.path.join(work, "corrections")
    if not os.path.isdir(corr_dir):
        return []
    pairs = {}
    for fp in sorted(glob.glob(os.path.join(corr_dir, "*.json"))):
        try:
            data = read_json_cn(fp)
        except Exception:
            continue
        data = expand_patch_data(data) if isinstance(data, (dict, list)) else []
        for p in data:
            if not isinstance(p, dict) or p.get("merge"):
                continue
            if p.get("status") == "deleted":
                continue
            old = (p.get("old") or "").strip()
            new = (p.get("new") or "").strip()
            if not old or not new or old == new or len(old) < 2:
                continue
            key = (old, new)
            pairs[key] = pairs.get(key, 0) + 1
    top = sorted(pairs.items(), key=lambda x: -x[1])[:top_n]
    return [(o, n, c) for (o, n), c in top]


def filter_proper_names_for_group(proper_names, group_chapters, chapters_meta):
    """按本组章范围过滤专名：若专名出现在本组任一章名中则优先保留。

    简单策略：章名含专名 → 标记"本章重点"；其余专名全部保留（专名库通常不大，
    全量注入比过滤更安全，避免漏校）。返回 (重点专名列表, 全部专名列表)。
    """
    if not proper_names:
        return [], []
    group_chapter_names = []
    for no in group_chapters:
        ch = chapters_meta.get(no, {})
        if ch.get("name"):
            group_chapter_names.append(ch["name"])
    key_names = []
    for pn in proper_names:
        for cn in group_chapter_names:
            if pn in cn:
                key_names.append(pn)
                break
    return key_names, proper_names


def render_brief(meta, group, chapters_meta, proper_names, prev_exp,
                 simplify_leak, work_dir, cross_names=None, round1_patterns=None,
                 candidates=None, common_sections=None):
    """渲染单个 group 的 brief（基于 SKILL.md §5 派工模板）。

    公共段落由 `_common.brief_sections()` 统一产出（book_info / candidates /
    proper_names / simplify / patterns），本函数只负责分组维度（章表、phys 区间、
    重点专名、前卷经验、第一轮模式）与派工契约（纠错要求、产出路径）。
    """
    gno = group["group"]
    label = group.get("label", f"第{gno}组")
    chapter_nos = group.get("chapters", [])
    phys_range = group.get("phys_range", [0, 0])
    chars = group.get("chars", 0)
    is_preface = group.get("is_preface", False)
    cross_names = cross_names or {"names": [], "compound_words": {}}
    common_sections = common_sections or []

    def common(key):
        for k, _h, body in common_sections:
            if k == key:
                return body
        return []

    book_title = meta.get("book") or "（book 缺）"
    book_type = meta.get("type") or "（type 缺）"

    # 章文件路径列表
    chapter_lines = []
    for no in chapter_nos:
        ch = chapters_meta.get(no, {})
        name = ch.get("name", f"第{no}章")
        pattern = os.path.join(work_dir, "chapters", f"{no:02d}_*.txt")
        matches = glob.glob(pattern)
        rng = f"phys{ch.get('phys_start', '?')}-{ch.get('phys_end', '?')}"
        if matches:
            rel = os.path.relpath(matches[0], work_dir)
            chapter_lines.append(f"{no:02d}. `{rel}`（{name}，{rng}）")
        else:
            chapter_lines.append(f"{no:02d}. chapters/{no:02d}_{name}.txt（{name}，{rng}）")

    key_names, all_names = filter_proper_names_for_group(proper_names, chapter_nos, chapters_meta)

    # ── 分块累积（末了统一编号，避免"写死序号 + 条件省略"造成跳号）
    blocks = []
    blocks.append(("需读取文件（页间有锚标记，引用必用锚，勿自行编造页码）",
                   ["锚格式：`<<<PAGE phys=N book=M>>>`（N=物理页序，M=原书页码）。",
                    "",
                    "**笔记页码写法（硬约束）**——02 速查卡按此解析，写错即定位列空白或错值：",
                    "- 唯一写法 `（原书页码 M）`，全角括号，落在条目/引文的**句末**；`>` 引用行同此规则。",
                    "- **一条只写一个页码**：引文跨两页时写起始页，禁 `（原书页码 14、29）` 这类多值枚举。",
                    "- **禁纯数字** `（328）`，也禁 `p.328`／`第328页`。",
                    "- 前辅文无印页（页锚的 book 为负值）写 `（phys N）`，N 取页锚的 phys 值——不得折算成印页。",
                    "- 区间仅在确需标整段跨度时用 `（原书页码 12–15）`（en dash 无空格）。",
                    ""] + chapter_lines))

    if key_names:
        blocks.append(("本章重点专名（章名中出现，须重点核对）", ["、".join(key_names)]))

    if all_names:
        rows = []
        for i in range(0, len(all_names), 5):
            rows.append("、".join(all_names[i:i + 5]))
        blocks.append(("全书专名清单（纠错时须核对，OCR 形近误读高发区）", rows))

    if cross_names.get("names") or cross_names.get("compound_words"):
        cross_lines = ["以下为**跨卷专名标准**（来源：已完成卷实战确认，**提示性参考，不强制**，"
                       "须结合本书上下文判断；有歧义须语境核实）："]
        if cross_names.get("names"):
            cross_lines += ["", "**人名/地名**："]
            for item in sorted(cross_names["names"], key=lambda x: x.get("name", "")):
                aliases = "、".join(f"「{a}」" for a in item.get("wrong_forms", []))
                note = item.get("note", "")
                cross_lines.append(f"- 标准「{item['name']}」：OCR 常见误形 {aliases}。{note}")
        if cross_names.get("compound_words"):
            cross_lines += ["", "**高频词语**："]
            for std, info in sorted(cross_names["compound_words"].items()):
                aliases = "、".join(f"「{a}」" for a in info.get("aliases", []))
                note = info.get("note", "")
                cross_lines.append(f"- 标准「{std}」：OCR 常见误形 {aliases}。{note}")
        blocks.append(("跨卷专名标准（提示性）", cross_lines))

    if common("candidates"):
        blocks.append(("高频通改候选（请确认是否升级为 systematic 全局通改）", common("candidates")))

    if prev_exp:
        pl = ["以下为前卷高频 OCR 错形（**提示性参考，不强制**，须结合本卷上下文判断）："]
        for i, e in enumerate(prev_exp[:15], 1):
            pl.append(f"  {i}. 「{e['old']}」→「{e['new']}」（前卷出现 {e['count']} 次）")
        blocks.append(("前卷经验（提示性）", pl))

    if round1_patterns:
        pl = ["以下为本书第一轮精读已发现的高频错形（**第二轮软提示，不强制**，"
              "请重点核对同类错形是否还有遗漏）：", ""]
        for i, (old, new, cnt) in enumerate(round1_patterns[:12], 1):
            pl.append(f"  {i}. 「{old}」→「{new}」（第一轮出现 {cnt} 次）")
        blocks.append(("第一轮漏校模式（第二轮软提示）", pl))

    if common("simplify"):
        blocks.append(("简繁码位抖动结论", common("simplify")))

    if common("patterns"):
        blocks.append(("常见漏校模式提示（来自多卷精读实战·软提示·不强制）", common("patterns")))

    blocks.append(("纠错要求（核心）", [
        "文本来自 OCR，可能个别错字，**请结合上下文与领域常识主动纠错**，但：",
        "",
        "- 每个非平凡纠错都写进 `corrections/` 台账，**先按 Stage 3「纠错判层与留痕落点」判层**：",
        "  - 属转录层（错在我方 OCR）的条目加 `\"ocr\": true`；",
        "  - 属底本层的记明原书原形与判据；",
        "  - **03 正文一律不留记号**（〔〕留痕不写入正文）；",
        "  - 拿不准的按存疑登记、不臆改；",
        "- 高频系统噪声在返回开头列「通改清单」，正文直接写修正后字；",
        "- 绝不无声改字、绝不臆改年代数字（疑则标不猜）；",
        "- 古籍集注本务必保留夹注（〔夹注〕标记），不可当噪声删。",
    ]))

    blocks.append(("产出路径", [
        f"- **纠错补丁**：`corrections/{gno:02d}_corrections.json`（Write 工具，JSON 数组）",
        f"- **分析笔记**：`notes/{gno:02d}.md`（Write 工具，全带原书页码锚点）",
        "- **阅读进度**：`reading_progress.md`（或 `.json`，记录行范围 + 已读行数 + 发现错误数）",
        "",
        "要求：忠实原文，数字/人名/年代/术语不臆造；每条笔记带原书页码锚点。",
    ]))

    lines = [f"# 派工 Brief · 第{gno:02d}组（{label}）", "",
             f"- **书名**：{book_title}",
             f"- **本书类型**：{book_type}",
             f"- **本组范围**：phys {phys_range[0]}-{phys_range[1]}，约 {chars} 字符",
             f"- **前辅文组**：{'是' if is_preface else '否'}",
             ""]
    for i, (title, body) in enumerate(blocks):
        num = CN_NUM[i] if i < len(CN_NUM) else str(i + 1)
        lines.append(f"## {num}、{title}")
        lines.append("")
        lines.extend(body)
        lines.append("")

    lines.append("---")
    lines.append("*本 brief 由 gen_dispatch_brief.py 自动生成（v2.3.0），"
                 "基于 group_plan.json + book_meta.json；公共段落与 gen_brief 同源。*")
    return "\n".join(lines)


def render_index(book_title, groups, chapters_meta):
    """渲染 briefs/_index.md 概览。"""
    lines = [f"# {book_title} · 派工分组概览", "",
             "| 组 | 标签 | 章号 | phys范围 | 字符数 | 前辅文 |",
             "|---|---|---|---|---|---|"]
    for g in groups:
        chs = ",".join(str(c) for c in g.get("chapters", []))
        pr = g.get("phys_range", [0, 0])
        lines.append(f"| {g['group']:02d} | {g.get('label', '')} | {chs} | {pr[0]}-{pr[1]} | "
                     f"{g.get('chars', 0)} | {'是' if g.get('is_preface') else '否'} |")
    lines += ["", "各组详细 brief 见同目录 group_NN.md。"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="派工 brief 自动生成（P1-A1）")
    ap.add_argument("--work", required=True, help="work 目录")
    ap.add_argument("--meta", default=None, help="book_meta.json 路径（默认 work/book_meta.json）")
    ap.add_argument("--groups", default=None, help="group_plan.json 路径（默认 work/group_plan.json）")
    ap.add_argument("--prev-experience", default=None, help="上一卷 work 目录（提取高频错形经验）")
    ap.add_argument("--simplify-leak", default=None, help="simplify_leak_report.md 路径")
    ap.add_argument("--proper-names", default="off", choices=["off", "all", "topic"],
                    help="跨卷专名注入策略：off=不注入（缺省）/ all=全部已确认 / topic=按 --topic 过滤")
    ap.add_argument("--topic", default="", help="配 --proper-names topic 用（如「甲午战争」）")
    args = ap.parse_args()

    work = args.work
    if not os.path.isdir(work):
        print(f"❌ work 目录不存在：{work}", file=sys.stderr)
        sys.exit(2)

    meta_path = args.meta or os.path.join(work, "book_meta.json")
    if not os.path.isfile(meta_path):
        print(f"❌ book_meta.json 不存在：{meta_path}", file=sys.stderr)
        sys.exit(2)
    meta = read_json_cn(meta_path)
    derive_phys_ends(meta)   # phys_end 可省略，由相邻篇推导
    check_meta_keys(meta)    # 缺必需键 / 残留同义旧键 → stderr 告警

    plan = load_group_plan(work, args.groups)
    if plan is None:
        print("❌ group_plan.json 不存在，请先跑 gen_group_plan.py（A2）", file=sys.stderr)
        sys.exit(2)

    chapters_meta = load_chapters_meta(work)
    fill_phys_end_from_meta(chapters_meta, meta)

    proper_names = meta.get("proper_names", [])
    book_title = meta.get("book") or plan.get("book") or "（书名缺）"
    book_type = meta.get("type") or plan.get("type") or "（类型缺）"

    prev_exp = extract_prev_experience(args.prev_experience) if args.prev_experience else []
    simplify_leak = load_simplify_leak(work, args.simplify_leak)
    cross_names = select_cross_book_names(args.proper_names, args.topic)

    # 公共段落只渲染一次（各组合用），避免逐组重算与口径漂移
    common_sections = brief_sections(meta, proper_names=[], simplify_leak=simplify_leak, work=work)

    out_dir = os.path.join(work, "briefs")
    os.makedirs(out_dir, exist_ok=True)

    groups = plan.get("groups", [])
    print(f"书名：{book_title}")
    print(f"类型：{book_type}")
    print(f"分组数：{len(groups)}")
    print(f"专名数：{len(proper_names)}")
    if prev_exp:
        print(f"前卷经验：{len(prev_exp)} 条高频错形")
    if cross_names["names"] or cross_names["compound_words"]:
        print(f"跨卷专名标准：{len(cross_names['names'])} 人名/地名 + "
              f"{len(cross_names['compound_words'])} 高频词语（策略 {args.proper_names}）")
    else:
        print("跨卷专名标准：未注入（--proper-names off，缺省；需要时显式开启）")
    round1_patterns = extract_round1_patterns(work) if os.path.isdir(os.path.join(work, "corrections")) else []
    if round1_patterns:
        print(f"第一轮漏校模式：{len(round1_patterns)} 条（第二轮软提示）")
    print()

    for g in groups:
        gno = g["group"]
        brief = render_brief(meta, g, chapters_meta, proper_names, prev_exp,
                             simplify_leak, work, cross_names, round1_patterns,
                             common_sections=common_sections)
        out_path = os.path.join(out_dir, f"group_{gno:02d}.md")
        safe_write(out_path, brief)
        label = g.get("label", "")
        chars = g.get("chars", 0)
        print(f"  ✅ group_{gno:02d}.md（{label}，{chars} 字符，{len(g.get('chapters', []))} 章）")

    index = render_index(book_title, groups, chapters_meta)
    safe_write(os.path.join(out_dir, "_index.md"), index)
    print(f"\n✅ 共生成 {len(groups)} 份 brief + _index.md，输出目录：{out_dir}")


if __name__ == "__main__":
    main()
