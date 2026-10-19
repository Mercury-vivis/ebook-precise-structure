#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_citation_verbatim.py —— 引文逐字率 C 口径（只核「关键原句摘录」）四类判定

守六件事（2026-10-19 立，C 口径取代「全篇引号」口径）：
  1. 只抽「关键原句摘录」小节：同文件的标题行/加粗小节名下的引号**不得**被当引文
  2. 锚剥离覆盖三种真实写法：`（原书页码 N）` / `（phys N）` / `（SEC idx=N）`
  3. 四类分流：✅03逐字 / ⚠️仅raw / ⚠️03疑似残留 / ❌均无
  4. 省略号切片：各段（≥4 字）都命中才算命中
  5. 03 句内夹入页码数字时仍判命中（底本一侧多数字才容忍，笔记的数字差异不容忍）
  6. 无「关键原句摘录」小节 → state=no_section，摘要必须写「未执行」而**不是 0%**

运行：python scripts/test_citation_verbatim.py
"""
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _common import (  # noqa: E402
    citation_verbatim_scan, citation_verbatim_summary, citation_verbatim_report,
    extract_quote_section, _strip_citation_anchor, normalize_citation_text,
)

fails = []


def check(desc, cond):
    if not cond:
        fails.append(desc)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


CLEAN = """<<<PAGE phys=1 book=1>>>
甲乙丙丁戊己庚辛壬癸，老骥伏枥志在千里，烈士暮年壮心不巳也。
<<<PAGE phys=2 book=2>>>
天地玄黄宇宙洪荒，日月盈昃辰宿列张。
<<<PAGE phys=3 book=3>>>
并封340为忠王，常侍传语，寒来暑往秋收冬藏。
"""
RAW = """<<<PAGE phys=1 book=1>>>
甲乙丙丁戊己庚辛壬癸，老骥伏枥志在千里，烈士暮年壮心不巳也。
<<<PAGE phys=2 book=2>>>
天地玄黄宇宙洪芒，日月盈昃辰宿列张。
<<<PAGE phys=3 book=3>>>
并封340为忠王，常侍传语，寒来暑往秋收冬藏。
"""

NOTES = """# 甲章笔记

## 1. 甲章

**核心概念**：
- 这一句“被引号包着但不在摘录小节里”不该被当引文核。

**关键原句摘录**：
> “甲乙丙丁……戊己庚辛壬癸”（原书页码 1）

> “天地玄黄宇宙洪荒”（phys 2）

> “烈士暮年壮心不已”（原书页 1）

> “天地玄黄宇宙洪芒”（phys 2）

> “子丑寅卯辰巳午未”（phys 9）

> “并封为忠王”（phys 3）

> “常侍（李愬）传语”（phys 3）

**我的判断**：
- “又一句引号，属于判断段，不是摘录。”
"""


def build(tmp):
    work = os.path.join(tmp, "work")
    write(os.path.join(work, "fulltext_clean.txt"), CLEAN)
    write(os.path.join(work, "fulltext_raw.txt"), RAW)
    write(os.path.join(work, "notes", "01.md"), NOTES)
    return work, [os.path.join(work, "notes", "01.md")]


def test_extract_section():
    rows = extract_quote_section(NOTES)
    texts = [t for _, t in rows]
    check("小节应抽到 7 条引用块", len(rows) == 7)
    check("标题行不被抽", all("被引号包着" not in t for t in texts))
    check("判断段不被抽", all("又一句引号" not in t for t in texts))
    check("省略号条目在抽中", any("……" in t for t in texts))
    print(f"✅ test_extract_section: 抽到 {len(rows)} 条，标题/判断段已排除")


def test_strip_anchor():
    cases = {
        "“甲乙丙丁”（原书页码 12）": "“甲乙丙丁”",
        "“甲乙丙丁”（phys 7）": "“甲乙丙丁”",
        "“甲乙丙丁”（SEC idx=3）": "“甲乙丙丁”",
        "“甲乙丙丁”（原书页 4）。": "“甲乙丙丁”",
    }
    for raw, want in cases.items():
        got = _strip_citation_anchor(raw)
        check(f"剥锚失败：{raw} → {got}（应为 {want}）", got == want)
    print("✅ test_strip_anchor: 三种锚写法 + 锚后句号全部剥净")


def test_four_classes():
    with tempfile.TemporaryDirectory() as tmp:
        work, notes = build(tmp)
        s = citation_verbatim_scan(work, notes)
        check(f"总数应为 7，实为 {s['total']}", s["total"] == 7)
        check(f"✅03逐字应为 3（含夹数字那条），实为 {s['ok']}", s["ok"] == 3)
        check(f"⚠️仅raw 应为 1，实为 {s['raw_only']}", s["raw_only"] == 1)
        check(f"⚠️疑似残留 应为 1，实为 {s['suspected']}", s["suspected"] == 1)
        check(f"❌均无 应为 2，实为 {s['none']}", s["none"] == 2)

        items = s["items"]
        sus = [x for x in items if x["status"] == "suspected"]
        check("疑似残留应指出 已/巳 差异",
              bool(sus) and "已" in sus[0]["detail"] and "巳" in sus[0]["detail"])
        raw_only = [x for x in items if x["status"] == "raw_only"]
        check("仅raw 条目须给出方向提示（台账已纠正 / 03 改坏）",
              bool(raw_only) and "04" in raw_only[0]["detail"])
        digit = [x for x in items if x["status"] == "ok" and x["detail"]]
        check("夹入数字那条须注明「03 该处夹入数字」",
              any("夹入数字" in x["detail"] for x in digit))
        anno = [x for x in items if x["status"] == "none" and "剥圆括号注后逐字命中" in x["detail"]]
        check("括号注条目须标注「属笔记加注」", len(anno) == 1)
        print(f"✅ test_four_classes: 7 条 → 逐字{s['ok']} / 仅raw{s['raw_only']}"
              f" / 疑似{s['suspected']} / 均无{s['none']}")


def test_ellipsis_slice():
    work_text = "孤帆远影碧空尽唯见长江天际流"
    note = """**关键原句摘录**：
