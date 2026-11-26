#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""⑪ 大事记/编年史型 02 三轨回归（2026-10-19，SKILL:539 / book-type:119 承诺落地）。

固化能力：⑪ 书的 02 走「大事年表＋关键人物＋关键文献」三轨（此前 is_narrative 无⑪、
脚本只出「概念+名句」——文档承诺落空）。覆盖：
  1) is_narrative 收录⑪（NARRATIVE_TYPE_PREFIX 三处一致）；
  2) PDF 路径（pipeline_assemble）：三轨三节 + 不渲染要点节 + 形态标识；
  3) EPUB 路径（assemble_epub）：同口径三轨。

须经 run_tests.py（或本机 venv 解释器）驱动，勿用 WorkBuddy 自带解释器。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

from _common import is_narrative  # 单测直 import（run_tests 同进程已在 venv）

HERE = os.path.dirname(os.path.abspath(__file__))
PDF_ASM = os.path.join(HERE, "pipeline_assemble.py")
EPUB_ASM = os.path.join(HERE, "assemble_epub.py")

# ⑪ 型笔记：每章=一个年份段，法定小节=时段概况/大事编年/关键人物与动向/关键文献·条约/历史意义
# （历史意义写成纯段落、不带 `- ` 条目——避免落入上一捕获小节桶，测试只验三轨本身）
NOTES_PDF = (
    "## 1. 1920 年\n**时段概况**：本年直皖相争，局势动荡。\n"
    "**大事编年**：\n- 1月：直皖战争爆发（PAGE phys=1）。\n- 7月：某条约签订（PAGE phys=1）。\n"
    "**关键人物与动向**：\n- 袁世凯：称帝失败退居（PAGE phys=1）。\n- 段祺瑞：主战（PAGE phys=1）。\n"
    "**关键文献/条约**：\n- 《某条约》：核心内容一二三（PAGE phys=1）。\n"
    "**历史意义**：承上启下之一年。（纯段落，无条目）\n\n"
    "## 2. 1921 年\n**时段概况**：又一年。\n"
    "**大事编年**：\n- 3月：某事再起（PAGE phys=2）。\n"
    "**关键人物与动向**：\n- 孙中山：改组（PAGE phys=2）。\n"
    "**关键文献/条约**：\n- 《另一文件》：要点（PAGE phys=2）。\n"
    "**历史意义**：承继之年。（纯段落，无条目）\n"
)
NOTES_EPUB = NOTES_PDF.replace("（PAGE phys=1）", "（SEC idx=1）").replace("（PAGE phys=2）", "（SEC idx=2）")

STRUCT_PDF = (
    "<<<PAGE phys=1 book=1 title=1920 年>>>\n1920 年，直皖战争爆发，某条约签订。\n"
    "<<<PAGE phys=2 book=2 title=1921 年>>>\n1921 年，时事延续。\n"
)
RAW_EPUB = (
    "<<<SEC idx=1 title=1920 年>>>\n1920 年，直皖战争爆发，某条约签订。\n"
    "<<<SEC idx=2 title=1921 年>>>\n1921 年，时事延续。\n"
)
META = {
    "book": "测试大事记", "author": "某", "type": "⑪ 大事记/编年史型", "source": "测试",
    "total_phys": 2,
    "chapters": [{"no": 1, "name": "1920 年", "phys_start": 1},
                 {"no": 2, "name": "1921 年", "phys_start": 2}],
}
PAGE_INDEX = {"total": 2, "pages": [{"phys": 1, "book": 1}, {"phys": 2, "book": 2}]}


def _w(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def _build(tmp):
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "out")
    os.makedirs(work, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    _w(os.path.join(work, "fulltext_struct.txt"), STRUCT_PDF)
    _w(os.path.join(work, "fulltext_raw.txt"), RAW_EPUB)
    _w(os.path.join(work, "page_index.json"), json.dumps(PAGE_INDEX, ensure_ascii=False))
    _w(os.path.join(work, "book_meta.json"), json.dumps(META, ensure_ascii=False))
    _w(os.path.join(work, "corrections", "01.json"), json.dumps([], ensure_ascii=False))
    _w(os.path.join(work, "notes", "01.md"), NOTES_PDF)
    return work, out


def _run(script, work, out):
    cmd = [sys.executable, script, "--work", work, "--out", out,
           "--meta", os.path.join(work, "book_meta.json")]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=HERE, timeout=180)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _read02(out):
    for f in os.listdir(out):
        if "02_" in f and f.endswith(".md"):
            return open(os.path.join(out, f), encoding="utf-8").read()
    return ""


