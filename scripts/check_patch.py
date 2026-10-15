#!/usr/bin/env python3
# check_patch.py —— Stage 3 前置校验：corrections/*.json 的格式 / 冲突 / 冗余 / 非法 old 片段
# 用途：在 pipeline_assemble.py 之前跑，把「脏补丁」拦在组装之前，避免错误静默混入 03。
# 用法：python check_patch.py <WORK目录>
#   退出码：0 = 通过（可 assemble）；1 = 存在 error（**须**修复再 assemble——assemble 侧非零即拒交付）。
#   注意：corrections/ 无 *.json 时打印"跳过校验"并 return 0（不阻断）。
#
# 三类输出：
#   [ERROR]   确定性缺陷（old 为空 → 无法定位唯一片段，等价于全书级误替换）—— 应阻断。
#   [WARN ]   启发式提示（old 含描述性文字、old 在 struct 容错未命中、删除型/系统噪声格式疑点）—— 人工复核。
#   [INFO ]   单字 old 易误替换（罕见字基本安全）—— 已降级，默认不刷屏，加 --verbose 可见。
#   [CONFLICT] 冲突：同一 old 出现多个不同 new（systematic 通改）—— 需仲裁，assemble 已标存疑。
# 默认只打印「类别摘要」而非逐条明细，避免刷屏；加 --verbose 看全部 WARN/INFO。

import re, json, glob, os, sys, argparse
from _common import _norm_fast, read_text_cn, read_json_cn, compile_linebreak, iter_spans, find_span, DESC_RE, expand_patch_data, _page_blocks  # 与 assemble 共用；#24 读取单一入口；expand_patch_data=2026-10-05 漂移收敛；_page_blocks=P2-B3 phys/book区分告警

# 描述性短语特征：Agent 把「解释说明」误写进 old 字段时常见的关键词。
# 只做启发式提示（WARN），不作确定性阻断——古文正文本身也可能含这些字（如校勘术语「一作」）。
# DESC_RE 已收敛到 _common（2026-10-04 审查 P2-14），本文件不再内联。


def _warn(warn_cat, warnings, cat, msg):
    """记录一条 WARN 并按类别计数（默认报告只打类别摘要，不刷明细）。"""
    warnings.append(msg)
    warn_cat[cat] = warn_cat.get(cat, 0) + 1