> “孤帆远影碧空尽……唯见长江天际流”（phys 1）
> “孤帆远影碧空尽……此段并不存在”（phys 1）
"""
    with tempfile.TemporaryDirectory() as tmp:
        work = os.path.join(tmp, "work")
        write(os.path.join(work, "fulltext_clean.txt"), f"<<<PAGE phys=1>>>\n{work_text}\n")
        write(os.path.join(work, "fulltext_raw.txt"), f"<<<PAGE phys=1>>>\n{work_text}\n")
        write(os.path.join(work, "notes", "01.md"), note)
        s = citation_verbatim_scan(work, [os.path.join(work, "notes", "01.md")])
        check(f"省略号两段全中应判逐字，实为 {s['ok']}", s["ok"] == 1)
        check(f"省略号有一段不中应判未命中，实为 {s['none']}", s["none"] == 1)
        print(f"✅ test_ellipsis_slice: 切片逐段核（1 中 1 不中）")


def test_no_section():
    note = "# 笔记\n\n## 1. 甲章\n\n**核心概念**：\n- 只有概念，没有摘录小节。\n"
    with tempfile.TemporaryDirectory() as tmp:
        work = os.path.join(tmp, "work")
        write(os.path.join(work, "fulltext_clean.txt"), CLEAN)
        write(os.path.join(work, "notes", "01.md"), note)
        notes = [os.path.join(work, "notes", "01.md")]
        s = citation_verbatim_scan(work, notes)
        check(f"无小节应 state=no_section，实为 {s['state']}", s["state"] == "no_section")
        summary = citation_verbatim_summary(work, notes)
        check("无小节摘要须写「未执行」", "未执行" in summary)
        check("无小节摘要不得出现 0%（会被读成有对象且全错）", "0%" not in summary)
        check("无小节摘要须声明「不代表通过」", "不代表通过" in summary)
        print(f"✅ test_no_section: {summary[:36]}…")


def test_report_file():
    with tempfile.TemporaryDirectory() as tmp:
        work, notes = build(tmp)
        s = citation_verbatim_scan(work, notes)
        out = os.path.join(tmp, "custom_report.md")
        citation_verbatim_report(s, work, out)
        check("自定义路径报告应落盘", os.path.isfile(out))
        text = io.open(out, encoding="utf-8").read()
        for kw in ("一、⚠️ 03 疑似残留", "二、⚠️ 仅原始底本命中", "三、❌ 两源均无", "四、✅ 03 逐字命中"):
            check(f"报告缺分栏：{kw}", kw in text)
        check("报告须声明不阻断", "不阻断" in text)
        print("✅ test_report_file: 四栏齐备 + 不阻断声明")


def main():
    test_extract_section()
    test_strip_anchor()
    test_four_classes()
    test_ellipsis_slice()
    test_no_section()
    test_report_file()
    if fails:
        print("\n❌ 失败项：")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("\n全部通过（6 组）")


if __name__ == "__main__":
    main()
