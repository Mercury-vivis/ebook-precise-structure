#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_book.py —— 交付前唯一的阻断闸（§6.1）

只回答两个问题，都关于**书**，不关于台账格式：
  闸A 这本书读得通吗？      ——随机抽页，用独立于台账的混淆词典扫 03，列出「phys + 疑似片段」
  闸B 书和账对得上吗？      ——台账/04 §四 记的每处改动，03 里 old 已清零、new 已到位
另加交付完整性（五件齐全 / BOM·CRLF·占位符 / 03 图引用 ⊆ imgs·fig / 00–03 交付指纹）。

设计原则（2026-10-18 定）
  · **抽检清单不阻断**：闸A 是抽样，不是全量证明。清单交人核——确认是书的错就改 03 并在 04 §四
    留痕；确认是误报就记一笔放行。用抽样结果阻断交付，只会逼人多跑几遍直到抽样干净。
  · **只有两件事阻断**：① 交付件缺件/脏格式/图断链；② 闸B 正向残留（03 里还留着台账说要改的错字，
    即书里确实还有错）。其余全部是报告。
  · **指纹只报告不阻断**：00–03 与 04 §五 登记的 sha256 不符时报出，但不定罪——修订路径本来就
    允许「定点改 03 + 04 §四 留痕」，改完指纹自然变。要是把它做成阻断，等于把这条正路也判死。
  · **反向差异分三类报**（2026-10-19）：
      ① 已登记（消除误报）— 账里有，只是要放宽空白/全半角才复现；
      ② 页归属错位 — 账的 phys 写错，补丁实际落在别的页（按全局回退仍生效）；
      ③ 真未登记 — 账上谁都解释不了，才是要人动手的。
    旧版把①②混进③，维民实测 47 页噪声里只有 1 行是真的。
  · **失败不自动重算**：报差异清单交人裁决。已交付且人已校对的书，走修订路径定点改 03 + 回写 04，
    不重跑 assemble（重算会用好书覆盖坏书，或用坏账覆盖好书）。

用法
  python check_book.py --work <work> --out <交付目录> [--meta book_meta.json] [--prefix 前缀]
      [--no-delivery]   跳过交付完整性（assemble 中途只有 03/04 时用）
      [--sample N]      闸A 抽页下限（默认 10）
      [--audit PATH]    把三项结论写入文件（默认 <work>/gate_report.md）

