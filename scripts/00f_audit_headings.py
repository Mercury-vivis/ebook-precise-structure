#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""00f_audit_headings.py —— 标题结构参考视图 + 裁决执行器（路径三 · 建议工序）

**定位：脚本只作参考，判断归人。**

每本书的标题形态都不一样——古籍是「卷／道／州縣／貢賦」，现代书是「第一章／1.1」，
西文书是「Part I」。任何写死在脚本里的词表或正则，都只能覆盖写它的那一本书；
换一本书，词表就从"帮忙"变成"添乱"（把全书章标题全报成可疑）。

因此本脚本**不做语义判定**：它只把全书标题连同上下文摊开给人看，由人读书判断
哪条该剥、哪条该定级；脚本只负责按人写下的裁决机械执行并留痕。

**它不做什么**
  不判「误标」、不出「候选」、不排序、不打分、不预填动作、不设通过/不通过。
  报告里每一条都只是事实：行号、层级、文本、上下文、前后空行数。

**能对账，是因为书自带目录**
  有目录（`--toc` 或 `work/toc_tree.json`）时，脚本把正文标题逐条对到目录条目上，
  报「一致／部分一致／目录无此条／目录有正文无」。基准是这本书自己的目录，不是
  脚本内置的规则——换书不用改脚本。目录本身也可能漏条、OCR 错字，所以「对不上」
  只说明两边不一致，谁对谁错仍由人判。

**四态**
  默认 / --audit   只读：出 heading_map.md/.json + 空白裁决模板；绝不碰 struct
  --check          只读：校验裁决文件本身（可缺）；缺文件只提示，不算失败
  --apply          执行裁决文件里写明的动作；未写的一律保持原样
  --fix-blank      标题前后空行归一为恰好 1 行（独立开关，默认不做）

**人怎么写裁决**（只写要改的，未写即保持）

  work/heading_decisions.json
  {"_meta": {"heading_count": 165, "struct_sha256": "…"},
   "decisions": {
     "12:ab12cd34": {"action": "strip", "reason": "此处是正文条目字段，非标题"},
     "13:ef56ab78": {"action": "set2",  "reason": "与同卷其余节标题同为二级"}
   }}

  动作：strip（去 # 变正文）｜ set1..set6（改层级）｜ keep（显式记录看过、不改）
  strip / setN 必须写 reason——人工判断的判据要留痕，否则事后无从复核。

**产出（均在 work 目录）**
  heading_map.md                  参考视图：标题地图（按印页分组）+ 形态事实 + 目录比对
  heading_map.json                机读全量标题（id/line/phys/level/text/prev/next/toc）
  heading_decisions.template.json 空白模板（decisions 为空，不含任何预填动作）
  heading_decisions.json          人写；--apply 只认它

**落痕**
  --apply 把实际改动项与理由追加到 work/04_dispositions.md，由 gen_correction_ledger.py
  并入 04 §四 4.4；标题是结构标注、非文字纠错，不进 corrections 台账。

用法
  python 00f_audit_headings.py --work <work>              # 出参考视图（只读）
  python 00f_audit_headings.py --work <work> --toc <目录>  # 同上，并与该书目录对账
  python 00f_audit_headings.py --work <work> --check      # 校验裁决文件
  python 00f_audit_headings.py --work <work> --apply      # 按裁决改 struct
  python 00f_audit_headings.py --work <work> --fix-blank  # 空行归一（可与 --apply 同用）
