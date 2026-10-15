"""回归测试：export_proper_names.py 跨册专名词典增量导出（v1.28.0 新增 · P1）。

验证：
- 从 corrections 抽专名类补丁（type/reason 命中线索），正字取 new、错形取 old；
- 删除型（new 空）、正则型、非专名类不入典；
- 只做并集不覆盖：已有条目的错形追加、不丢原有错形；
- 新条目 confirmed=false（防污染，不自动生效）；
- --dry-run 不写盘，--apply 才写；
- 与 _common.load_proper_names_lib 同一真相源（schema 归一可读）。

运行：python scripts/test_export_proper_names.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from export_proper_names import collect_from_corrections, collect_from_meta, merge_into_lib  # noqa: E402
from _common import load_proper_names_lib  # noqa: E402


def _work(d):
    w = os.path.join(d, "work")
    os.makedirs(os.path.join(w, "corrections"))
    return w


def test_collect_from_corrections():
    """专名类补丁入典；删除型 / 正则型 / 非专名类不入典。"""
    d = tempfile.mkdtemp(prefix="epn_")
    w = _work(d)
    rows = [
        {"old": "奕诉", "new": "奕訢", "type": "专名", "reason": "奕訢形近"},
        {"old": "奕忻", "new": "奕訢", "type": "专名", "reason": "同上"},
        {"old": "荀或", "new": "荀彧", "type": "专名", "reason": "人名专名"},
        # reason 含「非专名」否定表述、type 为形近字 → 应被否定排除（防误收）
        {"old": "磨宋", "new": "唐宋", "type": "形近字", "reason": "复合错形，磨→唐（非专名）"},
        {"old": "衍字", "new": "", "type": "专名", "reason": "删除型无正字"},
        {"old": "[〇○]年", "new": "〇年", "type": "专名", "reason": "正则型", "regex": True},
    ]
    json.dump(rows, open(os.path.join(w, "corrections", "01.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    got = collect_from_corrections(w, min_len=2)
    assert set(got.get("奕訢", [])) == {"奕诉", "奕忻"}, f"FAIL: 错形未归并 {got}"
    assert "荀彧" in got, f"FAIL: 人名类应入典 {got}"
    assert "唐宋" not in got, f"FAIL: 非专名类不应入典 {got}"
    assert "" not in got, "FAIL: 删除型不应入典"
    assert "〇年" not in got, "FAIL: 正则型不应入典"
    print("✅ test_collect_from_corrections: 专名类入典，删除型/正则型/非专名不入典")


def test_collect_from_meta():
    """book_meta.proper_names 提供正字（无错形）。"""
    d = tempfile.mkdtemp(prefix="epn_")
    w = _work(d)
    json.dump({"title": "测试书", "proper_names": ["吴大澂", "大隈重信"]},
              open(os.path.join(w, "book_meta.json"), "w", encoding="utf-8"), ensure_ascii=False)
    got = collect_from_meta(w)
    assert set(got) == {"吴大澂", "大隈重信"}, f"FAIL: {got}"
    print("✅ test_collect_from_meta: book_meta 正字入典")


def test_merge_union_no_overwrite():
    """并集合并：新错形追加、原错形不丢、新条目 confirmed=false。"""
    lib = {"names": [
        {"name": "奕訢", "wrong_forms": ["奕诉"], "source": "旧卷", "confirmed": True, "note": "恭亲王"},
    ]}
    cands = {"奕訢": ["奕诉", "奕忻"], "宋秉畯": ["宋秉峻"]}
    added, appended, skipped = merge_into_lib(lib, cands, "新卷")
    assert added == ["宋秉畯"], f"FAIL: 新增项不对 {added}"
    assert ("奕訢", ["奕忻"]) in appended, f"FAIL: 错形未追加 {appended}"
    e = [n for n in lib["names"] if n["name"] == "奕訢"][0]
    assert set(e["wrong_forms"]) == {"奕诉", "奕忻"}, f"FAIL: 原错形被覆盖 {e}"
    assert e["confirmed"] is True, "FAIL: 已有条目 confirmed 不应被降级"
    assert "旧卷" in e["source"] and "新卷" in e["source"], f"FAIL: source 未追加 {e['source']}"
    new = [n for n in lib["names"] if n["name"] == "宋秉畯"][0]
    assert new["confirmed"] is False, "FAIL: 新条目必须 confirmed=false（防污染）"
    print("✅ test_merge_union_no_overwrite: 只并集不覆盖、新条目未确认")


def test_lib_schema_readable():
    """导出的库与 _common 真相源同 schema，可被读回。"""
    lib = load_proper_names_lib()
    assert isinstance(lib["names"], list), "FAIL: names 应为数组"
    for n in lib["names"]:
        assert set(n) >= {"name", "wrong_forms", "source", "confirmed"}, f"FAIL: 条目字段缺失 {n}"
    print(f"✅ test_lib_schema_readable: 库 schema 归一可读（{len(lib['names'])} 条）")


def test_dry_run_vs_apply():
    """--dry-run 不写盘；--apply 写盘且条目数增加。"""
    import subprocess
    import shutil
    d = tempfile.mkdtemp(prefix="epn_")
    w = _work(d)
    json.dump([{"old": "测试错形一", "new": "测试专名甲", "type": "专名", "reason": "测试"}],
              open(os.path.join(w, "corrections", "01.json"), "w", encoding="utf-8"),
              ensure_ascii=False)

    # 复制一份库到临时位置，避免污染真库：借助 --work 之外无法改路径，
    # 故本用例只验证 dry-run 不写盘 + apply 写盘（写入真库后再还原）
    lib_path = os.path.join(HERE, "..", "references", "proper_names_cross_book.json")
    lib_path = os.path.normpath(lib_path)
    backup = open(lib_path, encoding="utf-8").read()
    try:
        r1 = subprocess.run([sys.executable, os.path.join(HERE, "export_proper_names.py"),
                             "--work", w], capture_output=True, text=True, encoding="utf-8")
        assert r1.returncode == 0, f"FAIL: dry-run 退出码 {r1.returncode}\n{r1.stderr}"
        assert "[dry-run] 未写盘" in r1.stdout, f"FAIL: 未提示 dry-run\n{r1.stdout}"
        assert open(lib_path, encoding="utf-8").read() == backup, "FAIL: dry-run 竟然写盘了"

        r2 = subprocess.run([sys.executable, os.path.join(HERE, "export_proper_names.py"),
                             "--work", w, "--apply"], capture_output=True, text=True, encoding="utf-8")
        assert r2.returncode == 0, f"FAIL: apply 退出码 {r2.returncode}\n{r2.stderr}"
        after = json.load(open(lib_path, encoding="utf-8"))
        assert any(n["name"] == "测试专名甲" for n in after["names"]), "FAIL: apply 未写入"
        assert not any(n["name"] == "测试专名甲" and n["confirmed"] for n in after["names"]), \
            "FAIL: 新条目应为 confirmed=false"
        print("✅ test_dry_run_vs_apply: dry-run 不写盘 / apply 写盘且新条目未确认")
    finally:
        # 还原真库（测试用的「测试专名甲」不得留在生产库里）
        with open(lib_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(backup)
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    test_collect_from_corrections()
    test_collect_from_meta()
    test_merge_union_no_overwrite()
    test_lib_schema_readable()
    test_dry_run_vs_apply()
    print("\n✅ test_export_proper_names 全部通过。")
