"""回归测试：04_check_citations.py 自动模式。

口径切换（2026-10-19）：--auto 默认已改为 C 口径（只核「关键原句摘录」小节）。
本文件守的是 **--all-quotes 旧口径**（全篇引号句）的三档判定与并集比对源，
故每个用例都显式传 --all-quotes；C 口径的行为由 test_citation_verbatim.py 守。

旧文档：04_check_citations.py 自动模式（v1.28.0 新增 · P0）。

验证：
- 从 notes 自动抽取引号引句（三种引号族），无需人工备 --checks 清单；
- 命中 / [未能验证] 判定正确，且比对源取并集（纠正后正字不假未命中）；
- 报告逐条标 [未能验证]；
- --strict 语义正确；手工模式未被破坏（仍要求 --checks）。

运行：python scripts/test_citations_auto.py
"""
import os
import sys
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 文件名以数字开头（04_），不能作为模块名直接 import，按文件路径加载
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "check_citations", os.path.join(HERE, "04_check_citations.py"))
_cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cc)
extract_citations = _cc.extract_citations


def _mk(tmp, name, text):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return p


def test_extract_quotes():
    """三种引号族都能抽出，且去重、达最短长度门槛。"""
    d = tempfile.mkdtemp(prefix="cit_")
    notes = os.path.join(d, "notes")
    os.makedirs(notes)
    with open(os.path.join(notes, "01.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("作者说：「兵者国之大事，死生之地也」\n")
        fh.write("又云：『凡用兵之法，全国为上』\n")
        fh.write('另载："将在外君命有所不受"\n')
        fh.write("「短句」\n")  # 长度不足，应被过滤
    got = extract_citations(notes, min_len=6)
    frags = [g[2] for g in got]
    assert "兵者国之大事，死生之地也" in frags, f"FAIL: 全角引号未抽出 {frags}"
    assert "凡用兵之法，全国为上" in frags, f"FAIL: 双角引号未抽出 {frags}"
    assert "将在外君命有所不受" in frags, f"FAIL: 半角引号未抽出 {frags}"
    assert "短句" not in frags, f"FAIL: 未过最短长度门槛 {frags}"
    # 去重
    assert len(frags) == len(set(frags)), "FAIL: 引句未去重"
    print("✅ test_extract_quotes: 三种引号族抽取 + 长度门槛 + 去重")


def test_auto_hit_and_miss():
    """命中与未命中判定正确；报告逐条标 [未能验证]。"""
    d = tempfile.mkdtemp(prefix="cit_")
    work = os.path.join(d, "work")
    notes = os.path.join(work, "notes")
    os.makedirs(notes)
    with open(os.path.join(notes, "01.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("原文作「兵者国之大事，不可不察也」\n")   # 底本标点不同 → 标点差异档命中
        fh.write("另载「凡战者以正合以奇胜」\n")           # 与底本逐字一致 → 精确档命中
        fh.write("又云「此句为凭空编造之语全书绝无」\n")     # 底本绝无 → 未能验证
    raw = _mk(d, "fulltext_raw.txt",
              "<<<PAGE phys=1>>>\n兵者，国之大事，不可不察也。\n"
              "<<<PAGE phys=2>>>\n凡战者以正合以奇胜。\n")
    out = os.path.join(work, "citation_report.md")
    r = subprocess.run([sys.executable, os.path.join(HERE, "04_check_citations.py"),
                        "--auto", "--all-quotes", "--work", work, "--raw", raw, "--out", out, "--min-len", "6"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"FAIL: 退出码 {r.returncode}\n{r.stderr}\n{r.stdout}"
    assert "抽到引句 3 条" in r.stdout, f"FAIL: 引句数不对\n{r.stdout}"
    assert "精确命中 1" in r.stdout, f"FAIL: 精确档应为 1\n{r.stdout}"
    assert "标点差异命中 1" in r.stdout, f"FAIL: 标点档应为 1（OCR 标点差异不该误杀）\n{r.stdout}"
    assert "未能验证 1" in r.stdout, f"FAIL: 未验证应为 1\n{r.stdout}"
    body = open(out, encoding="utf-8").read()
    assert "[未能验证]" in body, "FAIL: 报告未逐条标 [未能验证]"
    assert "✅ 命中" in body, "FAIL: 报告未标命中项"
    assert "⚠ 命中（标点差异）" in body, "FAIL: 标点差异命中未单独标注"
    print("✅ test_auto_hit_and_miss: 精确/标点差异/未验证三档判定正确，报告逐条标注")


def test_union_basis_corrected_form():
    """笔记引纠正后正字 → 只在 raw 会假未命中，加 --clean 后应命中（并集口径）。"""
    d = tempfile.mkdtemp(prefix="cit_")
    work = os.path.join(d, "work")
    notes = os.path.join(work, "notes")
    os.makedirs(notes)
    with open(os.path.join(notes, "01.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("原文作「荀彧进言于太祖之前」\n")
    raw = _mk(d, "fulltext_raw.txt", "<<<PAGE phys=1>>>\n荀或进言于太祖之前。\n")   # OCR 误：或
    clean = _mk(d, "03_clean.txt", "荀彧进言于太祖之前。\n")                        # 已纠正
    # 只给 raw → 应未命中
    r1 = subprocess.run([sys.executable, os.path.join(HERE, "04_check_citations.py"),
                         "--auto", "--all-quotes", "--work", work, "--raw", raw, "--min-len", "6"],
                        capture_output=True, text=True, encoding="utf-8")
    assert "未能验证 1" in r1.stdout, f"FAIL: 仅 raw 时应未命中\n{r1.stdout}"
    # 加 clean → 应命中（并集）
    r2 = subprocess.run([sys.executable, os.path.join(HERE, "04_check_citations.py"),
                         "--auto", "--all-quotes", "--work", work, "--raw", raw, "--clean", clean, "--min-len", "6"],
                        capture_output=True, text=True, encoding="utf-8")
    assert "命中 1" in r2.stdout and "未能验证 0" in r2.stdout, f"FAIL: 并集后应命中\n{r2.stdout}"
    print("✅ test_union_basis_corrected_form: 并集口径避免纠正后正字假未命中")


def test_strict_and_manual_mode_intact():
    """--strict 退出 1；手工模式缺 --checks 明确报错（原契约未被破坏）。"""
    d = tempfile.mkdtemp(prefix="cit_")
    work = os.path.join(d, "work")
    notes = os.path.join(work, "notes")
    os.makedirs(notes)
    with open(os.path.join(notes, "01.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("原文作「此句全书绝无仅有的编造」\n")
    raw = _mk(d, "fulltext_raw.txt", "<<<PAGE phys=1>>>\n无关正文。\n")
    r = subprocess.run([sys.executable, os.path.join(HERE, "04_check_citations.py"),
                        "--auto", "--all-quotes", "--work", work, "--raw", raw, "--min-len", "6", "--strict"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1, f"FAIL: --strict 应退出 1，实际 {r.returncode}"

    r2 = subprocess.run([sys.executable, os.path.join(HERE, "04_check_citations.py"), "--raw", raw],
                        capture_output=True, text=True, encoding="utf-8")
    assert r2.returncode == 2, f"FAIL: 手工模式缺 --checks 应退出 2，实际 {r2.returncode}"
    print("✅ test_strict_and_manual_mode_intact: --strict 生效且手工模式契约完好")



def test_section_mode_cli():
    """默认 --auto 走 C 口径：只核「关键原句摘录」小节，其他段的引号不核。"""
    d = tempfile.mkdtemp(prefix="cit_")
    work = os.path.join(d, "work")
    notes = os.path.join(work, "notes")
    os.makedirs(notes)
    with open(os.path.join(notes, "01.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("## 1. 甲章\n\n**核心概念**：\n- “自造框架名不是引文不该被核”（phys 1）\n\n"
                 "**关键原句摘录**：\n> “兵者国之大事不可不察也”（原书页 1）\n")
    clean = _mk(d, "03_clean.txt", "<<<PAGE phys=1>>>\n兵者，国之大事，不可不察也。\n")
    out = os.path.join(work, "citation_report.md")
    r = subprocess.run([sys.executable, os.path.join(HERE, "04_check_citations.py"),
                        "--auto", "--work", work, "--clean", clean, "--out", out],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"FAIL: 退出码 {r.returncode}\n{r.stderr}\n{r.stdout}"
    assert "C 口径" in r.stdout, f"FAIL: 默认应走 C 口径\n{r.stdout}"
    assert "待核 1 条" in r.stdout, f"FAIL: 只应抽到摘录小节 1 条\n{r.stdout}"
    assert "✅ 03 逐字 1" in r.stdout, f"FAIL: 该条应命中\n{r.stdout}"
    body = open(out, encoding="utf-8").read()
    assert "自造框架名" not in body, "FAIL: 非摘录小节的引号不应进报告"
    print("✅ test_section_mode_cli: 默认 C 口径，只核摘录小节")

if __name__ == "__main__":
    test_extract_quotes()
    test_auto_hit_and_miss()
    test_union_basis_corrected_form()
    test_strict_and_manual_mode_intact()
    test_section_mode_cli()
    print("\n✅ test_citations_auto 全部通过。")
