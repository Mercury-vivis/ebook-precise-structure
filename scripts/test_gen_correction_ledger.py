#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_gen_correction_ledger.py —— 04 §四 4.4 处置登记自动并入（v2.2.0 · S4）

守四件事（2026-10-22《西周铜器断代》实测教训：04 全自动生成、无手工注入钩子，
手工贴补每轮被 assemble 冲掉（双源漂移）+ 文本模式写盘把全文转 CRLF + 描述词形
被卫生检查擒（自指），一轮 5 次手工补救）：
  1. work/04_dispositions.md 存在 → 整体并入 04 §四作为 4.4，正文一字不差；
  2. 并入走 safe_write：全文件 LF、无 CRLF；
  3. 文件不存在 → 04 记「无」并提示可直接记于该文件，不报错；
  4. §四豁免双向：4.4 里的描述性词形（占位符类）与「原文→改后」裁定行不参与
     check_book 卫生检查与人工修订解析；4.1–4.3 照常参与。

运行：python scripts/test_gen_correction_ledger.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
GEN = os.path.join(HERE, "gen_correction_ledger.py")
fails = []


def check(desc, cond):
    if not cond:
        fails.append(desc)


def build(tmp):
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "out")
    os.makedirs(os.path.join(work, "corrections"))
    os.makedirs(out, exist_ok=True)
    with io.open(os.path.join(work, "fulltext_struct.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("<<<PAGE phys=1 book=1>>>\n他日自已当知此事也\n")
    with io.open(os.path.join(work, "corrections", "00z_fixup.json"), "w",
                 encoding="utf-8", newline="\n") as f:
        json.dump([{"phys": 1, "old": "自已当知此事", "new": "自己当知此事", "reason": "形近"}],
                  f, ensure_ascii=False)
    with io.open(os.path.join(work, "book_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"book": "测试书台账"}, f, ensure_ascii=False)
    with io.open(os.path.join(out, "书03_带页码全文.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("<<<PAGE phys=1 book=1>>>\n他日自己当知此事也\n")
    return work, out


def run_ledger(work, out):
    r = subprocess.run([PY, GEN, "--work", work, "--out", out,
                        "--meta", os.path.join(work, "book_meta.json"), "--prefix", "书"],
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def p04(out):
    return os.path.join(out, "书04_改错留痕.md")


def test_absent_source():
    """无 04_dispositions.md：04 正常生成，4.4 记「无」并给指引，不报错"""
    tmp = tempfile.mkdtemp(prefix="gcl_absent_")
    work, out = build(tmp)
    rc, o = run_ledger(work, out)
    check("无源文件也应 exit 0", rc == 0)
    txt = io.open(p04(out), encoding="utf-8").read()
    check("04 含 4.4 标题", "### 4.4 考据裁定与预处理处置登记" in txt)
    check("4.4 记「无」并指引源文件", "work/04_dispositions.md 不存在" in txt)
    print("✅ test_absent_source: 无源文件时 4.4 记「无」+ 指引，不报错")


def test_merge_and_lf():
    """有源文件：整体并入 4.4、正文一字不差、LF 无 CRLF"""
    tmp = tempfile.mkdtemp(prefix="gcl_merge_")
    work, out = build(tmp)
    disp = ("#### A. 考据裁定\n\n"
            "- phys=1 「自已当知此事」→「自己当知此事」（裁定记录：OCR 占位符描述不参与解析）\n"
            "- 竖排表压平待重建：imgs/p1_table_1.jpg\n")
    with io.open(os.path.join(work, "04_dispositions.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(disp)
    rc, o = run_ledger(work, out)
    check("有源文件 exit 0", rc == 0)
    raw = open(p04(out), "rb").read()
    txt = raw.decode("utf-8")
    check("4.4 在 4.3 之后、§五 之前",
          txt.find("### 4.3") < txt.find("### 4.4") and "## 五、" not in txt)
    check("源文件正文整体并入", "#### A. 考据裁定" in txt and "竖排表压平待重建" in txt)
    check("源文件逐行在（一字不差）", all(l in txt for l in disp.splitlines()))
    check("全文件 LF（无 CRLF）", b"\r\n" not in raw)
    print("✅ test_merge_and_lf: 源文件整体并入 4.4、LF、正文不差")


def test_section4_exemption():
    """§四豁免双向：4.4 的词形与裁定行不被卫生检查/人工修订解析擒获；4.2 照常解析"""
    sys.path.insert(0, HERE)
    import check_book as cb  # noqa: E402
    tmp = tempfile.mkdtemp(prefix="gcl_exempt_")
    out = os.path.join(tmp, "out")
    os.makedirs(out)
    p = p04(out)
    # 4.2 一条真人工修订（应被解析）；4.4 含未修项的「原文→改后」裁定行 +
    # 对检查器脚手架形态的描述性引用（【占位符】——收窄后仍需 §四豁免护住描述性引用）
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        "## 四、撤销与人工修订\n\n"
        "### 4.2 人工定点修订\n\n"
        "- phys=1 ｜ 原文「自已当知此事」→ 改为「自己当知此事」｜ 理由：形近\n\n"
        "### 4.4 考据裁定与预处理处置登记（源文件 work/04_dispositions.md）\n\n"
        "- phys=2 ｜ 原文「甲甲」→ 改为「乙乙」（裁定不修：合法文本）\n"
        "- 说明：误报分析——旧词表连「OCR 占位符」这类描述性行文都擒；收窄后需脚手架形态"
        "如【占位符】才命中，4.4 对该形态的描述性引用靠 §四豁免护住\n")
    revs = cb.load_manual_revisions(out, "书")
    check("4.2 人工修订被解析 1 条", len(revs) == 1 and revs[0]["old"] == "自已当知此事")
    check("4.4 裁定行不被解析", all(r["old"] != "甲甲" for r in revs))
    iss_all = cb.check_file_hygiene(p, exempt_placeholder_section=False)
    iss_ex = cb.check_file_hygiene(p, exempt_placeholder_section=True)
    check("不豁免时 4.4 词形被擒（证明豁免是必要的）",
          any("占位符" in s for s in iss_all))
    check("豁免后不再报占位符", not any("占位符" in s for s in iss_ex))
    print("✅ test_section4_exemption: 4.4 豁免双向（4.2 照解析 / 词形不误伤）")


def test_exemption_keeps_crlf():
    """豁免只免占位符：CRLF 在 04 仍照报"""
    sys.path.insert(0, HERE)
    import check_book as cb  # noqa: E402
    tmp = tempfile.mkdtemp(prefix="gcl_crlf_")
    p = os.path.join(tmp, "书04_改错留痕.md")
    with open(p, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("## 四、撤销与人工修订\n\n### 4.4 登记\n- 占位符 描述\n")
    iss = cb.check_file_hygiene(p, exempt_placeholder_section=True)
    check("CRLF 仍被报（豁免不收窄）", any("CRLF" in s for s in iss))
    check("占位符不报（§四豁免）", not any("占位符" in s for s in iss))
    print("✅ test_exemption_keeps_crlf: 豁免只针对占位符，CRLF 照报")


if __name__ == "__main__":
    test_absent_source()
    test_merge_and_lf()
    test_section4_exemption()
    test_exemption_keeps_crlf()
    if fails:
        print(f"❌ test_gen_correction_ledger 失败 {len(fails)} 项：")
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print("✅ test_gen_correction_ledger 通过")