"""
import argparse
import hashlib
import json
import os
import re
import sys
from bisect import bisect_right
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import read_text_cn, safe_write  # noqa: E402
from _fig_common import PAGE_RE  # noqa: E402

HEAD_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$")
ACTIONS = ("strip", "set1", "set2", "set3", "set4", "set5", "set6", "keep")
PUNCT_END = "。．，；：、！？"
CTX_MAX = 40


def hid(line, text):
    """id = 行号 + 文本指纹。行号或文本一变，id 就变——漂移时旧裁决自然失效。"""
    return f"{line}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]}"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _ctx(lines, i, step):
    """取标题相邻的上一/下一行正文；紧邻另一标题或页锚时如实标出。
    上下文的用处是让人判断"这一行处在什么位置"，所以相邻是标题、页首还是页末，
    本身都是有意义的信息——跨页去抓上一页的正文反而会误导。"""
    j = i + step
    while 0 <= j < len(lines):
        s = lines[j].strip()
        if not s:
            j += step
            continue
        if PAGE_RE.search(s):
            return "（页首）" if step < 0 else "（页末）"
        m = HEAD_RE.match(s)
        if m:
            return "（紧邻标题：" + m.group(2).strip()[:20] + "）"
        return s[:CTX_MAX] + ("…" if len(s) > CTX_MAX else "")
    return "—"


def scan(lines):
    """全量扫标题，返回 [{id,line,phys,level,text,prev,next}]。不筛、不排序、不判定。"""
    anchors = [(i, int(m.group(1))) for i, l in enumerate(lines)
               for m in [PAGE_RE.search(l)] if m]
    a_idx = [a for a, _ in anchors]
    a_phys = [p for _, p in anchors]

    def phys_of(i):
        k = bisect_right(a_idx, i) - 1
        return a_phys[k] if k >= 0 else 0

    out = []
    for i, l in enumerate(lines):
        m = HEAD_RE.match(l)
        if not m:
            continue
        text = m.group(2).strip()
        out.append({"id": hid(i + 1, text), "line": i + 1, "phys": phys_of(i),
                    "level": len(m.group(1)), "text": text,
                    "prev": _ctx(lines, i, -1), "next": _ctx(lines, i, +1)})
    return out


def _blank_run(lines, i, step):
    """标题 i 一侧的连续空行数（仅供 --fix-blank 参考，不进形态事实）。"""
    n, j = 0, i + step
    while 0 <= j < len(lines) and not lines[j].strip():
        n += 1
        j += step
    return n


def shape_facts(heads, lines):
    """形态事实：只描述标题自身的形状，不评价是否该改。
    不含空行数——空行是排版问题，由 --fix-blank 独立处理；混进这里只会刷屏，
    反而把真正需要人看的东西（层级、句读、字数）淹掉。"""
    facts, prev = [], None
    for h in heads:
        if prev is None and h["level"] != 1:
            facts.append((h, f"首个标题为 {h['level']} 级，其前无更高级标题"))
        elif prev is not None and h["level"] - prev > 1:
            facts.append((h, f"层级由 {prev} 级直接到 {h['level']} 级"))
        prev = h["level"]
        if h["text"] and h["text"][-1] in PUNCT_END:
            facts.append((h, f"以句读「{h['text'][-1]}」结尾"))
        if 0 < len(h["text"]) <= 2:
            facts.append((h, f"仅 {len(h['text'])} 字"))
    return facts


_STRIP_CHARS = "·・，。、；：？！“”‘’「」『』（）()《》〈〉【】[]〔〕…—－-～~ \t\u3000"


def norm(s):
    """比对用归一：去尾部页码、去空白/标点/点线。只用于对账，不用于判定。"""
    s = re.sub(r"[.．·…\s]+\d+$", "", s or "")   # 目录常见的「标题……12」
    return re.sub(r"[%s]" % re.escape(_STRIP_CHARS), "", s)


def load_toc(path):
    """读目录清单。支持三种格式，返回 [{text, level}]（level 可为 None）。

    ① toc_tree.json / 任何 {"sections":[{title,…}]} 或 [{title,…}]
    ② 纯文本：一行一条，`#` 前缀表层级，或缩进（2 空格/1 tab 记 1 级）
    """
    raw = read_text_cn(path)
    s = raw.lstrip()
    if s.startswith("{") or s.startswith("["):
        data = json.loads(s)
        secs = data.get("sections") if isinstance(data, dict) else data
        out = []
        for e in secs or []:
            t = (e.get("title") or "").strip() if isinstance(e, dict) else str(e).strip()
            if not t:
                continue
            lv = e.get("level") if isinstance(e, dict) else None
            out.append({"text": t, "level": int(lv) if str(lv).isdigit() else None})
        return out
    out = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        m = HEAD_RE.match(line)
        if m:
            out.append({"text": m.group(2).strip(), "level": len(m.group(1))})
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        lv = None
        if indent:
            lv = 1 + indent // 2 if "\t" not in line[:indent] else 1 + line[:indent].count("\t")
            lv = max(1, min(6, lv))
        out.append({"text": line.strip(), "level": lv})
    return out


def match_toc(heads, toc):
    """把正文标题对到目录条目上。只报「对得上／对不上」，不判谁对谁错。
    返回 (rows, missing)：rows=[(head, toc_entry|None, 状态)]；missing=目录有正文无。"""
    used, rows = set(), []
    for h in heads:
        n = norm(h["text"])
        hit, how = None, ""
        for i, t in enumerate(toc):
            if n and norm(t["text"]) == n:
                hit, how = i, "一致"
                break
        if hit is None:
            for i, t in enumerate(toc):
                m = norm(t["text"])
                if len(m) >= 2 and len(n) >= 2 and (m in n or n in m):
                    hit, how = i, "部分一致"
                    break
        if hit is None:
            rows.append((h, None, "目录无此条"))
        else:
            used.add(hit)
            rows.append((h, toc[hit], how))
    missing = [t for i, t in enumerate(toc) if i not in used]
    return rows, missing


def _cell(s):
    return (s or "").replace("|", "\\|")


def report_md(struct, heads, facts, sha, toc_rows=None, toc_missing=None, toc_src=None,
              toc_total=None):
    dist = {}
    for h in heads:
        dist[h["level"]] = dist.get(h["level"], 0) + 1
    dist_s = "、".join(f"{k} 级 {v}" for k, v in sorted(dist.items())) or "无"
    shown = struct.replace(os.sep, "/")
    L = [
        "# 标题结构参考视图（00f）", "",
        f"- 源文件：`{shown}`",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 标题总数：**{len(heads)}**（{dist_s}）",
        f"- struct 指纹：`sha256:{sha[:16]}…`", "",
        "> **本文件只呈现事实，不判断对错。** 哪条该剥、哪条该定级，请对着正文读；",
        "> 要改的条目写进 `heading_decisions.json`，未写的按原样保留。",
        "> 脚本不排序、不打分、不预填动作——它不知道这本书的标题该长什么样。", "",
        "## 一、标题地图（按印页分组）", "",
        "> 「上文／下文」取标题相邻的正文行；紧邻另一标题、页首或页末时如实标出。",
        "",
    ]
    groups = {}
    for h in heads:
        groups.setdefault(h["phys"], []).append(h)
    for phys in sorted(groups):
        L += [f"### phys={phys}" + ("（首个页锚之前）" if phys == 0 else ""), "",
              "| 行 | 层级 | 标题 | 上文 | 下文 |", "|---:|---:|---|---|---|"]
        for h in groups[phys]:
            L.append(f"| {h['line']} | {h['level']} | {_cell(h['text'])} | "
                     f"{_cell(h['prev'])} | {_cell(h['next'])} |")
        L.append("")

    L += ["## 二、形态事实（客观描述，供参考）", "",
          "> 以下只是「形状如此」，**不代表该条有问题**。是否处理由人判断。",
          "> 合法短标题、卷题、条目字段在形态上无法区分，机器只报形状、不报结论。", ""]
    if not facts:
        L += ["（无：全书标题在下列形态维度上未见异常。）", ""]
    else:
        L += ["| 行 | 层级 | 标题 | 现象 |", "|---:|---:|---|---|"]
        for h, why in facts:
            L.append(f"| {h['line']} | {h['level']} | {_cell(h['text'])} | {_cell(why)} |")
        L.append("")

    L += ["## 三、与目录比对（客观对账，供参考）", ""]
    if toc_rows is None:
        L += ["（未提供目录：`--toc` 未指定，且 `work/toc_tree.json` 不存在，未做比对。）",
              "",
              "> 有目录就能对账：把书的目录页整理成一行一条（`#` 前缀或缩进表层级），",
              "> 或直接给 EPUB 的 `toc_tree.json`，再跑一次即可。", ""]
    else:
        L += [f"- 目录来源：`{toc_src}`（{toc_total if toc_total is not None else '—'} 条）",
              "> 基准是**这本书自己的目录**，不是脚本内置的规则。目录本身也可能漏条、",
              "> OCR 错字，所以「对不上」只说明两边不一致，谁对谁错仍由人判。",
              "> 编号体系不同也会表现为对不上（正文「一、」对目录「第一节」），需人眼对。", "",
              "| 行 | 层级 | 正文标题 | 目录对应 | 比对 |", "|---:|---:|---|---|---|"]
        for h, t, how in toc_rows:
            if t is None:
                L.append(f"| {h['line']} | {h['level']} | {_cell(h['text'])} | — | {how} |")
            else:
                extra = ""
                if t.get("level") and t["level"] != h["level"]:
                    extra = f"；层级 目录 {t['level']} / 正文 {h['level']}"
                L.append(f"| {h['line']} | {h['level']} | {_cell(h['text'])} | "
                         f"{_cell(t['text'])} | {how}{extra} |")
        L.append("")
        L += ["### 目录有、正文未见", ""]
        if not toc_missing:
            L += ["（无）", ""]
        else:
            L += ["| 目录条目 | 层级 |", "|---|---:|"]
            for t in toc_missing:
                L.append(f"| {_cell(t['text'])} | {t['level'] if t['level'] else '—'} |")
            L.append("")
            L += ["> 可能是正文漏标标题、目录多列一条，或两处用字不同（归一后仍对不上）。", ""]
    return "\n".join(L)


def template(heads, sha):
    return {"_meta": {"heading_count": len(heads), "struct_sha256": sha,
                      "note": "只写要改的条目，未写的保持原样；strip/setN 必须写 reason"},
            "decisions": {}}


def load_decisions(path):
    if not os.path.isfile(path):
        return None, []
    try:
        return json.loads(read_text_cn(path)), []
    except Exception as e:
        return None, [f"裁决文件解析失败：{e}"]


def validate(heads, dec, sha):
    """只校验「裁决文件本身是否可用」：不评这本书合不合格。"""
    if not isinstance(dec, dict):
        return False, ["裁决文件顶层须为对象"]
    table = dec.get("decisions")
    if not isinstance(table, dict):
        return False, ["裁决文件缺 decisions 对象"]
    errs = []
    meta = dec.get("_meta") or {}
    if meta.get("heading_count") != len(heads):
        errs.append(f"标题数漂移：裁决记 {meta.get('heading_count')}，当前 {len(heads)}；"
                    "struct 已变，请重跑 audit 重新取 id")
    if meta.get("struct_sha256") and meta["struct_sha256"] != sha:
        errs.append("struct 指纹与裁决记录不一致：struct 已变，旧 id 可能错位；请重跑 audit")
    ids = {h["id"] for h in heads}
    for k, v in table.items():
        if k not in ids:
            errs.append(f"{k} 不在当前标题集合（行号或文本已变），请重跑 audit")
            continue
        act = (v or {}).get("action")
        if act not in ACTIONS:
            errs.append(f"{k} 动作非法：{act!r}（可选 {'/'.join(ACTIONS)}）")
        elif act != "keep" and not str((v or {}).get("reason") or "").strip():
            errs.append(f"{k} 动作 {act} 缺 reason：人工判断的判据须留痕")
    return not errs, errs


def apply_actions(lines, heads, dec):
    """只执行人写死的动作；行数不变（空行归一另走 --fix-blank）。"""
    by_id = {h["id"]: h for h in heads}
    work = list(lines)
    stats = {"strip": 0, "set": 0, "keep": 0}
    changes = []
    for _id, d in dec["decisions"].items():
        h = by_id[_id]
        i = h["line"] - 1
        m = HEAD_RE.match(work[i])
        if not m:
            raise RuntimeError(f"{_id} 行已不是标题（struct 漂移），请重跑 audit")
        text = m.group(2).strip()
        act = d["action"]
        if act == "strip":
            work[i] = text
            stats["strip"] += 1
            changes.append((h, "剥除标题标记", d.get("reason", "")))
        elif act.startswith("set"):
            lvl = int(act[3:])
            if len(m.group(1)) == lvl:
                stats["keep"] += 1
            else:
                work[i] = "#" * lvl + " " + text
                stats["set"] += 1
                changes.append((h, f"{h['level']} 级→{lvl} 级", d.get("reason", "")))
        else:
            stats["keep"] += 1
    return work, stats, changes


def verify_actions(new_lines, heads, dec):
    """落盘前核验：每个动作确实按裁决落地。"""
    by_id = {h["id"]: h for h in heads}
    errs = []
    for _id, d in dec["decisions"].items():
        i = by_id[_id]["line"] - 1
        act = d["action"]
        line = new_lines[i]
        if act == "strip":
            if line.startswith("#"):
                errs.append(f"{_id} 应已剥除标题标记，实际仍为：{line[:40]}")
        elif act.startswith("set"):
            lvl = int(act[3:])
            if not line.startswith("#" * lvl + " ") or line.startswith("#" * (lvl + 1)):
                errs.append(f"{_id} 应为 {lvl} 级，实际：{line[:40]}")
    return errs


def normalize_blank(lines):
    """标题前后空行归一为恰好 1 行；从后往前处理，避免行号漂移。"""
    out = list(lines)
    idx = [i for i, l in enumerate(out) if HEAD_RE.match(l)]
    ops = 0
    for i in reversed(idx):
        j = i
        while j > 0 and not out[j - 1].strip():
            out.pop(j - 1)
            j -= 1
            ops += 1
        if j > 0:
            out.insert(j, "")
            j += 1
            ops += 1
        k = j + 1
        while k < len(out) and not out[k].strip():
            out.pop(k)
            ops += 1
        if k < len(out):
            out.insert(k, "")
            ops += 1
    return out, ops


def disposition_md(stats, changes, blank_ops):
    L = ["", "#### （标题结构）00f 标题结构复核（源文件 work/heading_decisions.json）", "",
         f"- {datetime.now():%Y-%m-%d %H:%M}：人读标题地图后逐条判断，"
         f"改动 {len(changes)} 处（剥除标题标记 {stats['strip']}、改层级 {stats['set']}），"
         f"保留 {stats['keep']} 处。"]
    if blank_ops:
        L.append(f"- 空行归一：{blank_ops} 处（--fix-blank）。")
    if changes:
        L.append("- 逐条（行号｜原文｜处置｜判据）：")
        for h, what, why in changes:
            L.append(f"  - {h['line']}｜{h['text'][:30]}｜{what}｜{why or '（未填）'}")
    else:
        L.append("- 本次复核未改动 `fulltext_struct.txt`；重跑 assemble 仅为把本记录并入 04 §四。")
    L += ["- 判据来自人工阅读（标题形态因书而异，脚本不作判定）；"
          "属结构标注、非文字纠错，不入 corrections 台账，闸B 书账互释不受影响。", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="标题结构参考视图 + 裁决执行器（脚本只作参考）")
    ap.add_argument("--work", required=True, help="work 目录（含 fulltext_struct.txt）")
    ap.add_argument("--struct", default=None, help="结构正文路径（默认 work/fulltext_struct.txt）")
    ap.add_argument("--audit", action="store_true", help="只读出参考视图（默认行为）")
    ap.add_argument("--check", action="store_true", help="校验裁决文件本身；缺文件只提示")
    ap.add_argument("--apply", action="store_true", help="按裁决改 struct + 追加 04_dispositions.md")
    ap.add_argument("--fix-blank", action="store_true", help="标题前后空行归一为恰好 1 行")
    ap.add_argument("--toc", default=None,
                    help="目录清单（纯文本一行一条／toc_tree.json）；缺省自动探测 work/toc_tree.json")
    args = ap.parse_args()

    if sum([args.audit, args.check, args.apply]) > 1:
        print("❌ --audit / --check / --apply 互斥", file=sys.stderr)
        sys.exit(2)
    work = args.work
    struct = args.struct or os.path.join(work, "fulltext_struct.txt")
    if not os.path.isfile(struct):
        print(f"❌ 找不到 {struct}", file=sys.stderr)
        sys.exit(2)

    lines = read_text_cn(struct).split("\n")
    heads = scan(lines)
    sha = sha256_file(struct)
    dec_p = os.path.join(work, "heading_decisions.json")

    # ---- 只读态：出参考视图 ----
    if not (args.check or args.apply or args.fix_blank):
        facts = shape_facts(heads, lines)
        # 目录：显式给了就用；没给则探测 work/toc_tree.json（EPUB 路径常见）；都没有则跳过
        toc_path = args.toc or os.path.join(work, "toc_tree.json")
        toc_rows = toc_missing = None
        toc_src = None
        toc_total = None
        if os.path.isfile(toc_path):
            try:
                toc = load_toc(toc_path)
                toc_rows, toc_missing = match_toc(heads, toc)
                toc_src = toc_path.replace(os.sep, "/")
                toc_total = len(toc)
                for h, t, how in toc_rows:
                    h["toc"] = (t["text"] if t else None)
                    h["toc_match"] = how
            except Exception as e:
                print(f"[00f] ⚠ 目录读取失败（{e}），本次不做比对", file=sys.stderr)
        elif args.toc:
            print(f"[00f] ⚠ 找不到 --toc 指定的 {args.toc}，本次不做比对", file=sys.stderr)

        safe_write(os.path.join(work, "heading_map.md"),
                   report_md(struct, heads, facts, sha, toc_rows, toc_missing, toc_src, toc_total))
        safe_write(os.path.join(work, "heading_map.json"),
                   json.dumps(heads, ensure_ascii=False, indent=2) + "\n")
        safe_write(os.path.join(work, "heading_decisions.template.json"),
                   json.dumps(template(heads, sha), ensure_ascii=False, indent=2) + "\n")
        dist = {}
        for h in heads:
            dist[h["level"]] = dist.get(h["level"], 0) + 1
        toc_note = (f"；已与目录比对（{len(toc_rows)} 条对账、{len(toc_missing)} 条目录有正文无）"
                    if toc_rows is not None else "；未提供目录，未做比对")
        print(f"[00f] 参考视图已出：{len(heads)} 个标题（"
              + "、".join(f"{k} 级 {v}" for k, v in sorted(dist.items()))
              + f"），形态事实 {len(facts)} 条{toc_note}——只读，struct 未改")
        print(f"[00f] 报告：{os.path.join(work, 'heading_map.md')}")
        print("[00f] 要改的条目写进 heading_decisions.json（只写要改的，未写即保持），再跑 --apply")
        return

    dec, errs = load_decisions(dec_p)

    # ---- 校验态 ----
    if args.check:
        if dec is None:
            if errs:
                for e in errs:
                    print("❌", e, file=sys.stderr)
                sys.exit(1)
            print("ℹ 未找到裁决文件：视为全部保持原样（不阻断）。如需改动，写 " + dec_p)
            return
        ok, verrs = validate(heads, dec, sha)
        if not ok:
            for e in verrs:
                print("❌", e, file=sys.stderr)
            sys.exit(1)
        n = len(dec["decisions"])
        print(f"✅ 裁决文件可用：{n} 条明确动作，其余 {len(heads) - n} 个标题保持原样")
        return

    # ---- 执行态 ----
    if dec is None and errs:
        for e in errs:
            print("❌", e, file=sys.stderr)
        sys.exit(1)
    if dec is None:
        if not args.fix_blank:
            print("ℹ 未找到裁决文件，无可执行动作；struct 未改。"
                  "（空行归一可单独跑 --fix-blank）")
            return
        dec = {"decisions": {}}
    else:
        ok, verrs = validate(heads, dec, sha)
        if not ok:
            for e in verrs:
                print("❌", e, file=sys.stderr)
            sys.exit(1)

    new_lines, stats, changes = apply_actions(lines, heads, dec)
    verrs = verify_actions(new_lines, heads, dec)
    if verrs:
        for e in verrs:
            print("❌ 落盘前核验失败：", e, file=sys.stderr)
        sys.exit(1)

    blank_ops = 0
    if args.fix_blank:
        new_lines, blank_ops = normalize_blank(new_lines)

    if not changes and not blank_ops:
        print("ℹ 裁决全部为保留，struct 未改；未写 04 留痕（无处置可记）")
        return

    safe_write(struct, "\n".join(new_lines))
    back = read_text_cn(struct).split("\n")
    if back != new_lines:
        print("❌ 落盘后回读不一致，请检查磁盘/编码", file=sys.stderr)
        sys.exit(1)

    disp = os.path.join(work, "04_dispositions.md")
    old = read_text_cn(disp) if os.path.isfile(disp) else ""
    safe_write(disp, old.rstrip("\n") + "\n" + disposition_md(stats, changes, blank_ops))
    print(f"✅ 已写回 {struct}：剥除 {stats['strip']}、改层级 {stats['set']}、"
          f"保留 {stats['keep']}、空行归一 {blank_ops}")
    print(f"✅ 留痕已追加 {disp}（assemble 时并入 04 §四 4.4）")
    print("ℹ 下一步：重跑 pipeline_assemble.py，再跑 check_book.py 终检")


if __name__ == "__main__":
    main()