def test_is_narrative_includes_chronicle():
    """NARRATIVE_TYPE_PREFIX 收录⑪——与 SKILL:535/539、book-type:121 三处一致。"""
    assert is_narrative("⑪ 大事记/编年史型"), "FAIL: ⑪ 未入 NARRATIVE_TYPE_PREFIX"
    assert is_narrative("① 事件/战役型"), "FAIL: ① 回归"
    assert not is_narrative("② 思想/理论型"), "FAIL: ② 误入"
    print("✅ test_is_narrative_includes_chronicle: ⑪ 已入叙事型清单（三处一致）")


def test_pdf_chronicle_three_tracks():
    """PDF 路径：⑪ 书 02 出三轨三节、不渲染要点节、形态标识正确。"""
    tmp = tempfile.mkdtemp(prefix="t_chr_pdf_")
    try:
        work, out = _build(tmp)
        rc, log = _run(PDF_ASM, work, out)
        assert rc == 0, "FAIL: PDF 装配 rc=%d：\n%s" % (rc, log[:2500])
        c02 = _read02(out)
        assert c02, "FAIL: 02 未生成"
        for t in ("大事年表", "关键人物索引", "关键文献/条约索引"):
            assert t in c02, "FAIL: 02 缺三轨之「%s」：\n%s" % (t, c02[:1200])
        for entry in ("直皖战争爆发", "袁世凯", "《某条约》"):
            assert entry in c02, "FAIL: 三轨缺条目「%s」：\n%s" % (entry, c02[:1200])
        assert "关键要点速查" not in c02, "FAIL: ⑪ 却渲染了要点节（应被三轨取代）"
        assert "大事年表+关键人物+关键文献" in log, \
            "FAIL: 形态标识未切三轨：\n%s" % log[:1200]
        print("✅ test_pdf_chronicle_three_tracks: PDF 路径⑪三轨落地（年表/人物/文献，无要点节）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_epub_chronicle_three_tracks():
    """EPUB 路径：⑪ 书 02 同口径三轨。"""
    tmp = tempfile.mkdtemp(prefix="t_chr_epub_")
    try:
        work, out = _build(tmp)
        _w(os.path.join(work, "notes", "01.md"), NOTES_EPUB)
        rc, log = _run(EPUB_ASM, work, out)
        assert rc == 0, "FAIL: EPUB 装配 rc=%d：\n%s" % (rc, log[:2500])
        c02 = _read02(out)
        assert c02, "FAIL: 02 未生成"
        for t in ("大事年表", "关键人物索引", "关键文献/条约索引"):
            assert t in c02, "FAIL: 02 缺三轨之「%s」：\n%s" % (t, c02[:1200])
        for entry in ("直皖战争爆发", "袁世凯", "《某条约》"):
            assert entry in c02, "FAIL: 三轨缺条目「%s」：\n%s" % (entry, c02[:1200])
        assert "关键要点速查" not in c02, "FAIL: ⑪ 却渲染了要点节（应被三轨取代）"
        assert "大事年表+关键人物+关键文献" in c02, \
            "FAIL: 02 头形态标识未切三轨：\n%s" % c02[:600]
        print("✅ test_epub_chronicle_three_tracks: EPUB 路径⑪三轨落地")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_non_chronicle_unaffected():
    """回归保护：非⑪ 叙事型书仍走原「要点+名句」，三轨不误触。"""
    tmp = tempfile.mkdtemp(prefix="t_chr_neg_")
    try:
        work, out = _build(tmp)
        meta = dict(META); meta["type"] = "① 事件/战役型"
        _w(os.path.join(work, "book_meta.json"), json.dumps(meta, ensure_ascii=False))
        notes_ev = NOTES_PDF.replace("**大事编年**：", "**战争战役**：").replace(
            "**关键人物与动向**：", "**人物**：").replace("**关键文献/条约**：", "**文献**：")
        _w(os.path.join(work, "notes", "01.md"), notes_ev)
        rc, log = _run(PDF_ASM, work, out)
        assert rc == 0, "FAIL: ① 型书装配 rc=%d：\n%s" % (rc, log[:2500])
        c02 = _read02(out)
        assert "大事年表" not in c02, "FAIL: 非⑪ 书误触三轨"
        assert "要点+名句" in log, "FAIL: ① 型书形态标识异常：\n%s" % log[:1200]
        print("✅ test_non_chronicle_unaffected: 非⑪ 书仍走要点+名句，三轨不误触")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_typed_non_chronicle_with_appendix():
    """回归：meta.type 已标注为 ② 时，章名兜底不得把含「附录」章的非编年书误判为 ⑪ 三轨。"""
    tmp = tempfile.mkdtemp(prefix="t_chr_typed_")
    try:
        work, out = _build(tmp)
        meta = dict(META)
        meta["type"] = "② 思想/理论型"
        meta["chapters"] = [{"no": 1, "name": "军事思想", "phys_start": 1},
                            {"no": 2, "name": "附录：编辑出版人员", "phys_start": 2}]
        _w(os.path.join(work, "book_meta.json"), json.dumps(meta, ensure_ascii=False))
        _w(os.path.join(work, "fulltext_struct.txt"),
           "<<<PAGE phys=1 book=1>>>\n军事思想：关于战争与军队问题的理性认识。\n"
           "<<<PAGE phys=2 book=2>>>\n附录：编辑出版人员名单。\n")
        _w(os.path.join(work, "notes", "01.md"),
           "## 1. 军事思想\n**核心概念**：\n- 概念甲：定义（PAGE phys=1）。\n\n"
           "## 2. 附录：编辑出版人员\n**核心概念**：\n- 概念乙：定义（PAGE phys=2）。\n")
        rc, log = _run(PDF_ASM, work, out)
        assert rc == 0, "FAIL: ② 型含附录章装配 rc=%d：\n%s" % (rc, log[:2500])
        c02 = _read02(out)
        assert "大事年表" not in c02, "FAIL: ② 型书误触三轨：\n%s" % c02[:1200]
        assert "概念+名句" in log, "FAIL: ② 型书形态标识异常：\n%s" % log[:1200]
        print("✅ test_typed_non_chronicle_with_appendix: ② 型含附录章仍走概念+名句，三轨不误触")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_typed_chronicle_plain_chapters():
    """回归：已标注 ⑪ 但章名不呈大事记体时，仍以标注为准走三轨（锁 startswith 优先级）。"""
    tmp = tempfile.mkdtemp(prefix="t_chr_plain_")
    try:
        work, out = _build(tmp)
        meta = dict(META)
        meta["type"] = "⑪ 大事记/编年史型"
        meta["chapters"] = [{"no": 1, "name": "上编", "phys_start": 1},
                            {"no": 2, "name": "下编", "phys_start": 2}]
        _w(os.path.join(work, "book_meta.json"), json.dumps(meta, ensure_ascii=False))
        _w(os.path.join(work, "fulltext_struct.txt"),
           "<<<PAGE phys=1 book=1>>>\n上编：某年纪事。\n"
           "<<<PAGE phys=2 book=2>>>\n下编：另一年纪事。\n")
        _w(os.path.join(work, "notes", "01.md"),
           "## 1. 上编\n**大事编年**：\n- 1月：某事（PAGE phys=1）。\n"
           "**关键人物与动向**：\n- 人物甲：动向（PAGE phys=1）。\n"
           "**关键文献/条约**：\n- 《某文件》：要点（PAGE phys=1）。\n\n"
           "## 2. 下编\n**大事编年**：\n- 2月：另一事（PAGE phys=2）。\n")
        rc, log = _run(PDF_ASM, work, out)
        assert rc == 0, "FAIL: ⑪ 普通章名装配 rc=%d：\n%s" % (rc, log[:2500])
        c02 = _read02(out)
        assert "大事年表" in c02, "FAIL: ⑪ 已标注却未走三轨：\n%s" % c02[:1200]
        assert "大事年表+关键人物+关键文献" in log, \
            "FAIL: ⑪ 形态标识异常：\n%s" % log[:1200]
        print("✅ test_typed_chronicle_plain_chapters: ⑪ 标注优先于章名，普通章名仍走三轨")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_untyped_year_chapters():
    """回归：meta.type 未标注且章名呈大事记体（≥3 章含四位年份）时，章名兜底仍生效。"""
    tmp = tempfile.mkdtemp(prefix="t_chr_untyped_")
    try:
        work, out = _build(tmp)
        meta = dict(META)
        meta["type"] = ""
        meta["total_phys"] = 3
        meta["chapters"] = [{"no": 1, "name": "1920 年", "phys_start": 1},
                            {"no": 2, "name": "1921 年", "phys_start": 2},
                            {"no": 3, "name": "1922 年", "phys_start": 3}]
        _w(os.path.join(work, "book_meta.json"), json.dumps(meta, ensure_ascii=False))
        _w(os.path.join(work, "fulltext_struct.txt"),
           "<<<PAGE phys=1 book=1 title=1920 年>>>\n1920 年，某事。\n"
           "<<<PAGE phys=2 book=2 title=1921 年>>>\n1921 年，另一事。\n"
           "<<<PAGE phys=3 book=3 title=1922 年>>>\n1922 年，第三事。\n")
        _w(os.path.join(work, "page_index.json"),
           json.dumps({"total": 3, "pages": [{"phys": 1, "book": 1},
                                              {"phys": 2, "book": 2},
                                              {"phys": 3, "book": 3}]}, ensure_ascii=False))
        _w(os.path.join(work, "notes", "01.md"), NOTES_PDF +
           "\n## 3. 1922 年\n**大事编年**：\n- 3月：第三事（PAGE phys=3）。\n"
           "**关键人物与动向**：\n- 人物丙：动向（PAGE phys=3）。\n"
           "**关键文献/条约**：\n- 《第三文件》：要点（PAGE phys=3）。\n")
        rc, log = _run(PDF_ASM, work, out)
        assert rc == 0, "FAIL: 未标注年份章装配 rc=%d：\n%s" % (rc, log[:2500])
        c02 = _read02(out)
        assert "大事年表" in c02, "FAIL: 未标注年份章未走章名兜底三轨：\n%s" % c02[:1200]
        assert "大事年表+关键人物+关键文献" in log, \
            "FAIL: 未标注年份章形态标识异常：\n%s" % log[:1200]
        print("✅ test_untyped_year_chapters: 未标注且章名呈大事记体，兜底仍走三轨")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_epub_typed_non_chronicle_with_appendix():
    """回归：EPUB 路径与 PDF 同口径——已标注 ② 的书不因「附录」章误触三轨。"""
    tmp = tempfile.mkdtemp(prefix="t_chr_epub_typed_")
    try:
        work, out = _build(tmp)
        meta = dict(META)
        meta["type"] = "② 思想/理论型"
        meta["chapters"] = [{"no": 1, "name": "军事思想", "phys_start": 1},
                            {"no": 2, "name": "附录：编辑出版人员", "phys_start": 2}]
        _w(os.path.join(work, "book_meta.json"), json.dumps(meta, ensure_ascii=False))
        _w(os.path.join(work, "notes", "01.md"),
           "## 1. 军事思想\n**核心概念**：\n- 概念甲：定义（SEC idx=1）。\n\n"
           "## 2. 附录：编辑出版人员\n**核心概念**：\n- 概念乙：定义（SEC idx=2）。\n")
        rc, log = _run(EPUB_ASM, work, out)
        assert rc == 0, "FAIL: EPUB ② 型含附录章装配 rc=%d：\n%s" % (rc, log[:2500])
        c02 = _read02(out)
        assert "大事年表" not in c02, "FAIL: EPUB ② 型书误触三轨：\n%s" % c02[:1200]
        assert "概念+名句" in c02, "FAIL: EPUB ② 型书形态标识异常：\n%s" % c02[:600]
        print("✅ test_epub_typed_non_chronicle_with_appendix: EPUB ② 型含附录章仍走概念+名句")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_is_narrative_includes_chronicle()
    test_pdf_chronicle_three_tracks()
    test_epub_chronicle_three_tracks()
    test_non_chronicle_unaffected()
    test_typed_non_chronicle_with_appendix()
    test_typed_chronicle_plain_chapters()
    test_untyped_year_chapters()
    test_epub_typed_non_chronicle_with_appendix()
    print("\nALL PASS · ⑪ 三轨回归通过（清单一致 / ⑪ 优先级 / 非⑪不误触 / 未标注兜底 / PDF / EPUB）。")