退出码：0 = 通过（闸A 清单、指纹不符等报告项可能非空）；1 = 交付不全或闸B 正向残留；2 = 无法定位。
"""
import os
import re
import sys
import glob
import argparse
import hashlib

from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

from _common import (  # noqa: E402
    read_text_cn, read_json_cn, strip_correction_traces, compile_linebreak,
    strip_leaked_headers, _page_blocks, is_residual_full,
    load_active_patches, iter_spans, find_span,
    preprocess_audit_summary,  # v2.1.0：预处理闭环检查复用汇总口径（2026-10-20）
    _body_of,  # v2.2.0：残留实例上下文从正文区取（与 is_residual_full 同域）
    read_delivery03, sha256_delivery03,  # 03 分册模式（2026-10-27）：全文读取与指纹单一口径
)

DELIVERY_SUFFIX = {
    "00": "00_梳理总览与目录对照.md",
    "01": "01_章节详解.md",
    "02": "02_脉络与速查卡.md",
    "03": "03_带页码全文.md",
    "04": "04_改错留痕.md",
}

# 04 §四 人工修订登记行。推荐写法（delivery-specs §6.3）：
#   - phys=112 ｜ 原文「国民党车队」→ 改为「国民党军队」｜ 理由：OCR 军/车 形近
# 同时容忍 «phys 112 / 「X」→「Y」» 这类宽松写法。
# v2.3.1：开闭引号**配对**（「」一对、“”一对），不再共用一个字符类。旧版开闭都写
# `[」\"”]`，old/new 内容里只要出现弯引号 `”` 就被当成闭引号提前截断——实测
# 《中国的历史脉动》04 §四 4.1 的 phys=45 行「「己出而后为子”（同前)，」→…」，
# old/new 双双被截成「己出而后为子」，解析出假 old==new 修订，闸B 正向误判残留
# （阻断）；phys=5/28 两条 new 被截成「交错杂陈。」式短串，反向复现时把引文括注
# 整段删掉，假报 2 页「③ 真未登记」。
_REV_RE = re.compile(
    r"phys\s*=?\s*(\d+)[^\n]*?[「“](.+?)[」”][^\n]{0,8}?(?:→|->|改为)[^\n]{0,8}?[「“](.+?)[」”]")

_FP_RE = re.compile(r"(00|01|02|03)=sha256:([0-9a-f]{64})")

_PLACEHOLDER_PATS = [
    r"<!--\s*待补充\s*-->", r"TODO", r"FIXME", r"\bXXX\b",
    # v2.2.0（S3）：三个中文词形收窄为「需伴随脚手架语法」才命中。原为词形字面匹配，
    # 两类实证误伤：① notes/01/02 描述 OCR 未识别字符的分析行文（「OCR 占位符」，
    # 正当使用）被误报阻断；② 04 §四 人工处置登记为解释误报而引用词形本身，两轮被
    # 擒（自指陷阱）。真正的脚手架残留必带【】〔〕或 HTML 注释语法；分析行文不再命中。
    # TODO/FIXME/XXX 在行文中极少正当使用，保持字面。
    r"[【〔]\s*占位符\s*[】〕]", r"<!--\s*占位符",
    r"[【〔]\s*占位标记\s*[】〕]", r"<!--\s*占位标记",
    r"[【〔]\s*占位待补\s*[】〕]", r"<!--\s*占位待补",
    # v2.3.1：「待填」同案收窄。实证：《中国的历史脉动》03 正文「这种瓦解带来的空白
    # 亟待填充」含子串「待填」，裸词模式报「占位符残留：待填(1处)」阻断交付——全书
    # 唯一命中即此正当行文。与上述三词形同理：真残留必带【】〔〕或 HTML 注释语法。
    r"[【〔]\s*待填\s*[】〕]", r"<!--\s*待填",
]


# ============================ 定位 ============================

def find_prefix(out_dir, prefix=""):
    if prefix:
        return prefix
    sfx = DELIVERY_SUFFIX["00"]
    hits = glob.glob(os.path.join(out_dir, "*" + sfx))
    if hits:
        base = os.path.basename(hits[0])
        return base[: -len(sfx)]
    # 退回用 03 推前缀
    hits = glob.glob(os.path.join(out_dir, "*" + DELIVERY_SUFFIX["03"]))
    if hits:
        base = os.path.basename(hits[0])
        return base[: -len(DELIVERY_SUFFIX["03"])]
    return ""


def find_03(out_dir, prefix=""):
    cands = sorted(glob.glob(os.path.join(out_dir, "*03_*.md")))
    if prefix:
        cands = [c for c in cands if os.path.basename(c).startswith(prefix)] or cands
    return cands[0] if cands else ""


# ============================ 闸A 可读性抽检 ============================

def load_chapters(meta_path):
    """从 book_meta.json 取章节 phys 范围（用于「每章至少 1 页」）。"""
    if not meta_path or not os.path.isfile(meta_path):
        return []
    try:
        meta = read_json_cn(meta_path)
    except Exception:
        return []
    chs = []
    for c in (meta.get("chapters") or []):
        a, b = c.get("phys_start"), c.get("phys_end")
        if isinstance(a, int) and isinstance(b, int) and b >= a:
            chs.append({"no": c.get("no"), "name": c.get("name", ""), "a": a, "b": b})
    return sorted(chs, key=lambda x: x["a"])


def pick_pages(blocks, chapters, min_total=10):
    """抽页：每章至少 1 页（取章中部），总量不足 min_total 时从全书均匀补足。

    返回 [(phys, 说明)]。说明用于在报告里交代「这一页为什么被抽到」。
    """
    phys_all = sorted(blocks)
    if not phys_all:
        return []
    picked, why = [], {}

    def _take(p, reason):
        if p in blocks and p not in why:
            why[p] = reason
            picked.append(p)

    for ch in chapters:
        span = [p for p in phys_all if ch["a"] <= p <= ch["b"]]
        if not span:
            continue
        _take(span[len(span) // 2], f"第{ch['no']}章「{ch['name']}」章中")
    # 均匀补足
    if len(picked) < min_total:
        step = max(1, len(phys_all) // min_total)
        for p in phys_all[::step]:
            if len(picked) >= min_total:
                break
            _take(p, "全书均匀抽样")
    # 仍不足（书太薄）→ 全抽
    for p in phys_all:
        if len(picked) >= min_total:
            break
        _take(p, "全书页数不足抽检下限，逐页抽")
    return [(p, why[p]) for p in sorted(picked)]


def gate_a(blocks, chapters, book_type, whitelist, min_total=10):
    """闸A：抽页 + 独立基准（ocr_confusion_dict）扫描。

    返回 (picks, hits)：picks=[(phys, 说明)]；hits=[(phys, 命中描述)]。
    词典不可用时不假装通过——返回特殊标记，由调用方显著提示。
    """
    picks = pick_pages(blocks, chapters, min_total)
    try:
        from ocr_confusion_dict import scan as dict_scan
    except Exception as e:
        return picks, [("?", f"⚠️ 混淆词典不可用（{e}），闸A 未执行，不得据此判通过")]
    hits = []
    for p, _why in picks:
        for h in dict_scan(blocks[p], book_type, whitelist=whitelist):
            hits.append((p, h))
    return picks, hits


# ============================ 闸B 书账互释 ============================

def load_active(work):
    """加载 active 记录。记录源 = corrections/*.json（统一口径见 _common.load_active_patches）。

    历史坑（2026-10-19 修）：此处一度优先读 patch_manifest.json。manifest 只落 9 个键，
    **不含 regex / systematic**，正则系统性补丁被当字面量施加 → 维民两条零字归一正则全失配，
    闸B 反向假报 47 页未登记差异（全是年份 O/〇→○）。manifest 现在只作为 deleted 墓碑来源。
    """
    return load_active_patches(work)


def load_manual_revisions(out_dir, prefix):
    """从 04 §四 4.2 提取人工定点修订（修订路径的登记）。

    v2.2.0（S4）：§四 4.4「考据裁定与预处理处置登记」**不参与解析**——该节是人写
    裁定记录（多为「裁定不修」），其中的「phys…「X」→「Y」」行形是记录在案的决定，
    不是修订动作；不豁免则未修改项的 old 进入闸B 正向，合法文本被误判残留阻断
    （自指陷阱的变体：解释性引用被当作执行记录）。真人工修订走 corrections 条目
    （type=人工定点修订 → 4.2 索引）。
    v2.3.1：解析范围由 4.1–4.3 收窄为**仅 4.2**。4.1「经核实撤销的补丁」是
    status=deleted 墓碑的审计节（gen_correction_ledger 生成），撤销补丁的 old
    本就该留在 03（或修正已由通改代劳）——当修订解析等于把台账已排除的东西重新
    放进闸B 正向，与「只查 active」的既定口径（SKILL §6.1.2）直接冲突；其
    「old→new」行形是撤销记录而非修订动作。4.3「派生件同步」是 notes→01/02 同步
    记录，同样不是修订动作。实测：《中国的历史脉动》54 条撤销补丁被当修订解析，
    phys=45 一条截断出假 old==new（正向残留误阻断），phys=5/28 两条截断出短 new
    （反向复现删掉引文括注，假报 2 页未登记差异）。
    """
    p04 = os.path.join(out_dir, (prefix or "") + DELIVERY_SUFFIX["04"])
    if not os.path.isfile(p04):
        return []
    txt = read_text_cn(p04)
    m = re.search(r"^##\s*四、[^\n]*\n(.*?)(?=^##\s|\Z)", txt, re.S | re.M)
    if not m:
        return []
    m2 = re.search(r"^###\s*4\.2\b[^\n]*\n(.*?)(?=^###\s|\Z)", m.group(1), re.S | re.M)
    if not m2:
        return []
    revs = []
    for line in m2.group(1).splitlines():
        mm = _REV_RE.search(line)
        if mm:
            revs.append({"phys": mm.group(1), "old": mm.group(2), "new": mm.group(3),
                         "source": "04 §四 人工修订"})
    return revs


def _apply_records(body, records):
    """把登记（台账补丁 + 04 §四 人工修订）施加到一段正文，得「照账应有的样子」。

    这是闸B 反向的判据基础：如果账是完整的，施加后应与 03 该页逐字相同。
    **匹配语义必须与 apply_corrections 完全一致**，否则闸把自己没复现出来的已登记改动
    报成「未登记差异」——这类假阳性比漏报更伤，因为它逼人去核一条已经改对的地方。
    故：
      · 字面型走 iter_spans（精确 → 容忍换行 → 容忍空白/全半角），不是 compile_linebreak：
        维民 phys23「裁掉无 功」底本带空格、phys236 补丁 old 里带空格而底本换行，
        只用 compile_linebreak 两条都失配，反向假报未登记。
      · 正则型原样 re.sub。
    """
    out = body
    for r in records:
        old, new = r.get("old", ""), r.get("new", "")
        if not old:
            continue
        if r.get("regex"):
            try:
                out = re.sub(old, new, out)
            except re.error:
                continue
            continue
        spans = iter_spans(out, old)
        for s, e in reversed(spans):  # 反向施加，否则前段替换会移动后段坐标
            out = out[:s] + new + out[e:]
    return out


_HDR_LINE_RE = re.compile(r"^\s*第[一二三四五六七八九十百千\d]+[章节编卷篇部]")


def _content_text(text):
    """去掉结构行与全部空白，得纯字符流。

    为什么要抹掉换行：struct 是 OCR 原始断行（一句常断成两三行），03 经过结构清洗后断行更少，
    按行比会把「同一句话断法不同」全算成改动（实测占未登记差异的六成以上）。
    抹掉空白后，剩下的才是真正的字符增删改。
    """
    keep = []
    for l in text.splitlines():
        s = l.strip()
        if not s or "<<<PAGE" in s or _HDR_LINE_RE.match(s):
            continue
        keep.append(s)
    return "".join(keep).replace(" ", "").replace("\u3000", "")


def _resolve_placements(records, struct_blocks):
    """把每条逐页记录定位到它**实际生效**的页（复刻 apply_corrections 的定位语义）。

    apply_corrections 对逐页补丁有两条规则（_common.apply_corrections_core）：
      ① 先在自己的声明页找；找到就地施加；
      ② 声明页找不到且 old ≥4 字 → **全局回退**，落到全书第一个命中的页施加。
    所以「台账 phys 与底本对不上」并不等于「这条没生效」——它照样改了书，只是改在了别的页。
    反向比对若只按声明页摆补丁，就会把这类已生效改动报成「未登记」，
    同时漏掉真正该提示的「台账页号写错」。

    返回 (placed, misplaced, unresolved, tolerant_only)：
      placed[phys] = [记录]       该页实际要施加的记录
      misplaced    = [(声明页, 实测页, 记录)]   页归属错位
      unresolved   = [记录]       本页与全书均无命中（账/底本真不一致）
      tolerant_only= [记录]       精确与「仅容忍换行」都命不中的记录（只有放宽空白/全半角才命中）
    """
    from collections import defaultdict
    placed = defaultdict(list)
    misplaced, unresolved, tolerant_only = [], [], []
    order = sorted(struct_blocks)          # 文档顺序近似（phys 升序与 blocks 迭代同序）
    for r in records:
        if r.get("systematic"):
            continue
        old = r.get("old", "")
        if not old:
            continue
        ph = r.get("phys")
        declared = int(ph) if str(ph).isdigit() else None
        blk = struct_blocks.get(declared) if declared is not None else None
        if blk is not None and find_span(blk, old):
            placed[declared].append(r)
            if not _strict_hit(blk, old):
                tolerant_only.append(r)
            continue
        if len(old) >= 4:
            for p in order:
                if p == declared:
                    continue
                if find_span(struct_blocks[p], old):
                    placed[p].append(r)
                    misplaced.append((declared, p, r))
                    break
            else:
                unresolved.append(r)
        else:
            unresolved.append(r)
    return placed, misplaced, unresolved, tolerant_only


def _strict_hit(body, old):
    """「逐字节或仅容忍换行」能否命中——用来标出只有放宽匹配才命中的记录。

    这类记录是反向差异**假阳性的来源**：按底本写法逐字比对时它不命中，
    改动就看起来没人认领；实际台账写得明明白白。（维民 phys23/236/76 即此类。）
    """
    if old in body:
        return True
    try:
        return bool(compile_linebreak(old).search(body))
    except re.error:
        return False


def _reverse_diff(clean03, struct, records):
    """反向：逐页块比较「照账应有的样子」与 03 实际，报未被任何登记解释的差异。

    为什么按页块而不按全书：全书比对会把分页/断行差异全算成改动，噪声淹没信号；
    页块内结构同源，差异才是真差异。页块内再按字符流比，进一步消掉断行噪声。

    返回 (unreg, misplaced, unresolved, tolerant_only, structural)：
      unreg        [(phys, [差异描述])]  真未登记（账上找不到任何一条能解释它）
      misplaced    [(声明页, 实测页, 记录)]
      unresolved   [记录]
      tolerant_only[记录]
      structural   [(phys, [差异描述])]  ④ 结构标注（v2.3.0）：整页差异**全部**是行首 Markdown
                   标题标记（`#{1,6}`）——即 03 有标题、struct 侧没有。这不是内容改动，
                   是「标题只落在 03、没落 struct」；单列不计入未登记（口径见 SKILL §6.1.2）。
    """
    import difflib
    # 预测基线先剥页眉（assemble 在 apply 之后同做一次），否则每条页眉都算成未登记差异
    struct_stripped, _ = strip_leaked_headers(struct)
    struct_blocks = _page_blocks(struct_stripped)
    clean_blocks = _page_blocks(clean03)
    sys_recs = [r for r in records if r.get("systematic")]
    placed, misplaced, unresolved, tolerant_only = _resolve_placements(records, struct_blocks)

    # 通改让位（apply_corrections 的 page_claims 机制）：通改在逐页补丁的声明区间让位，
    # 于是「按通改前形态书写」的逐页 old 得以原样命中。静态复现不模拟让位，
    # 这类记录会在该页留一条差异残影——识别出来只为给差异行加一句人话注脚，不改变计数。
    deferred = {}
    for phys, blk in struct_blocks.items():
        for r in placed.get(phys, []):
            if not find_span(_apply_records(blk, sys_recs), r["old"]):
                deferred.setdefault(phys, []).append(r)

    out, structural = [], []
    for phys, blk in sorted(struct_blocks.items()):
        if phys not in clean_blocks:
            continue
        predicted = _apply_records(blk, sys_recs + placed.get(phys, []))
        a = _content_text(predicted)
        b = _content_text(clean_blocks[phys])
        if a == b:
            continue
        descs, hdescs = [], []
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            la, lb = a[i1:i2], b[j1:j2]
            ctx_l = a[max(0, i1 - 8):i1]
            ctx_r = a[i2:i2 + 8]
            # ④ 结构标注：纯插入一个行首标题标记（`#`…`######`），别处一字未动。
            # 判据故意收得很紧——只认「插入、且插入内容恰好是 1–6 个 #」，正文里真有
            # 井号或标题被改写（插入带字）都不算，仍走③。
            if tag == "insert" and not la and re.fullmatch(r"#{1,6}", lb or ""):
                hdescs.append(f"[结构标注] …{ctx_l}【{lb}】{ctx_r}…")
                continue
            hint = ""
            for r in deferred.get(phys, []):
                if la and la in r["old"]:
                    hint = f"（该页有通改让位记录：台账「{r['old'][:24]}」，静态复现不留让位，属复现口径差）"
                    break
            if not hint:
                for r in records:
                    if r.get("systematic") or not r.get("new"):
                        continue
                    if lb and lb in r["new"] and la and la in r["old"]:
                        hint = f"（疑似台账「{r['old'][:20]}」→「{r['new'][:20]}」未能在本页定位）"
                        break
            descs.append(f"[{tag}] …{ctx_l}【{la}】{ctx_r}… → 【{lb}】{hint}")
        if descs:
            # 混合页：仍算未登记（有真改动），标题项并行列示但不额外计页
            out.append((phys, descs + hdescs))
        elif hdescs:
            structural.append((phys, hdescs))
    return out, misplaced, unresolved, tolerant_only, structural


def gate_b(clean03, struct, work, out_dir, prefix):
    """闸B：正向（记录→书）+ 反向（书→记录）。

    正向 = 阻断判据：台账 / 04 §四 记录的 old 在 03 干净正文仍有残留 → 书里还有错。
    反向 = 报告项，按四类分流（2026-10-19 B 档；2026-10-22 加 ④）：
      ① 已登记（消除误报）——账里有、只是需要放宽匹配才复现；不再是「未登记」。
      ② 页归属错位      ——账里的 phys 写错，补丁实际落在别的页（报「台账 phys=A / 实测 B」）。
      ③ 真未登记        ——账上找不到任何一条能解释的改动（漏登记，或 assemble 后手改）。
      ④ 结构标注        ——整页差异全是行首 Markdown 标题标记（03 有标题、struct 没有）。
                           不是内容改动，是「标题没落 struct」；单列不计未登记（见 SKILL §6.1.2）。
    旧版把②的账页错记成「账/底本错位」、把①混进③，两类噪声压过了真信号。
    """
    active = load_active(work)
    revs = load_manual_revisions(out_dir, prefix)
    records = active + revs

    residual = []
    blocks = _page_blocks(clean03)
    body03 = _body_of(clean03)
    for r in records:
        old, new = r["old"], r.get("new", "")
        if not old or r.get("regex"):
            continue
        if is_residual_full(clean03, old, new):
            phys = r.get("phys", "")
            page = blocks.get(int(phys)) if str(phys).isdigit() else None
            if page is not None:
                _pg = page.replace(new, "\x00") if new else page
                where = "声明页内残留" if old in _pg else "全文仍见（phys 可能错位）"
            else:
                where = "全文仍见（phys 非具体页）"
            # v2.2.0（S6）：附残留实例上下文（±15 字，最多 5 处）——合法同形与真残留
            # 靠上下文一眼可判（实测案例：宽形 old「同殷」误中「同殷辭」，商殷语义），
            # 不必回全书自行找。实例从正文区取；落在 new 区间内的（增字型判定时掩掉的
            # 那类）不列，与 is_residual_full 口径一致。判定口径不动（阻断语义不松）。
            nspans = ([m.span() for m in re.finditer(re.escape(new), body03)]
                      if (new and old != new) else [])
            inst = []
            for m in re.finditer(re.escape(old), body03):
                s, e = m.span()
                if any(ns <= s and e <= ne for ns, ne in nspans):
                    continue
                cs, ce = max(0, s - 15), min(len(body03), e + 15)
                inst.append(body03[cs:ce].replace("\n", "⏎"))
                if len(inst) >= 5:
                    break
            tail = f"；残留实例：{' ｜ '.join(inst)}" if inst else ""
            residual.append(f"phys={phys} 「{old}」→「{new}」（来源 {r.get('source','')}，{where}）{tail}")

    if struct:
        unreg, misplaced, unresolved, tolerant_only, structural = _reverse_diff(clean03, struct, records)
    else:
        unreg, misplaced, unresolved, tolerant_only, structural = [], [], [], [], []
    return {
        "residual": residual,
        "unreg": unreg,
        "structural": structural,
        "misplaced": misplaced,
        "unresolved": unresolved,
        "tolerant_only": tolerant_only,
        "n_records": len(records),
    }


# ============================ 交付完整性 ============================

def check_file_hygiene(path, exempt_placeholder_section=False):
    """BOM / CRLF / 占位符 / 空文件。

    v2.2.0（S3）：exempt_placeholder_section=True 时（仅 04），先剥掉 §四 人工登记节
    （`## 四、`～`## 五、`）再扫占位符——该节是人写内容，描述性使用这些词形是它的职能
    （解释某处为何误报必然引用词形本身，自指陷阱）。BOM / CRLF / 空文件仍全查，
    豁免只针对占位符一类、只针对 §四 一节。"""
    issues = []
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        issues.append("BOM残留(UTF-8)")
    elif raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        issues.append("BOM残留(UTF-16)")
    n_crlf = raw.count(b"\r\n")
    if n_crlf:
        issues.append(f"CRLF残留{n_crlf}处（应为LF）")
    if not raw.strip():
        issues.append("文件为空")
    try:
        text = raw.decode("utf-8", errors="ignore")
        if exempt_placeholder_section:
            text = re.sub(r"## 四、.*?(?=## 五、|\Z)", "", text, flags=re.S)
        ph = []
        for pat in _PLACEHOLDER_PATS:
            n = len(re.findall(pat, text))
            if n:
                ph.append(f"{pat}({n}处)")
        if ph:
            issues.append("占位符残留：" + ", ".join(ph))
    except Exception:
        pass
    return issues


def check_delivery(out_dir, prefix):
    """五件齐全 + 格式卫生 + 图引用实体。返回 (missing, issues, img_issues)。"""
    missing, issues = [], []
    for key, sfx in DELIVERY_SUFFIX.items():
        p = os.path.join(out_dir, prefix + sfx)
        if not os.path.isfile(p):
            missing.append(f"{key}({sfx})")
            continue
        iss = check_file_hygiene(p, exempt_placeholder_section=(key == "04"))
        if iss:
            issues.append((key, iss))
    # 图引用 ⊆ 实体
    p03 = os.path.join(out_dir, prefix + DELIVERY_SUFFIX["03"])
    img_issues = []
    if os.path.isfile(p03):
        txt = read_delivery03(out_dir, prefix)  # 分册模式下 = 各分册拼接（索引页不参与）
        refs = set(re.findall(r"((?:imgs|fig)/[^)\s\"'>\]]+?\.(?:jpg|jpeg|png|gif|webp|svg))", txt, re.I))
        miss = sorted(r for r in refs if not os.path.isfile(os.path.join(out_dir, r)))
        if miss:
            head = "、".join(miss[:5]) + ("…" if len(miss) > 5 else "")
            img_issues.append(f"图引用断链 {len(miss)}/{len(refs)}（{head}）——03 图路径相对交付目录，"
                              f"须把 work 的 imgs/ 同步过来")
    return missing, issues, img_issues


def check_fingerprint(out_dir, prefix):
    """交付指纹：04 里记的 00/01/02/03 sha256 ↔ 实件（防 assemble 之后手改）。

    指纹行由 pipeline_assemble / assemble_epub 在定稿前写入 04 末尾（`## 五、交付指纹`），
    形如 `00=sha256:<64hex>；01=…；02=…；03=…`；04 自身不参与（自指无意义）；
    交付件已收敛为五件（00–04），指纹覆盖 00–03。

    返回 (state, issues)：state ∈ {"ok"（核过且一致）, "absent"（04 无指纹行，未核验）, "n/a"}。
    404 的旧交付件只有 absent，不能当失败处理——否则历史成果一件都过不了。
    本项从 verify_delivery.py 迁入（2026-10-19）：原实现把已退场的 05 也列入
    齐全性检查，中途运行（05 尚未生成）会给出假失败。
    """
    p04 = os.path.join(out_dir, prefix + DELIVERY_SUFFIX["04"])
    if not os.path.isfile(p04):
        return "n/a", []
    try:
        txt = read_text_cn(p04)
    except Exception:
        return "absent", []
    recorded = dict(_FP_RE.findall(txt))
    if not recorded:
        return "absent", []
    issues = []
    for key in ("00", "01", "02", "03"):
        if key not in recorded:
            continue
        p = os.path.join(out_dir, prefix + DELIVERY_SUFFIX[key])
        if not os.path.isfile(p):
            continue
        # 03 走分册口径（索引页 + 各分册字节序），与 assemble 写指纹同一函数
        actual = sha256_delivery03(out_dir, prefix) if key == "03" else _sha256(p)
        if actual != recorded[key]:
            issues.append(f"{key} sha256 与 04 登记不符——疑似 assemble 定稿后又手改；"
                          f"改动请写回台账（或 04 §四）并重跑 assemble")
    return "ok", issues


def check_preprocess_closure(work_dir, out_dir, prefix):
    """预处理闭环检查（v2.1.0 · 2026-10-20 · 报告项，不阻断）。

    检查 00b/00c/00d/00e 四份预处理报告是否存在，以及发现的红旗项是否在 04 中有处置登记。
    设计原则：只报告不阻断——预处理发现问题但未处置，不阻止交付，但要写得清清楚楚。

    返回 (lines, has_redflag)：
      lines       = 报告行列表（供打印和落盘）
      has_redflag = 是否有「检测了但未处置」的红旗项（仅用于终端醒目标记，不参与退出码）
    """
    lines = []
    has_redflag = False

    # 读 04 全文，用于检测处置登记关键词
    p04 = os.path.join(out_dir, prefix + DELIVERY_SUFFIX["04"])
    o4_text = ""
    if os.path.isfile(p04):
        try:
            o4_text = read_text_cn(p04)
        except Exception:
            pass

    # ---- 1. 表格审计（00b）----
    ta_path = os.path.join(work_dir, "table_audit.json")
    if os.path.isfile(ta_path):
        try:
            ta = read_json_cn(ta_path)
            total = ta.get("total_tables", 0)
            s = ta.get("summary", {})
            n_align = s.get("redflag_align_fail", 0)
            if n_align > 0:
                # 检查 04 中是否有表格处置登记
                has_table_treatment = any(kw in o4_text for kw in
                    ["表格重建", "表格处置", "空td", "LLM重建", "表格原图", "表格审计", "错位表"])
                if has_table_treatment:
                    lines.append(f"  ✅ 表格审计：{total} 表，空td>30% 红旗 {n_align} 个，04 已登记处置")
                else:
                    lines.append(f"  ⚠️ 表格审计：{total} 表，空td>30% 红旗 {n_align} 个，04 未发现处置登记"
                                 f"——须对照 imgs/p{{phys}}_table_*.jpg 决定重建或保留")
                    has_redflag = True
            else:
                lines.append(f"  ✅ 表格审计：{total} 表，无空td>30% 红旗")
        except Exception as e:
            lines.append(f"  — 表格审计：报告存在但读取失败（{e}）")
    else:
        lines.append("  — 表格审计：未执行（无 table_audit.json）")

    # ---- 2. 碎片剥离（00c）----
    rna_path = os.path.join(work_dir, "region_noise_audit.md")
    rnl_path = os.path.join(work_dir, "region_noise_log.md")
    if os.path.isfile(rna_path):
        if os.path.isfile(rnl_path):
            lines.append("  ✅ 碎片剥离：已 --apply（region_noise_log.md 存在）")
        else:
            # v2.3.1：审计**命中剥离 0 页**时「无 log」属正常——没有页可剥，不存在
            # 漏执行。旧逻辑无条件按 dry-run 红旗报：《中国的历史脉动》（命中剥离页 0）
            # 被误报「碎片仍在底本中」，且该分支不看 04 是否已登记处置，连带
            # 「有红旗项未在 04 登记处置」提示误亮。解析失败（取不到命中数）时保守
            # 仍按红旗报——宁可误报，不放漏。
            m_hit = re.search(r"命中剥离页[：:]\s*\*?\*?(\d+)", read_text_cn(rna_path))
            n_hit = int(m_hit.group(1)) if m_hit else None
            if n_hit == 0:
                lines.append("  ✅ 碎片剥离：审计命中剥离 0 页，无页可剥"
                             "（未产生 region_noise_log.md 属正常，非漏执行）")
            else:
                lines.append("  ⚠️ 碎片剥离：仅 dry-run（无 region_noise_log.md），碎片仍在底本中")
                has_redflag = True
    else:
        lines.append("  — 碎片剥离：未执行（无 region_noise_audit.md）")

    # ---- 3. 图片缺口（00d）----
    fg_path = os.path.join(work_dir, "figure_gaps.json")
    if os.path.isfile(fg_path):
        try:
            fg = read_json_cn(fg_path)
            counts = fg.get("counts", {})
            n_a = counts.get("A", 0)
            n_c = counts.get("C", 0)
            if n_a + n_c > 0:
                has_fig_treatment = any(kw in o4_text for kw in
                    ["图片补嵌", "图版嵌入", "图片缺口", "补图", "figure_gap"])
                if has_fig_treatment:
                    lines.append(f"  ✅ 图片缺口：A类断链 {n_a} / C类文字层 {n_c}，04 已登记处置")
                else:
                    lines.append(f"  ⚠️ 图片缺口：A类断链 {n_a} / C类文字层 {n_c}，04 未发现处置登记")
                    has_redflag = True
            else:
                lines.append("  ✅ 图片缺口：无缺口")
        except Exception as e:
            lines.append(f"  — 图片缺口：报告存在但读取失败（{e}）")
    else:
        lines.append("  — 图片缺口：未执行（无 figure_gaps.json）")

    # ---- 4. 简繁抖动（00e）----
    sl_path = os.path.join(work_dir, "simplify_leak_report.md")
    if os.path.isfile(sl_path):
        try:
            text = read_text_cn(sl_path)
            m_auto = re.search(r"通改候选\s*(\d+)\s*字", text)
            n_auto = int(m_auto.group(1)) if m_auto else 0
            if n_auto > 0:
                has_simplify_treatment = any(kw in o4_text for kw in
                    ["简繁", "繁简", "通改", "simplify", "码位抖动"])
                if has_simplify_treatment:
                    lines.append(f"  ✅ 简繁抖动：通改候选 {n_auto} 字，04 已登记处置")
                else:
                    lines.append(f"  ⚠️ 简繁抖动：通改候选 {n_auto} 字，04 未发现处置登记——须核 §3.8 收窄口径")
                    has_redflag = True
            else:
                lines.append("  ✅ 简繁抖动：无通改候选")
        except Exception as e:
            lines.append(f"  — 简繁抖动：报告存在但读取失败（{e}）")
    else:
        lines.append("  — 简繁抖动：未执行（无 simplify_leak_report.md；简体底本可跳过）")

    return lines, has_redflag


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================ 主流程 ============================

def main():
    ap = argparse.ArgumentParser(description="交付前唯一阻断闸：闸A 可读性抽检 + 闸B 书账互释 + 交付完整性")
    ap.add_argument("--work", required=True, help="工作目录（含 corrections/、fulltext_struct.txt）")
    ap.add_argument("--out", required=True, help="交付目录（03 所在）")
    ap.add_argument("--meta", default="", help="book_meta.json（取 chapters 与 proper_names）")
    ap.add_argument("--prefix", default="", help="交付文件名前缀，不指定则自动检测")
    ap.add_argument("--no-delivery", action="store_true", help="跳过交付完整性（assemble 中途用）")
    ap.add_argument("--sample", type=int, default=10, help="闸A 抽页下限（默认 10）")
    ap.add_argument("--audit", default="", help="结论写入文件（默认 <work>/gate_report.md）")
    args = ap.parse_args()

    B, OUT = args.work, args.out
    if not os.path.isdir(OUT):
        print(f"❌ 交付目录不存在：{OUT}")
        return 2
    prefix = find_prefix(OUT, args.prefix)
    p03 = find_03(OUT, prefix)
    if not p03:
        print(f"❌ 未找到 03_*.md（--out 是否指向交付目录？）：{OUT}")
        return 2

    # 分册模式（2026-10-27）：p03 只是索引页，正文按分册拼接——读取口径收在 _common，
    # 与 assemble 写指纹时用同一函数，避免两侧各算各的。
    txt03 = read_delivery03(OUT, prefix)
    clean03 = strip_correction_traces(txt03, strip_tonggai_lines=True)
    blocks = _page_blocks(clean03)

    meta_path = args.meta or os.path.join(B, "book_meta.json")
    book_type, whitelist = "", []
    if os.path.isfile(meta_path):
        try:
            meta = read_json_cn(meta_path)
            book_type = meta.get("type", "") or ""
            whitelist = meta.get("proper_names", []) or []
        except Exception as e:
            print(f"⚠️ book_meta 读取失败（书型过滤降级）：{e}")

    print("=" * 72)
    print(f"check_book · {os.path.basename(p03)}")
    print(f"前缀 {prefix or '(无)'} | 书型 {book_type or '(未提供)'} | 干净正文 {len(clean03)} 字符 / {len(blocks)} 页")
    print("=" * 72)

    # ---- 闸A ----
    chapters = load_chapters(meta_path)
    picks, a_hits = gate_a(blocks, chapters, book_type, whitelist, args.sample)
    print(f"\n【闸A 可读性抽检】抽 {len(picks)} 页"
          + (f"（每章 ≥1：{len(chapters)} 章）" if chapters else "（无章信息，全书均匀抽）"))
    if picks:
        print("  抽到：" + "、".join(f"phys={p}" for p, _ in picks))
    else:
        print("  抽到：（无页可抽）")
    if a_hits:
        print(f"  ⚠️ 疑似读不通 {len(a_hits)} 处（清单交人核，**不阻断**）：")
        for p, h in a_hits[:40]:
            print(f"     phys={p} | {h}")
        if len(a_hits) > 40:
            print(f"     …（共 {len(a_hits)} 处，见 gate_report.md）")
        print("     处置：确认是书的错 → 改 03 并在 04 §四 留痕；确认误报 → 记一笔放行。")
    else:
        print("  ✅ 抽到的页未发现词典命中")

    # ---- 闸B ----
    struct_path = os.path.join(B, "fulltext_struct.txt")
    struct = read_text_cn(struct_path) if os.path.isfile(struct_path) else ""
    if not struct:
        print(f"\n⚠️ 未找到 fulltext_struct.txt，闸B 反向（diff 兜底）跳过")
    gb = gate_b(clean03, struct, B, OUT, prefix)
    b_residual, b_unreg = gb["residual"], gb["unreg"]
    b_misplaced, b_unresolved, b_tolerant = gb["misplaced"], gb["unresolved"], gb["tolerant_only"]
    b_structural = gb.get("structural", [])
    n_records = gb["n_records"]
    print(f"\n【闸B 书账互释】记录 {n_records} 条（台账 active + 04 §四 人工修订）")
    if b_residual:
        print(f"  ❌ 正向残留 {len(b_residual)} 处 —— 03 里还留着记录说要改的字（书里确实还有错）")
        for r in b_residual[:30]:
            print(f"     · {r}")
        if len(b_residual) > 30:
            print(f"     …（共 {len(b_residual)} 处）")
        print("     处置：确认书对 → 补改 03 并把该处写进 04 §四；确认账错 → 台账标 deleted。二者都不需要整本重跑。")
    else:
        print("  ✅ 正向：记录里的 old 在 03 中已清零")

    # 反向差异三类分流：先说「已经解释掉的」，再说「需要动手的」
    if struct:
        print(f"  · ① 已登记（消除误报）{len(b_tolerant)} 条 —— 台账有这条，只是底本带空格/全半角差异，"
              f"逐字比对命不中；放宽匹配即复现，不再计入未登记")
        for r in b_tolerant[:5]:
            print(f"       phys={r.get('phys','')} 「{r['old'][:28]}」")
        if len(b_tolerant) > 5:
            print(f"       …（共 {len(b_tolerant)} 条）")
    if b_misplaced:
        print(f"  · ② 页归属错位 {len(b_misplaced)} 条 —— 台账 phys 与底本实际页不符"
              f"（补丁按全局回退仍生效，改动落在实测页，不是漏改）")
        for declared, actual, r in b_misplaced[:15]:
            print(f"       台账 phys={declared} / 实测 {actual}｜「{r['old'][:26]}」（{r.get('source','')}）")
        if len(b_misplaced) > 15:
            print(f"       …（共 {len(b_misplaced)} 条）")
        print("       处置：把台账该条 phys 改成实测页（或确认行号来自印页、统一偏移）；不改书。")
    if b_unresolved:
        print(f"  · ②附 全书 0 命中 {len(b_unresolved)} 条 —— 声明页和全书都找不到 old"
              f"（台账 old 写成了终稿形态，或该处根本无需补丁）")
        for r in b_unresolved[:10]:
            print(f"       phys={r.get('phys','')} 「{r['old'][:28]}」（{r.get('source','')}）")
        if len(b_unresolved) > 10:
            print(f"       …（共 {len(b_unresolved)} 条）")
    if b_unreg:
        n_lines = sum(len(d) for _, d in b_unreg)
        print(f"  · ③ 真未登记 {len(b_unreg)} 页 / {n_lines} 行 ——"
              f"账上没有任何一条能解释这些改动（漏登记，或 assemble 后手改 03）")
        shown = 0
        for phys, dl in b_unreg:
            for l in dl:
                if shown >= 20:
                    break
                print(f"     phys={phys} {l[:100]}")
                shown += 1
            if shown >= 20:
                break
        print("     处置：确认是漏登记 → 补进 04 §四；确认是台账 old 写错 → 修台账。不整本重跑。")
    elif struct:
        print("  ✅ ③ 真未登记：逐页比对无未登记差异（施加登记即可复现 03）")
    if b_structural:
        n_s = sum(len(d) for _, d in b_structural)
        print(f"  · ④ 结构标注 {len(b_structural)} 页 / {n_s} 处 —— 差异全是行首标题标记"
              f"（03 有标题、struct 侧没有）；非内容改动，不计未登记")
        for phys, dl in b_structural[:5]:
            print(f"     phys={phys} {dl[0][:80]}")
        if len(b_structural) > 5:
            print(f"       …（共 {len(b_structural)} 页）")
        print("     处置：`01c_add_headings.py --work <work>` 把标题补进 fulltext_struct 后重跑 assemble；"
              "标题落 struct 后本项自然清零，且重跑不会再丢。")

    # ---- 交付完整性 ----
    missing, hygiene, img_issues = ([], [], [])
    fp_state, fp_issues = ("跳过", [])
    if not args.no_delivery:
        missing, hygiene, img_issues = check_delivery(OUT, prefix)
        print(f"\n【交付完整性】五件（00–04）")
        if missing:
            print(f"  ❌ 缺失 {len(missing)} 件：{'、'.join(missing)}")
        else:
            print("  ✅ 五件齐全")
        if hygiene:
            for k, iss in hygiene:
                print(f"  ❌ {k}：{'; '.join(iss)}")
        else:
            print("  ✅ 无 BOM/CRLF/占位符残留，无空文件")
        if img_issues:
            print(f"  ❌ {img_issues[0]}")
        else:
            print("  ✅ 图引用均落在交付目录（或无图）")
        fp_state, fp_issues = check_fingerprint(OUT, prefix)
        if fp_issues:
            print("  ⚠️ 交付指纹与 04 登记不符（不阻断）——00–03 在 assemble 定稿后被改过：")
            for s in fp_issues:
                print(f"     - {s}")
            print("     处置：走修订路径的正常（定点改 03 + 04 §四 留痕），把 04 §五 指纹行按新内容重算；"
                  "否则说明有未留痕的手改，补 04 §四 或回退。")
        elif fp_state == "ok":
            print("  ✅ 交付指纹：00/01/02/03 与 assemble 定稿一致")
        else:
            print("  — 交付指纹：04 无指纹行（早于指纹机制的旧交付件），本项未核验")

        # ---- 预处理闭环（v2.1.0 · 报告项，不阻断）----
        pre_lines, pre_redflag = check_preprocess_closure(B, OUT, prefix)
        print(f"\n【预处理闭环】00b/00c/00d/00e（报告项，不阻断）")
        for line in pre_lines:
            print(line)
        if pre_redflag:
            print("  处置：有红旗项未在 04 登记处置——不阻断交付，但请回头核对预处理报告。")

        # ---- 图版清理（v2.5.0 · 路径三专属报告项，不阻断）----
        cleanup_lines = []
        if os.path.isfile(os.path.join(B, "bookocr_dir.txt")):
            def _count_files(d):
                if not os.path.isdir(d):
                    return 0
                return sum(1 for fn in os.listdir(d) if os.path.isfile(os.path.join(d, fn)))
            n_work_imgs = _count_files(os.path.join(B, "imgs")) + _count_files(os.path.join(B, "fig"))
            if not n_work_imgs and os.path.isfile(os.path.join(B, "cleanup_log.md")):
                cleanup_lines.append("  ✅ 图版已回收（work/imgs·fig 已清扫，cleanup_log.md 在，权威图在交付目录）")
            elif n_work_imgs and not os.path.isfile(os.path.join(B, "cleanup_log.md")):
                cleanup_lines.append(f"  ⚠️ 图版未回收：work/imgs·fig 仍有 {n_work_imgs} 张图，交付收尾后请跑 "
                                     f"09_cleanup_after_delivery.py（先 dry-run 再 --apply）")
            elif not n_work_imgs and not os.path.isfile(os.path.join(B, "cleanup_log.md")):
                cleanup_lines.append("  ⚠️ work/imgs·fig 已不存在但无 cleanup_log.md——疑似手工删除，建议补跑 09 留痕")
            else:
                cleanup_lines.append("  ⚠️ work/imgs·fig 与 cleanup_log.md 同时存在——cleanup_log 可能过期，重跑 09 核对")
        else:
            cleanup_lines.append("  — 非路径三（无 bookocr_dir.txt），图版回收 N/A")
        print(f"\n【图版清理】（v2.5.0 · 报告项，不阻断）")
        for line in cleanup_lines:
            print(line)
    else:
        pre_lines, pre_redflag = [], False
        cleanup_lines = []

    # ---- 结论 ----
    blocking = bool(missing) or bool(hygiene) or bool(img_issues) or bool(b_residual)
    print("\n" + "-" * 72)
    if blocking:
        print("❌ 未通过。阻断项："
              + "、".join(filter(None, [
                  f"交付缺件{len(missing)}" if missing else "",
                  f"格式问题{len(hygiene)}件" if hygiene else "",
                  f"图断链" if img_issues else "",
                  f"03 残留{len(b_residual)}处" if b_residual else "",
              ])))
    else:
        print("✅ 通过。"
              + (f"闸A 有 {len(a_hits)} 处待核项（不阻断，照清单核）。" if a_hits else "")
              + (f"闸B 反向：已登记消除误报 {len(b_tolerant)} 条、页归属错位 {len(b_misplaced)} 条、"
                 f"全书 0 命中 {len(b_unresolved)} 条、真未登记 {len(b_unreg)} 页（均不阻断）。"
                 if (b_tolerant or b_misplaced or b_unresolved or b_unreg) else ""))
    print("-" * 72)

    # ---- 落盘 ----
    audit = args.audit or os.path.join(B, "gate_report.md")
    try:
        lines = [
            "# 设闸结论（check_book）", "",
            f"- 交付件：`{os.path.basename(p03)}`",
            f"- 书型：{book_type or '(未提供)'}｜干净正文 {len(clean03)} 字符 / {len(blocks)} 页",
            f"- 结论：{'未通过' if blocking else '通过'}", "",
            "## 闸A 可读性抽检", "",
            f"- 抽页 {len(picks)} 页：" + ("、".join(f"phys={p}" for p, _ in picks) or "（无）"),
            f"- 疑似读不通 {len(a_hits)} 处（不阻断，交人核）：",
        ]
        for p, h in a_hits[:200]:
            lines.append(f"  - phys={p} | {h}")
        if not a_hits:
            lines.append("  - （无）")
        lines += ["", "## 闸B 书账互释", "",
                  f"- 记录 {n_records} 条（台账 active + 04 §四 人工修订）",
                  f"- 正向残留 {len(b_residual)} 处" + ("（阻断）" if b_residual else ""),
                  f"- ① 已登记（消除误报）{len(b_tolerant)} 条：台账有这条，放宽匹配即复现（不阻断）",
                  f"- ② 页归属错位 {len(b_misplaced)} 条：台账 phys 与底本实际页不符（不阻断）",
                  f"- ②附 全书 0 命中 {len(b_unresolved)} 条：old 在声明页与全书都找不到（不阻断）",
                  f"- ③ 真未登记 {len(b_unreg)} 页：账上无任何条目可解释（不阻断）"]
        for r in b_residual[:200]:
            lines.append(f"  - {r}")
        for declared, actual, r in b_misplaced[:100]:
            lines.append(f"  - [页归属] 台账 phys={declared} / 实测 {actual}｜「{r['old'][:40]}」"
                         f"（{r.get('source','')}）")
        for r in b_unresolved[:100]:
            lines.append(f"  - [0命中] phys={r.get('phys','')}「{r['old'][:40]}」（{r.get('source','')}）")
        for phys, dl in b_unreg[:50]:
            for l in dl[:5]:
                lines.append(f"  - [未登记] phys={phys} {l}")
        lines += ["", "## 交付完整性", ""]
        lines.append(f"- 缺失：{'、'.join(missing) or '无'}")
        lines.append(f"- 格式问题：{('；'.join(f'{k}: {chr(59).join(v)}' for k, v in hygiene)) or '无'}")
        lines.append(f"- 图引用：{img_issues[0] if img_issues else '全部落地'}")
        lines.append(f"- 交付指纹：{'不符 ' + str(len(fp_issues)) + ' 件（报告项：修订路径改过则正常，须更新 04 §五）' if fp_issues else ('一致' if fp_state == 'ok' else '04 无指纹行，未核验')}")
        if pre_lines:
            lines += ["", "## 预处理闭环（报告项，不阻断）", ""]
            lines.extend(pre_lines)
        if cleanup_lines:
            lines += ["", "## 图版清理（v2.5.0 · 报告项，不阻断）", ""]
            lines.extend(cleanup_lines)
        lines.append("")
        with open(audit, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        print(f"（结论已写入 {audit}）")
    except Exception as e:
        print(f"⚠️ 审计落盘失败（不影响结论）：{e}")

    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
