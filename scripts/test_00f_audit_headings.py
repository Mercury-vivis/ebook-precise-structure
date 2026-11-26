#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_00f_audit_headings.py —— 标题结构参考视图 + 裁决执行器（00f）判据回归

本脚本的定位是「只作参考、判断归人」，回归重点因此不是「报得准不准」，
而是「不越权、不预填、不判定、不丢信息」：

  1. scan：全量标题都进地图（合法形态与怪形态一视同仁），不筛不排；
  2. 上下文：prev/next 跳过空行与页锚，取真实相邻正文；
  3. id：同文本同行稳定，行号变即变（漂移时旧裁决自然失效）；
  4. 报告：只出事实，不出现「候选/误标/疑似/建议」等判定词；
  5. 模板：decisions 为空，不预填任何动作（防照抄即误删）；
  6. 部分覆盖：只写要改的，其余保持原样；
  7. 校验：动作非法 / strip 缺 reason / 标题数漂移 / 指纹漂移 一律拒；
  8. apply：strip 与 setN 落地，落盘前自核验；
  9. CLI：缺裁决文件不阻断（返回 0 且提示），有错才返回 1；
 10. --fix-blank：独立开关，默认不动空行；
 11. 04 留痕：改动项与人工判据都进 work/04_dispositions.md。
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "audit_headings", os.path.join(HERE, "00f_audit_headings.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = load_mod()

# 故意混装：合法卷序目标题 + 怪形态标题，验证「一视同仁全进地图」
SAMPLE = [
    "<<<PAGE phys=1 book=1>>>",
    "# 元和郡縣圖志序",
    "序正文。",
    "## 墾田",
    "墾田正文。",
    "<<<PAGE phys=2 book=2>>>",
    "# 元和郡縣圖志卷第一",
    "## 卷一關內道一",
    "正文。",
    "## 本此。",
    "残句正文。",
]

FORBIDDEN = ("候选", "误标", "疑似", "建议", "宁多勿漏", "可疑")


def write(p, text):
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def t_scan_all():
    heads = M.scan(SAMPLE)
    texts = [h["text"] for h in heads]
    assert texts == ["元和郡縣圖志序", "墾田", "元和郡縣圖志卷第一", "卷一關內道一", "本此。"], texts
    assert [h["level"] for h in heads] == [1, 2, 1, 2, 2]
    assert [h["phys"] for h in heads] == [1, 1, 2, 2, 2]
    assert len({h["id"] for h in heads}) == len(heads)
    print("✅ t_scan_all：全量标题进地图，合法与怪形态一视同仁")


def t_context():
    heads = M.scan(SAMPLE)
    first = heads[0]
    assert first["prev"] == "（页首）", first     # 上方是页锚，不跨页去抓上一页正文
    assert first["next"] == "序正文。", first     # 跳过空行取正文
    k = heads[1]
    assert k["prev"] == "序正文。" and k["next"] == "墾田正文。", k
    juan = heads[2]
    assert juan["prev"] == "（页首）", juan
    assert juan["next"] == "（紧邻标题：卷一關內道一）", juan
    print("✅ t_context：上文/下文标出页首页末与紧邻标题，不跨页抓正文")


def t_facts_shape_only():
    """形态事实只讲标题自身的形状：不混排版空行，避免刷屏淹掉有用信息。"""
    facts = M.shape_facts(M.scan(SAMPLE), SAMPLE)
    whys = [w for _, w in facts]
    assert not any("空行" in w for w in whys), whys
    assert any("以句读" in w for w in whys), whys       # 「本此。」应报
    assert any("仅 2 字" in w for w in whys), whys       # 「墾田」应报
    print("✅ t_facts_shape_only：形态事实只描述标题形状，不含排版空行")


def t_id():
    assert M.hid(1, "abc") == M.hid(1, "abc")
    assert M.hid(1, "abc") != M.hid(2, "abc")
    assert M.hid(1, "abc") != M.hid(1, "abd")
    print("✅ t_id：id 随行号/文本变化，漂移即失效")


def t_report_no_verdict():
    heads = M.scan(SAMPLE)
    facts = M.shape_facts(heads, SAMPLE)
    md = M.report_md("work/fulltext_struct.txt", heads, facts, "a" * 64)
    for w in FORBIDDEN:
        assert w not in md, f"报告出现判定词「{w}」"
    assert "只呈现事实" in md and "不判断对错" in md
    assert "形态事实" in md
    print("✅ t_report_no_verdict：报告只出事实，无判定词")


def t_template_empty():
    heads = M.scan(SAMPLE)
    tpl = M.template(heads, "b" * 64)
    assert tpl["decisions"] == {}, "模板不得预填任何动作"
    assert tpl["_meta"]["heading_count"] == len(heads)
    assert tpl["_meta"]["struct_sha256"] == "b" * 64
    print("✅ t_template_empty：模板 decisions 为空，不预填动作")


def t_partial_coverage():
    heads = M.scan(SAMPLE)
    target = [h for h in heads if h["text"] == "本此。"][0]
    dec = {"_meta": {"heading_count": len(heads), "struct_sha256": "x"},
           "decisions": {target["id"]: {"action": "strip", "reason": "残句，非标题"}}}
    ok, errs = M.validate(heads, dec, "x")
    assert ok, errs
    new, stats, changes = M.apply_actions(SAMPLE, heads, dec)
    joined = "\n".join(new)
    assert "## 本此。" not in joined and "\n本此。\n" in joined
    assert "# 元和郡縣圖志卷第一" in joined, "未写裁决的标题必须原样保留"
    assert stats["strip"] == 1 and len(changes) == 1
    print("✅ t_partial_coverage：只执行写明的条目，其余保持原样")


def t_validate_gates():
    heads = M.scan(SAMPLE)
    base = {"_meta": {"heading_count": len(heads), "struct_sha256": "z"},
            "decisions": {}}
    assert M.validate(heads, base, "z")[0]
    bad = json.loads(json.dumps(base))
    bad["decisions"]["99:deadbeef"] = {"action": "strip", "reason": "x"}
    assert not M.validate(heads, bad, "z")[0], "不存在的 id 必须拒"
    bad = json.loads(json.dumps(base))
    bad["decisions"][heads[0]["id"]] = {"action": "set9", "reason": "x"}
    assert not M.validate(heads, bad, "z")[0], "非法动作必须拒"
    bad = json.loads(json.dumps(base))
    bad["decisions"][heads[0]["id"]] = {"action": "strip"}
    ok, errs = M.validate(heads, bad, "z")
    assert not ok and any("reason" in e for e in errs), errs
    bad = json.loads(json.dumps(base))
    bad["decisions"][heads[0]["id"]] = {"action": "keep"}   # keep 不要求 reason
    assert M.validate(heads, bad, "z")[0]
    bad = json.loads(json.dumps(base))
    bad["_meta"]["heading_count"] = 999
    assert not M.validate(heads, bad, "z")[0], "标题数漂移必须拒"
    assert not M.validate(heads, base, "different")[0], "指纹漂移必须拒"
    print("✅ t_validate_gates：id/动作/reason/数量/指纹五道校验")


def t_apply_setlevel_and_verify():
    heads = M.scan(SAMPLE)
    k = [h for h in heads if h["text"] == "墾田"][0]
    dec = {"_meta": {"heading_count": len(heads), "struct_sha256": "z"},
           "decisions": {k["id"]: {"action": "set3", "reason": "应为三级"}}}
    new, stats, changes = M.apply_actions(SAMPLE, heads, dec)
    assert "### 墾田" in new and stats["set"] == 1, stats
    assert not M.verify_actions(new, heads, dec), "落盘前核验应通过"
    broken = list(new)
    broken[k["line"] - 1] = "墾田"       # 人为破坏，模拟写盘失手
    assert M.verify_actions(broken, heads, dec), "核验必须抓出未落地"
    print("✅ t_apply_setlevel_and_verify：setN 落地 + 落盘前核验")


def t_fix_blank_opt_in():
    lines = ["正文上一段。", "# 章标题", "正文下一段。"]
    out, ops = M.normalize_blank(lines)
    assert out == ["正文上一段。", "", "# 章标题", "", "正文下一段。"], out
    assert ops > 0
    heads = M.scan(SAMPLE)
    dec = {"_meta": {"heading_count": len(heads), "struct_sha256": "z"}, "decisions": {}}
    same, _, _ = M.apply_actions(SAMPLE, heads, dec)
    assert same == SAMPLE, "--apply 不带 --fix-blank 时不得动空行"
    print("✅ t_fix_blank_opt_in：空行归一是独立开关，默认不动")


def t_norm():
    assert M.norm("卷一 關內道一") == "卷一關內道一"
    assert M.norm("墾田……12") == "墾田"
    assert M.norm("「京兆府」") == "京兆府"
    assert M.norm("州境：") == "州境"
    print("✅ t_norm：空白/标点/点线/尾部页码归一")


TOC_TEXT = """# 元和郡縣圖志序
## 墾田
# 元和郡縣圖志卷第一
## 卷一 關內道一
## 京兆府
"""


def t_load_toc_text():
    tmp = tempfile.mkdtemp(prefix="00f_toc_")
    try:
        p = os.path.join(tmp, "toc.txt")
        write(p, TOC_TEXT)
        toc = M.load_toc(p)
        assert [t["text"] for t in toc] == ["元和郡縣圖志序", "墾田", "元和郡縣圖志卷第一",
                                            "卷一 關內道一", "京兆府"], toc
        assert [t["level"] for t in toc] == [1, 2, 1, 2, 2], toc
        print("✅ t_load_toc_text：纯文本目录（# 前缀表层级）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_load_toc_json():
    tmp = tempfile.mkdtemp(prefix="00f_tocj_")
    try:
        p = os.path.join(tmp, "toc_tree.json")
        write(p, json.dumps({"sections": [{"title": "卷一"}, {"title": "墾田", "level": 2}]},
                            ensure_ascii=False))
        toc = M.load_toc(p)
        assert [t["text"] for t in toc] == ["卷一", "墾田"], toc
        assert [t["level"] for t in toc] == [None, 2], toc
        print("✅ t_load_toc_json：toc_tree.json 可直接当目录用")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_match_toc():
    heads = M.scan(SAMPLE)
    toc = [
        {"text": "元和郡縣圖志序", "level": 1},
        {"text": "墾田", "level": 1},            # 层级与正文（2 级）不一致
        {"text": "元和郡縣圖志卷第一", "level": 1},
        {"text": "卷一 關內道一", "level": 2},
        {"text": "京兆府", "level": 2},          # 目录有、正文无
    ]
    rows, missing = M.match_toc(heads, toc)
    by_text = {h["text"]: (t, how) for h, t, how in rows}
    assert by_text["元和郡縣圖志序"][1] == "一致"
    assert by_text["卷一關內道一"][1] == "一致", by_text["卷一關內道一"]   # 空格归一后相等
    assert by_text["本此。"][1] == "目录无此条"
    assert by_text["墾田"][0]["level"] == 1, "应带回目录层级供人比对"
    assert [t["text"] for t in missing] == ["京兆府"], missing
    print("✅ t_match_toc：一致/归一后一致/目录无此条/目录有正文无")


def t_report_toc_section():
    heads = M.scan(SAMPLE)
    toc = [{"text": "墾田", "level": 1}, {"text": "京兆府", "level": 2}]
    rows, missing = M.match_toc(heads, toc)
    md = M.report_md("w/fulltext_struct.txt", heads, [], "a" * 64, rows, missing, "w/toc.txt")
    assert "## 三、与目录比对" in md
    assert "目录无此条" in md and "目录有、正文未见" in md and "京兆府" in md
    assert "层级 目录 1 / 正文 2" in md, md
    md2 = M.report_md("w/fulltext_struct.txt", heads, [], "a" * 64)
    assert "未提供目录" in md2
    print("✅ t_report_toc_section：目录比对表 + 层级差异 + 反向清单 + 未提供时如实说")


def t_cli_toc():
    tmp = tempfile.mkdtemp(prefix="00f_clitoc_")
    try:
        work = os.path.join(tmp, "work")
        os.makedirs(work)
        write(os.path.join(work, "fulltext_struct.txt"), "\n".join(SAMPLE) + "\n")
        script = os.path.join(HERE, "00f_audit_headings.py")

        def run(*args):
            return subprocess.run([sys.executable, script, "--work", work, *args],
                                  capture_output=True, text=True, encoding="utf-8")

        # 自动探测 work/toc_tree.json
        write(os.path.join(work, "toc_tree.json"),
              json.dumps({"sections": [{"title": "墾田"}]}, ensure_ascii=False))
        r = run()
        assert r.returncode == 0, r.stderr
        assert "已与目录比对" in r.stdout, r.stdout
        md = open(os.path.join(work, "heading_map.md"), encoding="utf-8").read()
        assert "toc_tree.json" in md and "京兆府" not in md

        # 显式 --toc 覆盖
        p = os.path.join(tmp, "toc.txt")
        write(p, TOC_TEXT)
        r = run("--toc", p)
        assert r.returncode == 0, r.stderr
        md = open(os.path.join(work, "heading_map.md"), encoding="utf-8").read()
        assert "京兆府" in md, "显式 --toc 应生效"
        js = json.loads(open(os.path.join(work, "heading_map.json"), encoding="utf-8").read())
        assert any(h.get("toc_match") == "目录无此条" for h in js), js
        print("✅ t_cli_toc：自动探测 toc_tree.json / --toc 显式指定")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_cli():
    tmp = tempfile.mkdtemp(prefix="00f_test_")
    try:
        work = os.path.join(tmp, "work")
        os.makedirs(work)
        struct = os.path.join(work, "fulltext_struct.txt")
        write(struct, "\n".join(SAMPLE) + "\n")
        script = os.path.join(HERE, "00f_audit_headings.py")

        def run(*args):
            return subprocess.run([sys.executable, script, "--work", work, *args],
                                  capture_output=True, text=True, encoding="utf-8")

        before = sha(struct)
        r = run()
        assert r.returncode == 0, r.stderr
        assert sha(struct) == before, "只读态不得写 struct"
        for f in ("heading_map.md", "heading_map.json", "heading_decisions.template.json"):
            assert os.path.isfile(os.path.join(work, f)), f
        assert not os.path.exists(os.path.join(work, "heading_decisions.json"))

        # 缺裁决文件：不阻断（用户裁定「不拦截，只提示」）
        r = run("--check")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert "未找到裁决文件" in (r.stdout + r.stderr)

        # 空裁决 = 全部保留，不改 struct
        tpl = json.loads(open(os.path.join(work, "heading_decisions.template.json"),
                              encoding="utf-8").read())
        write(os.path.join(work, "heading_decisions.json"),
              json.dumps(tpl, ensure_ascii=False, indent=2))
        r = run("--apply")
        assert r.returncode == 0, r.stderr
        assert sha(struct) == before, "空裁决不得改 struct"

        # 写一条 strip（带 reason）→ 落地 + 04 留痕
        heads = M.scan(SAMPLE)
        target = [h for h in heads if h["text"] == "本此。"][0]
        tpl["decisions"][target["id"]] = {"action": "strip", "reason": "残句，非标题"}
        write(os.path.join(work, "heading_decisions.json"),
              json.dumps(tpl, ensure_ascii=False, indent=2))
        r = run("--check")
        assert r.returncode == 0, (r.returncode, r.stderr)
        r = run("--apply")
        assert r.returncode == 0, r.stderr
        assert sha(struct) != before
        body = open(struct, encoding="utf-8").read()
        assert "## 本此。" not in body and "\n本此。\n" in body, body
        disp = os.path.join(work, "04_dispositions.md")
        txt = open(disp, encoding="utf-8").read()
        assert "00f" in txt and "残句，非标题" in txt, txt
        print("✅ t_cli：只读不写盘 / 缺文件不阻断 / 空裁决不改 / 有条目才落地并留痕")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    t_scan_all()
    t_context()
    t_facts_shape_only()
    t_id()
    t_report_no_verdict()
    t_template_empty()
    t_partial_coverage()
    t_validate_gates()
    t_apply_setlevel_and_verify()
    t_fix_blank_opt_in()
    t_norm()
    t_load_toc_text()
    t_load_toc_json()
    t_match_toc()
    t_report_toc_section()
    t_cli_toc()
    t_cli()
    print("✅ 00f 全部测试通过")
