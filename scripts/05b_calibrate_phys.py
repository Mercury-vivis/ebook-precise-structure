#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""05b_calibrate_phys.py —— 逐页补丁 phys 自动校准（Stage 3 收尾 / Stage 4 前置）

背景与口径：派工中 Agent 写的补丁 `phys` 常**偏页**——它把「读到该错字时翻到的页」记成了该字
所在页，于是 assemble 在声明页找不到锚，只能走「未命中 → 全局回退」；成批回退本身就是信号。
本脚本按**与 assemble 完全相同的定位口径**（`_common.find_span` 三级容错：精确 → 容忍换行 →
去空白/全角折半）把偏页补丁的 `phys` 归位到 `old` 实际所在的页块。

用法：
  python 05b_calibrate_phys.py --work <工作目录>            # dry-run（默认，只报告，不写盘）
  python 05b_calibrate_phys.py --work <工作目录> --apply    # 就地改写 corrections/*.json

**判据（保守归位，宁缺勿滥）**——对每条逐页补丁（有 old、phys 为数字、非 merge、非 systematic）：

  noop       `old` 与 `new` 去空白后逐字相同（如 `{"old":"曾国藩","new":"曾国藩","reason":"无错"}`）
             → **空操作补丁**（既非错字也无修正），不改动。这类条目在存量补丁里成批存在。
  syscover   `old` 已是某条 systematic 通改的错形 → 通改先改掉它，逐页补丁必然落空（由 assemble
             的「代劳复核」兜底，不计未命中）→ 不改动。
  ok         声明页即命中 → 无需改动。
  moved      **声明页未命中、且全书只在一页命中** → 归位到该页（唯一解，零歧义）。这是本脚本的
             唯一落盘动作。
  multihit   声明页未命中，但全书**多页**命中 → **不改动**、列全部命中页待人工判断。
             ⚠️ 为什么不用「取全书首次命中页」：高频短串（人名、常用词、年代数字）在全书到处
             出现，取"首次"会把 phys 指到毫不相干的页——实测《第17卷上》「曾国藩」210→8、
             「石达开」270→5。偏页的真实形态是"邻近几页"，不是"全书第一页"。
  unresolved 全书未命中（跨页断裂 / 抄录有误 / 描述性文字）→ 不改动，单列报告。

**配套硬规则（派工侧，见 SKILL.md §5.1 phys 口径）**：`old` 必须带足上下文（建议 ≥6 字，含错字前后
各 2–3 字）使其在全书**唯一命中**——唯一命中时「首次命中页」与「所在页」等价，phys 由 old 唯一
确定，本脚本即可全自动归位。短 old 是偏页与多命中的共同根源。

其它：只改 `phys` 数值，不动 old/new/reason、键序与其它字节（按行就地替换；非「一行一对象」
格式的文件跳过该行）；幂等（校准后重跑应 0 变更）；写盘统一 LF（§6.0）。