def main():
    ap = argparse.ArgumentParser(description="校验 corrections/*.json 补丁质量")
    ap.add_argument("work", help="工作目录（含 corrections/*.json）")
    ap.add_argument("--verbose", action="store_true", help="打印每条 WARN/INFO 明细（默认只打印类别摘要，不刷屏）")
    args = ap.parse_args()
    B = args.work

    corr_files = sorted(glob.glob(os.path.join(B, "corrections", "*.json")))
    if not corr_files:
        print("无 corrections/*.json，跳过校验")
        return 0

    errors, warnings, conflicts, infos = [], [], [], []
    warn_cat = {}  # WARN 类别 -> 计数（用于默认只打类别摘要）
    sys_map = {}      # old -> set(new)（仅 systematic 通改）
    sys_regex = set() # systematic 正则型 old（用于 body0 构造时走 re.compile）
    silent_count = 0   # silent:true 补丁数（结构性静默删除）
    ocr_count = 0      # ocr:true 补丁数（转录层只进台账，§3.1，2026-10-01）
    per_phys_map = {} # (phys, old) -> set(new)（零散补丁冲突）
    # 🟡v1.10.6：元素为 (old, is_regex)；正则型 old 是正则原文，命中检查须走 re.search 而非字面包含
    all_olds = []     # 所有 old（用于 struct 命中检查）
    deleted_entries = []  # P2 新-5 收尾：status=deleted 的 tombstone，仅用于防御性 WARN
    struct = None
    struct_path = os.path.join(B, "fulltext_struct.txt")
    if os.path.exists(struct_path):
        struct = read_text_cn(struct_path)  # #24：统一 utf-8-sig 容错
    # 总物理页数（用于 phys 合法性校验）
    # 2026-10-05 P1（复审）：读取失败/缺失不得静默——total_phys 取不到会让
    # `pnum > total_phys` 恒不触发，phys 越界校验形同虚设，脏 phys 补丁静默漏到 assemble。
    total_phys = None
    meta_path = os.path.join(B, "book_meta.json")
    if not os.path.exists(meta_path):
        errors.append("book_meta.json: 不存在，total_phys 无法取得，phys 越界校验将失效")
    else:
        try:
            total_phys = read_json_cn(meta_path).get("total_phys")  # #24：统一 BOM 容错
        except Exception as e:
            errors.append(f"book_meta.json: total_phys 读取失败，phys 越界校验将失效：{e}")
        if total_phys is None:
            errors.append("book_meta.json: 缺 total_phys 字段，phys 越界校验将失效（补上后重跑）")

    for cf in corr_files:
        try:
            data = json.load(open(cf, encoding="utf-8-sig"))  # utf-8-sig：容忍 BOM（🟡8）
        except json.JSONDecodeError:
            try:
                data = [json.loads(l) for l in open(cf, encoding="utf-8-sig") if l.strip()]
            except Exception as e:
                errors.append(f"{os.path.basename(cf)}: JSON 解析失败（既非合法 JSON 也非合法 JSONL）：{e}")
                continue
        # {"patches":[...]}/单条 dict/顶层数组 三形态归一（2026-10-05：收敛到 _common.expand_patch_data 单一真相源）
        data = expand_patch_data(data)
        for idx, c in enumerate(data):
            if not isinstance(c, dict):
                continue
            if "old" not in c and "merge" not in c:
                continue
            tag = f"{os.path.basename(cf)}[{idx}]"
            if c.get("merge"):
                # 🟡19（2026-09-08 剩余批）：merge 补丁旧版跳过全部校验，脏 phys 直接漏到
                # assemble（旧实现会崩；现 _common 侧记 miss 丢弃）。预检就该拦住：
                try:
                    pnum = int(c.get("phys"))
                    if pnum < 1 or (total_phys and pnum > total_phys):
                        errors.append(f"{tag}: merge 补丁 phys={c.get('phys')} 超出全书范围（1–{total_phys or '?'}）")
                except (TypeError, ValueError):
                    errors.append(f"{tag}: merge 补丁 phys={c.get('phys')!r} 非数字/缺失（assemble 无法定位合并位置）")
                continue
            old = c.get("old", "")
            # P0 #4（2026-09-12）：缺 `new` 键 ≠ 删除型（new 为空串）。原用 c.get("new","")
            # 把两者混为一谈 → 缺键补丁只给 WARN 并放行，assemble 侧 `c["new"]` 随即
            # KeyError 裸崩（预检绿灯、运行即死）。缺键是脏数据，必须在预检拦下。
            if "new" not in c:
                errors.append(f"{tag}: 缺 new 字段。删除型补丁须显式写 \"new\": \"\"；"
                              f"空着会让 assemble 读取 c[\"new\"] 时 KeyError 崩溃。")
                continue
            new = c.get("new", "")
            phys = c.get("phys")
            systematic = c.get("systematic") or str(phys).upper() == "ALL"
            is_regex = bool(c.get("regex"))
            if c.get("status") == "deleted":
                # 🟡P2 新-5 收尾：tombstone 已声明不施加，跳过全部校验，避免对"不施加补丁"误报 ERROR；
                # 仅留防御性 WARN（若错误形仍残留在正文则告警，见 step ⑧ 后）。
                deleted_entries.append((old, is_regex, new, tag, systematic))
                continue

            # ① old 为空：确定性缺陷，仍阻断。
            #    🟡17（2026-09-08 剩余批）：旧文案「会导致 find_span 死循环」有误——实测
            #    find_span(hay, '') 立即返回 None（有 if not needle 保护），不死循环。
            #    阻断的真正理由：空 old 无法定位唯一片段，按"处处命中"理解即全书级误替换，
            #    且语义不明属脏数据，必须在预检拦下。
            if not old:
                errors.append(f"{tag}: old 为空字符串（无法定位唯一片段，等价于全书级误替换，脏数据须修正）")
                continue
            # 🟡v1.10.6：正则型（"regex": true）的 old 是正则原文，不做字面长度/描述性检查
            if is_regex:
                try:
                    re.compile(old)
                except re.error as e:
                    errors.append(f"{tag}: 正则型 old「{old}」无法编译（{e}），脏数据须修正")
                    continue
                sys_regex.add(old)
            all_olds.append((old, is_regex, new, tag, systematic))

            # ② old 单字：易全局误替换（降级为 INFO：罕见字几乎不会误替，仅提示，默认不刷屏）
            #    🟡v1.10.6：仅字面型有意义——正则型的安全性由正则自身的上下文约束决定，
            #    看 old 长度判断会误报（如 `(?<=[〇○一二三])○` 短但安全，纯 `○` 才是真危险）。
            if not is_regex and len(old) < 2:
                infos.append(f"{tag}: old「{old}」仅 1 字，易误替换，建议补足上下文")

            # ③ old 含描述性文字：无法在原文命中（正则型同理跳过：元字符会被误判为描述性）
            if not is_regex and DESC_RE.search(old):
                _warn(warn_cat, warnings, "描述性文字", f"{tag}: old「{old}」疑似描述性文字（非原文连续片段），可能无法命中")

            # ④ new 为空 = 删除型补丁（OCR 衍字删除，new 留空），属合法格式；仅提示确认 old 确为多余字
            if not new:
                _warn(warn_cat, warnings, "删除型补丁", f"{tag}: new 为空 = 删除型补丁（OCR衍字删除）；请确认 old「{old}」在原文确有且非正文必需字（assemble 会就地标〔原书OCR衍\"{old}\"，删〕）")

            # ⑤ 逻辑矛盾：标了 systematic 但 phys 是具体页码（系统噪声通改应 phys=ALL）
            if c.get("systematic") and phys not in ("ALL", "all", "All", None):
                _warn(warn_cat, warnings, "systematic冲突", f"{tag}: systematic:true 但 phys={phys}（非 ALL），系统噪声通改应 phys=ALL")

            # ⑤b silent 静默补丁校验：仅限结构性对象（错乱表格HTML、图谱碎片小图、表/图文字层碎片）
            if c.get("silent"):
                silent_count += 1
                if not new:
                    infos.append(f"{tag}: silent:true + new为空 = 结构性静默删除（错乱表格/图谱碎片/表图文字层）；assemble 不留痕、不计入 C1，须有原图或 region_noise_log 可核")
                else:
                    _warn(warn_cat, warnings, "silent非空new", f"{tag}: silent:true 但 new 非空（{new[:30]}…）；silent 仅限结构性删除/替换，普通文字纠错严禁 silent")
                if "<table" not in old and "<img" not in old and "</div>" not in old and len(old) < 20:
                    _warn(warn_cat, warnings, "silent疑似非结构性", f"{tag}: silent:true 但 old 不含 table/img/div 且 <20 字，疑似普通文字纠错误用 silent")
            elif c.get("ocr"):
                # ⑤c 转录层台账型（§3.1，2026-10-01）：错在我方 OCR，施加但不留正文记号
                ocr_count += 1

            # ⑥ phys 合法性校验（零散补丁）
            if not systematic:
                try:
                    pnum = int(phys)
                    if pnum < 1 or (total_phys and pnum > total_phys):
                        errors.append(f"{tag}: phys={phys} 超出全书范围（1–{total_phys or '?'}）")
                except (TypeError, ValueError):
                    errors.append(f"{tag}: phys={phys} 非数字（零散补丁 phys 须为具体页码）")

            # ⑦ 冲突检测
            if systematic:
                sys_map.setdefault(old, set()).add(new)
            else:
                try:
                    per_phys_map.setdefault((int(phys), old), set()).add(new)
                except (TypeError, ValueError):
                    pass

    # 冲突：同一 old 多个不同 new（systematic）
    for old, news in sys_map.items():
        if len(news) > 1:
            conflicts.append((old, sorted(news)))
    # 冲突：同一 phys 同一 old 多个不同 new（零散补丁）
    for (pnum, old), news in per_phys_map.items():
        if len(news) > 1:
            conflicts.append((f"phys{pnum}「{old}」", sorted(news)))

    # ⑧ grounding（P2 新-2：基准对齐到「已通改 body0」，不再验未通改 struct）
    #    - systematic 条目：验 raw struct（未通改）——0 命中=本书无效通改，WARN 建议清理（不阻断）。
    #    - per-phys 条目：验 body0（已通改，systematic 先施加）——0 命中分两类：
    #        · new 已在 body0 → 冗余（已被 systematic 代劳），WARN 建议 status:deleted（不阻断）；
    #        · new 也不在 → 凭空制造/phys错/over-specific，ERROR 阻断（gen-time 接地硬闸）。
    #    R6 性能修复（2026-09-08）：全书归一化只做一次。
    if struct is not None:
        struct_norm = _norm_fast(struct)  # 全书归一化只做一次（R6）
        body0 = struct
        body0_norm = struct_norm
        if sys_map:  # 有 systematic 才构 body0（避免无谓正则开销）
            clean_sys = [(o, sorted(n)[0], "") for o, n in sys_map.items() if len(n) == 1]
            sys_rules = []
            for o, new, _ in clean_sys:
                is_rx = o in sys_regex
                rx = re.compile(o) if is_rx else compile_linebreak(o)
                sys_rules.append((o, new, rx, is_rx))
            for o, new, rx, is_rx in sys_rules:
                body0 = rx.sub((lambda m, n=new, ir=is_rx: m.expand(n)) if is_rx else new, body0)
            body0_norm = _norm_fast(body0)
        # 性能优化：_page_blocks只调用一次（循环内重复调用会每次遍历全文分割14842页）
        _blocks_cache = _page_blocks(body0)
        for old, is_regex, new, tag, systematic in all_olds:
            if is_regex:
                # 正则型：按正则在 raw struct 上粗筛（与 assemble 施加口径略有差异，仅判形态是否出现）
                try:
                    if not re.search(old, struct_norm):
                        _warn(warn_cat, warnings, "容错未命中", f"正则 old「{old}」在 fulltext_struct 未命中（补丁可能已失效）")
                except re.error:
                    pass
                continue
            nn = _norm_fast(old)
            if systematic:
                # systematic 验 raw struct（compile_linebreak 容忍换行，与 apply_corrections 同口径）
                if old and not compile_linebreak(old).search(struct):
                    _warn(warn_cat, warnings, "通改本书0命中",
                          f"{tag}: systematic「{old}」在本书 fulltext_struct 0 命中（无效通改，建议置 deleted / 移出 manifest）")
                continue
            # per-phys：验 body0（已通改），用 iter_spans / find_span（与 apply_corrections 同口径，容忍换行）
            spans = iter_spans(body0, old) if old else []
            if old and not spans:
                if new and find_span(body0, new):
                    _warn(warn_cat, warnings, "冗余补丁",
                          f"{tag}: per-phys「{old}」在已通改正文 0 命中但 new「{new}」已存在（已被 systematic 代劳，冗余，建议 status:deleted）")
                else:
                    # P2-B3（v1.27.0）：0命中时检测 phys 是否误用了 book 页码（印页）
                    # book页码通常比phys小一个offset（如offset=7时 book=phys-7），
                    # 若在 phys+5..phys+15 范围内的某页能找到 old，则告警。
                    book_misuse_hint = ""
                    try:
                        pnum = int(c.get("phys", 0))
                        if pnum > 0:
                            blocks = _blocks_cache
                            for offset in range(5, 16):
                                check_phys = pnum + offset
                                if check_phys in blocks and find_span(blocks[check_phys], old):
                                    book_misuse_hint = f"（疑似 phys 误用了 book 印页：old 实际在 phys{check_phys}，偏移+{offset}，请核对 phys 应为物理页序非印页）"
                                    break
                    except (ValueError, TypeError):
                        pass
                    # v1.16.2 修复精神 + P2 新-2：new 也不在=补丁凭空制造，确定性脏数据，ERROR 阻断
                    errors.append(f"{tag}: per-phys「{old}」在已通改正文 0 命中（凭空制造/phys错/over-specific，须核实或修正 old；新基准下仍查不到即补丁无效）{book_misuse_hint}")
            elif old and len(spans) > 1:
                # P1-B1（v1.27.0）：per-phys 补丁 >1 命中 = old 太短/上下文不足，偏页风险
                _warn(warn_cat, warnings, "多命中偏页风险",
                      f"{tag}: per-phys「{old}」在已通改正文命中 {len(spans)} 处（>1，old 太短或上下文不足，可能偏页；建议 old 带≥6字上下文使全书唯一命中）")

        # 🟡P2 新-5 收尾：防御性 WARN——deleted tombstone 若错误形仍残留在 body0（new 也未出现），
        # 说明疑似误删 / phys 错位，正文仍带错字。仅告警不阻断，作为"已删但 03 仍错"的安全网。
        for old, is_regex, new, tag, systematic in deleted_entries:
            if is_regex:
                continue
            if old and iter_spans(body0, old) and (not new or not find_span(body0, new)):
                _warn(warn_cat, warnings, "deleted仍残留",
                      f"{tag}: 已 deleted 但错误形「{old}」仍残留在正文（new「{new}」未出现），疑似误删或 phys 错位，请确认 03 是否已正确")

    # 报告（默认只打类别摘要，不刷 43 行明细；加 --verbose 看每一条）
    sep = "=" * 64
    print(sep)
    print(f"check_patch：{len(corr_files)} 份补丁 → error={len(errors)} / warn={len(warnings)} / conflict={len(conflicts)} / info={len(infos)} / silent={silent_count} / ocr台账={ocr_count}")
    print(sep)
    if errors:
        for e in errors:
            print(f"[ERROR  ] {e}")
    if args.verbose:
        for w in warnings:
            print(f"[WARN   ] {w}")
        for i in infos:
            print(f"[INFO   ] {i}")
    else:
        if warnings:
            cat_str = "，".join(f"{k} {v}" for k, v in sorted(warn_cat.items()))
            print(f"[WARN   ] 共 {len(warnings)} 条（{cat_str}）；加 --verbose 看明细")
        if infos:
            print(f"[INFO   ] 共 {len(infos)} 条单字易误替换类提示（罕见字基本安全，可忽略）；加 --verbose 看明细")
    for old, news in conflicts:
        print(f"[CONFLICT] 「{old}」→ " + " / ".join(f"「{n}」" for n in news))
    print(sep)
    if errors:
        print("存在 error，建议先修复再 assemble。")
    elif conflicts:
        print("无 error，但有冲突：assemble 会对冲突项原地标〔存疑〕，请手动仲裁。")
    else:
        print("通过（可 assemble）。")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
