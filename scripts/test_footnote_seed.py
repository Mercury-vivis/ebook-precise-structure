#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_footnote_seed.py —— 脚注号形态归一种子（v2.8.0）

守六件事：
  1. 种子正则只匹配非标准形态：`〔N〕` 不空转、`[1795]`（书目年）不动、`[0,1)` 不动
  2. 中文书按底本实况生成种子（phys=ALL + regex + systematic）
  3. 西文书不生成（`[N]` 是西文脚注正规形态，归一即改写语义）
  4. 幂等：种子已存在不覆盖；已标 status=deleted 尊重撤销、不重建
  5. 种子进 corrections 后能被 load_active_patches 读到，且标为正则型（闸B 正向才管得住）
  6. 端到端：施加后正文真的归一、再次施加不再变化（幂等），且 tongai 里带「〔正则型〕」

为什么这条链要单独测：归一是对正文的改动，此前写在施加器里（账外、改完查不到账）。
搬进台账后，唯一的守门人就是「种子生成 → 台账 → 施加 → 幂等」这一串；任一环断掉，
归一要么不生效（错形留在书里），要么变成新的账外改动。

运行：python scripts/test_footnote_seed.py
"""
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from _common import (  # noqa: E402
    FOOTNOTE_FORM_RX, FOOTNOTE_FORM_NEW, FOOTNOTE_SEED_FILE,
    footnote_form_stats, seed_footnote_form, apply_corrections_core, load_active_patches,
)

fails = []


def check(desc, cond):
    if not cond:
        fails.append(desc)


def _work(text):
    d = tempfile.mkdtemp(prefix="fn_seed_")
    with open(os.path.join(d, "fulltext_struct.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return d


def test_regex_judgement():
    """种子正则的四个边界：混用改写、标准形态不空转、书目年不动、区间不动。"""
    rx = re.compile(FOOTNOTE_FORM_RX)
    cases = [
        ("[12〕", "〔12〕", True), ("〔12]", "〔12〕", True), ("[12]", "〔12〕", True),
        ("［7）", "〔7〕", True), ("[3}", "〔3〕", True),
        ("〔12〕", "〔12〕", False),      # 已是标准 → 不匹配（否则全书每条脚注都算一次空转命中）
        ("[1795]", "[1795]", False),     # 4 位书目年 → 不动
        ("[0,1)", "[0,1)", False),       # 数学区间 → 不动
    ]
    for src, want, should in cases:
        got = rx.sub(FOOTNOTE_FORM_NEW, src)
        check(f"正则：{src!r} 应得 {want!r}，实得 {got!r}", got == want)
        check(f"正则：{src!r} 命中应为 {should}", bool(rx.findall(src)) is should)


def test_seed_decisions():
    """五种决策：seeded / exists / tombstoned / western_skip / clean / no_struct。"""
    # 中文书：混用形态 → 生成
    w = _work("<<<PAGE phys=1 book=1>>>\n" + "注[12〕与〔13]混排。" * 3)
    r = seed_footnote_form(w, quiet=True)
    p = os.path.join(w, "corrections", FOOTNOTE_SEED_FILE)
    check("中文书应 seeded", r["status"] == "seeded")
    check("种子文件应落盘", os.path.isfile(p))
    if os.path.isfile(p):
        d = json.load(open(p, encoding="utf-8"))
        p0 = d["patches"][0]
        check("种子应 phys=ALL", p0.get("phys") == "ALL")
        check("种子应 systematic+regex", p0.get("systematic") is True and p0.get("regex") is True)
        check("种子 old 应是降级正则原文", p0.get("old") == FOOTNOTE_FORM_RX)
    check("幂等重跑应 exists", seed_footnote_form(w, quiet=True)["status"] == "exists")
    # 标 deleted → 尊重撤销
    if os.path.isfile(p):
        d = json.load(open(p, encoding="utf-8"))
        d["patches"][0]["status"] = "deleted"
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(d, f, ensure_ascii=False)
        check("已撤销应 tombstoned", seed_footnote_form(w, quiet=True)["status"] == "tombstoned")
    shutil.rmtree(w, ignore_errors=True)

    # 西文书：不生成
    w = _work("<<<PAGE phys=1 book=1>>>\nThe year [1795] and page [136]. " * 5)
    r = seed_footnote_form(w, quiet=True)
    check("西文书应 western_skip", r["status"] == "western_skip")
    check("西文书不应落种子文件", not os.path.isfile(os.path.join(w, "corrections", FOOTNOTE_SEED_FILE)))
    shutil.rmtree(w, ignore_errors=True)

    # 全书已是标准形态：不生成（无形态不一致可纠）
    w = _work("<<<PAGE phys=1 book=1>>>\n" + "纯中文正文，注〔12〕已标准。" * 5)
    check("已全标准应 clean", seed_footnote_form(w, quiet=True)["status"] == "clean")
    shutil.rmtree(w, ignore_errors=True)

    # 无底本
    w = tempfile.mkdtemp(prefix="fn_seed_")
    check("无 struct 应 no_struct", seed_footnote_form(w, quiet=True)["status"] == "no_struct")
    shutil.rmtree(w, ignore_errors=True)


def test_stats_split():
    """清点口径：标准 / 待归一 / 4 位以上 三者互不重叠。"""
    st = footnote_form_stats("[12〕〔13][14]〔15〕[1795]")
    check("待归一只数非标准形态（3）", st["nonstd"] == 3)
    check("标准只数〔N〕（1）", st["std"] == 1)
    check("4 位以上单列（1）", st["big"] == 1)


def test_apply_e2e():
    """端到端：种子经台账施加 → 正文归一、命中进 tongai（标正则型）、再施加不再变。"""
    w = _work("<<<PAGE phys=1 book=1>>>\n" + "注[12〕与〔13]混排。" * 2)
    seed_footnote_form(w, quiet=True)
    cf = os.path.join(w, "corrections", FOOTNOTE_SEED_FILE)
    check("端到端：种子文件应存在", os.path.isfile(cf))

    # 台账侧：能被 load_active_patches 读到，且标为正则型
    act = load_active_patches(w)
    hit = [p for p in act if p.get("regex") and p.get("phys") in ("ALL", "all")]
    check("台账应读到归一种子且标 regex", bool(hit))
    check("台账不应把种子当字面量", all(p["old"] != "old" for p in hit))

    blocks = [[1, 1, "", "注[12〕与〔13]混排。"]]
    ret = apply_corrections_core(blocks, [cf], work_dir=w)
    body = blocks[0][3]
    check("施加后应归一为〔12〕", "〔12〕" in body)
    check("施加后应归一为〔13〕", "〔13〕" in body)
    check("施加后不应残留 [12〕", "[12〕" not in body)
    tongai = ret[3]
    check("tongai 应标〔正则型〕", "〔正则型〕" in tongai)

    # 幂等：同一份已归一的正文再施加一遍，不应再变
    blocks2 = [[1, 1, "", body]]
    apply_corrections_core(blocks2, [cf], work_dir=w)
    check("再次施加正文不应再变（幂等）", blocks2[0][3] == body)
    shutil.rmtree(w, ignore_errors=True)


def test_western_not_touched():
    """西文书不生成种子 → 施加链上 `[1795]` 一字不动（这是搬进台账要根治的误伤）。"""
    src = "See [1795] and [136] for details. "
    w = _work("<<<PAGE phys=1 book=1>>>\n" + src * 3)
    seed_footnote_form(w, quiet=True)
    cf = os.path.join(w, "corrections", FOOTNOTE_SEED_FILE)
    blocks = [[1, 1, "", src]]
    apply_corrections_core(blocks, [cf] if os.path.isfile(cf) else [], work_dir=w)
    check("西文书 [1795] 不应被归一", "[1795]" in blocks[0][3])
    check("西文书 [136] 不应被归一", "[136]" in blocks[0][3])
    shutil.rmtree(w, ignore_errors=True)


if __name__ == "__main__":
    test_regex_judgement()
    test_seed_decisions()
    test_stats_split()
    test_apply_e2e()
    test_western_not_touched()
    if fails:
        print(f"❌ test_footnote_seed 失败 {len(fails)} 项：")
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print("✅ test_footnote_seed 通过（正则边界 / 五决策 / 清点口径 / 施加端到端 / 西文不误伤）")