退出码：0 正常；1 = 存在 unresolved 条目（需人工处理，但**不改动**）；2 = 所有补丁解析到 0 条有效补丁（格式不支持/文件为空，非通过）。
"""
import argparse
import glob
import json
import os
import re
import sys

from _common import find_span, read_text_cn, same_text, safe_write, collect_systematic_olds, expand_patch_data  # 与 assemble 同一匹配语义；expand_patch_data=2026-10-05 漂移收敛

ANCHOR_ITER = re.compile(r"<<<(?:PAGE|SEC)\s+phys=(\d+)[^>]*>>>\n(.*?)(?=\n<<<|\Z)", re.S)


def parse_blocks(struct_text):
    """struct → [(phys, body), ...]（页块顺序即原文顺序）。"""
    return [(int(m.group(1)), m.group(2)) for m in ANCHOR_ITER.finditer(struct_text)]


def locate(blocks, old):
    """old 在页块序列中的全部命中页（按原文顺序）。"""
    return [phys for phys, body in blocks if find_span(body, old)]


def load_patches(path):
    """读取一个 corrections 文件，返回 (data, entries)。

    data 为顶层对象（list / {"patches":[...]} / 单条 dict）；entries 为要逐条校验的补丁
    列表（对 data 的引用，修改即生效，落盘时 json.dump(data) 即可保留原结构）。
    解析失败返回 (None, None)。v1.16.2 修复：旧版逐行正则 OBJ_LINE 只认"一行一 JSON 对象"
    的 JSONL，而实际 corrections 文件全是缩进展开的标准 JSON 数组（{ 单独成行），导致
    三本书实测解析 0 条却 exit 0（环2 假通过）。现用 json.load 读整篇，并退化支持 JSONL。
    """
    raw = read_text_cn(path, encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            data = [json.loads(ln) for ln in raw.splitlines() if ln.strip()]
        except json.JSONDecodeError:
            return None, None
    if isinstance(data, list):
        return data, data
    if isinstance(data, dict):
        return data, expand_patch_data(data)  # 2026-10-05：收敛到 _common 单一真相源
    return None, None


def detect_cross_page(blocks, old):
    """P2-B2（v1.27.0）：检测old是否跨页断裂，返回(前半命中页, 建议缩短子串)或None。

    跨页断裂两种形态：
    1. old 本身含 \\n（硬换行切断）
    2. old 的前半在某页末尾、后半在下一页开头（无显式\\n但页界切断）
    建议：取old前半部分（单页内子串）作为新old，使全书唯一命中。
    """
    if not old or len(old) < 4:
        return None
    # 形态1：old含换行
    if "\n" in old or "\r" in old:
        # 取换行前的部分作为建议
        short = re.split(r"[\n\r]", old)[0].strip()
        if len(short) >= 4:
            # 找short在哪个页
            for i, (phys, body) in enumerate(blocks):
                if find_span(body, short):
                    return (phys, short)
        return None
    # 形态2：old前后半跨页（从中间切开尝试）
    mid = len(old) // 2
    for split in range(mid, len(old) - 2):
        front = old[:split]
        back = old[split:]
        if len(front) < 4 or len(back) < 4:
            continue
        # 找front命中页
        for i, (phys, body) in enumerate(blocks):
            if find_span(body, front):
                # 检查back是否在下一页开头
                if i + 1 < len(blocks):
                    next_body = blocks[i + 1][1]
                    if find_span(next_body[:len(back) + 10], back):
                        return (phys, front)
                break
    return None


def classify(c, blocks, sys_olds):
    """判定单条补丁 → (类别, 归位页 or None, 命中页列表)。类别见模块 docstring。"""
    old, new = c["old"], c.get("new", "")
    try:
        old_phys = int(c["phys"])
    except (TypeError, ValueError):
        return "skip", None, []
    if same_text(old, new):
        return "noop", None, []
    if old in sys_olds:
        return "syscover", None, []
    hits = locate(blocks, old)
    if not hits:
        # P2-B2：unresolved 时检测跨页断裂，给出缩短建议
        cross = detect_cross_page(blocks, old)
        if cross:
            return "cross_page", cross[0], [cross[1]]  # 归位页=前半所在页，命中页列表=[建议缩短子串]
        return "unresolved", None, []
    if old_phys in hits:
        return "ok", None, []
    if len(hits) == 1:
        return "moved", hits[0], hits
    return "multihit", None, hits


def process_file(path, blocks, sys_olds, apply_changes):
    """处理单个 corrections 文件，返回 (stats, detail_lines, touched) 或 None（解析失败）。"""
    data, entries = load_patches(path)
    if data is None or not entries:
        return None
    stats = {k: 0 for k in ("noop", "syscover", "ok", "moved", "multihit", "unresolved", "cross_page", "skip")}
    detail = []
    touched = False
    for c in entries:
        if not isinstance(c, dict) or "old" not in c or c.get("merge"):
            continue
        # systematic 通改（phys=ALL 或 systematic:true）：非逐页补丁，不校准（与 assemble 一致）。
        if str(c.get("phys", "")).upper() == "ALL" or c.get("systematic"):
            stats["syscover"] += 1
            continue
        kind, target, hits = classify(c, blocks, sys_olds)
        stats[kind] += 1
        if kind == "moved":
            old_phys = int(c["phys"])
            detail.append(f"  ✅ phys {old_phys} → {target}   「{c['old'][:28]}」")
            c["phys"] = target  # 就地改值，落盘时随 data 一起写出
            touched = True
        elif kind == "multihit":
            detail.append(f"  ⚠️ 多页命中，未改动（phys {c['phys']}）：「{c['old'][:28]}」→ {hits}")
        elif kind == "cross_page":
            # P2-B2：跨页断裂，建议缩短old到单页内子串（hits[0]为建议子串，target为前半所在页）
            short = hits[0] if hits else ""
            detail.append(f"  💡 跨页断裂（phys {c['phys']}）：「{c['old'][:40]}」→ 建议缩短为「{short}」并归位phys{target}（old跨页边界，须缩短至单页内子串才能命中）")
        elif kind == "unresolved":
            detail.append(f"  ❌ 全书未命中（phys {c['phys']} 保持不动）：「{c['old'][:40]}」")
    if touched and apply_changes:
        out = json.dumps(data, ensure_ascii=False, indent=2)
        safe_write(path, out)  # P1-9（2026-10-04）：corrections 高敏落盘走原子写单一入口
    return stats, detail, touched


def main():
    ap = argparse.ArgumentParser(description="逐页补丁 phys 校准（唯一命中才归位，保守模式）")
    ap.add_argument("--work", required=True, help="工作目录（含 fulltext_struct.txt 与 corrections/）")
    ap.add_argument("--apply", action="store_true", help="落盘改写 corrections/*.json（默认 dry-run 只报告）")
    args = ap.parse_args()
    B = args.work
    struct_path = os.path.join(B, "fulltext_struct.txt")
    if not os.path.exists(struct_path):
        sys.exit(f"❌ 找不到 {struct_path}（本脚本需要 struct 做定位基准）")
    blocks = parse_blocks(read_text_cn(struct_path))
    if not blocks:
        sys.exit(f"❌ {struct_path} 中未解析到任何 <<<PAGE phys=N ...>>> 页块，终止。")
    corr_files = sorted(glob.glob(os.path.join(B, "corrections", "*.json")))
    if not corr_files:
        print("无 corrections/*.json，无需校准。")
        return 0
    sys_olds = collect_systematic_olds(B)  # P1-8（2026-10-04 审查）：复用 _common 单一真相源（与 assemble 错字清单复扫同口径）

    print("=" * 76)
    print(f"phys 校准（{'落盘 --apply' if args.apply else 'dry-run'}）｜页块 {len(blocks)} 个｜"
          f"补丁文件 {len(corr_files)} 份｜通改 old {len(sys_olds)} 条")
    print("=" * 76)
    total = {k: 0 for k in ("noop", "syscover", "ok", "moved", "multihit", "unresolved", "skip")}
    any_touched = False
    parsed_total = 0
    for cf in corr_files:
        res = process_file(cf, blocks, sys_olds, args.apply)
        if res is None:
            print(f"\n【{os.path.basename(cf)}】⚠️ JSON 解析失败（既非标准 JSON 也非 JSONL），跳过")
            total["skip"] += 1
            continue
        stats, detail, touched = res
        parsed_total += sum(stats.values())
        for k in total:
            total[k] += stats[k]
        any_touched = any_touched or touched
        if detail:
            print(f"\n【{os.path.basename(cf)}】")
            print("\n".join(detail))
    print("\n" + "=" * 76)
    print(f"汇总：归位 {total['moved']} ｜ 声明页已命中 {total['ok']} ｜ 多页命中(未动) {total['multihit']} "
          f"｜ 全书未命中 {total['unresolved']}")
    print(f"      空操作补丁(未动) {total['noop']} ｜ 已被通改覆盖(未动) {total['syscover']} ｜ 脏/解析失败 {total['skip']}")
    if args.apply:
        print("落盘：" + ("已改写" if any_touched else "无需改写（幂等，零变更）"))
    else:
        print("dry-run：未写盘。确认无误后加 --apply 落盘。")
    print("=" * 76)
    # v1.16.2 修复：解析到 0 条有效补丁必须报错而非 exit 0（环2 假通过）。
    if parsed_total == 0:
        print("❌ 所有补丁文件解析到 0 条有效补丁（格式不支持或文件为空），终止（非通过）。", file=sys.stderr)
        return 2
    return 1 if total["unresolved"] else 0


if __name__ == "__main__":
    sys.exit(main())
