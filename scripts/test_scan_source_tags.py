"""回归测试：scan_source_tags.py 陈述来源标签扫描（v1.28.0 新增 · P0）。

验证：
- 四类标签（A/B/C/D）正确计数；
- 「疑似推断未标」能抓出含推断词却无 C/D 标签的行；
- 原文直引行（引号整句包裹）里作者自己用「可能」不算推断，不误报；
- 已标注行不重复告警；报告落盘且含统计表。

运行：python scripts/test_scan_source_tags.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scan_source_tags import scan_file, TAG_RE


def _tmpdir():
    return tempfile.mkdtemp(prefix="srtest_")


def test_tag_counting():
    """四类标签分别计数，不串档。"""
    d = _tmpdir()
    p = os.path.join(d, "01.md")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("作者认为兵民合一 〔A 原意〕\n")
        fh.write("全书共十二卷 〔B 事实〕\n")
        fh.write("此处当为传抄之误 〔C 推断〕\n")
        fh.write("该年份未见他书记载 〔D 未验证〕\n")
        fh.write("又一次原意标注 〔A 原意〕\n")
    counts, suspects = scan_file(p)
    assert counts == {"A": 2, "B": 1, "C": 1, "D": 1}, f"FAIL: 计数错误 {counts}"
    assert suspects == [], f"FAIL: 已标注行不应告警 {suspects}"
    print("✅ test_tag_counting: 四类标签计数正确、已标注行不复告警")


def test_suspect_detection():
    """含推断词且无标签的行必须被抓出。"""
    d = _tmpdir()
    p = os.path.join(d, "02.md")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("这一制度大概沿袭自唐代前期的府兵旧制\n")
        fh.write("此处可能是作者笔误造成的年代错位问题\n")
    counts, suspects = scan_file(p)
    assert len(suspects) == 2, f"FAIL: 应抓到 2 处疑似未标，实际 {suspects}"
    assert counts == {"A": 0, "B": 0, "C": 0, "D": 0}
    print("✅ test_suspect_detection: 疑似推断未标被抓出（含行号）")


def test_quote_line_not_suspect():
    """原文直引行里作者自己用「可能」→ 不判为推断（防误报）。"""
    d = _tmpdir()
    p = os.path.join(d, "03.md")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("「此事可能出于权宜之计，非长久之策」\n")
        fh.write("> 引文块：大约当时兵力不足以支撑两线作战\n")
    counts, suspects = scan_file(p)
    assert suspects == [], f"FAIL: 引号整句/引用块不应误报 {suspects}"
    print("✅ test_quote_line_not_suspect: 原文直引与引用块不误判为推断")


def test_report_generated():
    """主流程跑通：报告落盘且含统计表与判级指引。"""
    import subprocess
    d = _tmpdir()
    work = os.path.join(d, "work")
    notes = os.path.join(work, "notes")
    os.makedirs(notes)
    with open(os.path.join(notes, "01.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("作者明确指出兵农分立 〔A 原意〕\n")
        fh.write("此处大概沿袭了前朝旧制\n")
    r = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan_source_tags.py"),
         "--work", work],
        capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"FAIL: 退出码 {r.returncode}\n{r.stderr}"
    assert "疑似推断未标：1 处" in r.stdout, f"FAIL: 未报疑似数\n{r.stdout}"
    rep = os.path.join(work, "source_tag_report.md")
    assert os.path.isfile(rep), "FAIL: 报告未落盘"
    body = open(rep, encoding="utf-8").read()
    assert "〔A 原意〕" in body and "疑似推断未标" in body, "FAIL: 报告内容缺失"
    print("✅ test_report_generated: 报告落盘且含统计与判级指引")


def test_strict_exit():
    """--strict 下存在疑似未标 → 退出 1；无问题时退出 0。"""
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan_source_tags.py")
    d = _tmpdir()

    bad = os.path.join(d, "bad")
    os.makedirs(os.path.join(bad, "notes"))
    with open(os.path.join(bad, "notes", "01.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("此处可能是后人窜入的文字内容\n")
    r1 = subprocess.run([sys.executable, script, "--work", bad, "--strict"],
                        capture_output=True, text=True, encoding="utf-8")
    assert r1.returncode == 1, f"FAIL: --strict 应退出 1，实际 {r1.returncode}"

    good = os.path.join(d, "good")
    os.makedirs(os.path.join(good, "notes"))
    with open(os.path.join(good, "notes", "01.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("此处可能是后人窜入 〔C 推断〕\n")
    r2 = subprocess.run([sys.executable, script, "--work", good, "--strict"],
                        capture_output=True, text=True, encoding="utf-8")
    assert r2.returncode == 0, f"FAIL: 已标注时 --strict 应退出 0，实际 {r2.returncode}"
    print("✅ test_strict_exit: --strict 仅在疑似未标时退出 1")


if __name__ == "__main__":
    test_tag_counting()
    test_suspect_detection()
    test_quote_line_not_suspect()
    test_report_generated()
    test_strict_exit()
    print("\n✅ test_scan_source_tags 全部通过。")
