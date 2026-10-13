"""回归测试：gen_dispatch_brief.py 派工 brief 自动生成（P1-A1）。

验证：
① 从 group_plan.json + book_meta.json 生成 brief 含章列表/phys范围/专名；
② 前卷经验注入（prev_experience 提取高频错形）；
③ briefs/_index.md 概览生成；
④ 缺 group_plan.json 时明确报错。

运行：python scripts/test_gen_dispatch_brief.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _common import safe_write


def _make_work(tmp):
    """造一个最小 work 目录：book_meta.json + group_plan.json + chapters/。"""
    work = os.path.join(tmp, "work")
    os.makedirs(os.path.join(work, "chapters"), exist_ok=True)

    # book_meta
    meta = {
        "title": "测试书",
        "type": "⑧ 编年/纪事型",
        "proper_names": ["张三", "李四", "王五"],
        "chapters": [
            {"no": 0, "name": "前辅文", "phys_start": 1, "phys_end": 2},
            {"no": 1, "name": "第一章 测试", "phys_start": 3, "phys_end": 10},
            {"no": 2, "name": "第二章 张三登场", "phys_start": 11, "phys_end": 20},
        ],
    }
    safe_write(os.path.join(work, "book_meta.json"), json.dumps(meta, ensure_ascii=False, indent=2))

    # chapters files + _chapters.json
    ch_meta = []
    for c in meta["chapters"]:
        no, name = c["no"], c["name"]
        fn = os.path.join(work, "chapters", f"{no:02d}_{name}.txt")
        safe_write(fn, f"<<<PAGE phys={c['phys_start']} book=1>>>\n测试内容 {name}\n")
        ch_meta.append({"no": no, "name": name, "phys_start": c["phys_start"],
                        "phys_end": c["phys_end"], "book_start": None, "book_end": None})
    safe_write(os.path.join(work, "chapters", "_chapters.json"),
               json.dumps(ch_meta, ensure_ascii=False, indent=2))

    # group_plan
    plan = {
        "book": "测试书",
        "type": "⑧ 编年/纪事型",
        "target_chars": 40000,
        "total_chapters": 3,
        "total_groups": 2,
        "total_chars": 100,
        "groups": [
            {"group": 1, "label": "前辅文", "chapters": [0], "phys_range": [1, 2],
             "chars": 10, "is_preface": True, "notes": ""},
            {"group": 2, "label": "第1-2章", "chapters": [1, 2], "phys_range": [3, 20],
             "chars": 90, "is_preface": False, "notes": ""},
        ],
    }
    safe_write(os.path.join(work, "group_plan.json"), json.dumps(plan, ensure_ascii=False, indent=2))
    return work


def _make_prev_work(tmp):
    """造一个前卷 work 目录，含 corrections 高频错形。"""
    prev = os.path.join(tmp, "prev_work")
    os.makedirs(os.path.join(prev, "corrections"), exist_ok=True)
    corr = [
        {"old": "奕诉", "new": "奕訢", "phys": 1, "ocr": True},
        {"old": "奕诉", "new": "奕訢", "phys": 5, "ocr": True},
        {"old": "大限", "new": "大隈", "phys": 3, "ocr": True},
    ]
    safe_write(os.path.join(prev, "corrections", "01_corrections.json"),
               json.dumps(corr, ensure_ascii=False))
    return prev


def test_basic_generation():
    """① 基本生成：brief 含章列表/phys范围/专名。"""
    tmp = tempfile.mkdtemp(prefix="brief_test_")
    try:
        work = _make_work(tmp)
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "gen_dispatch_brief.py"),
                           "--work", work], capture_output=True, text=True)
        assert r.returncode == 0, f"FAIL: exit {r.returncode}\n{r.stderr}"

        # 检查输出文件
        assert os.path.isfile(os.path.join(work, "briefs", "group_01.md"))
        assert os.path.isfile(os.path.join(work, "briefs", "group_02.md"))
        assert os.path.isfile(os.path.join(work, "briefs", "_index.md"))

        # 检查 group_02 内容
        with open(os.path.join(work, "briefs", "group_02.md"), encoding="utf-8") as f:
            content = f.read()
        assert "第1-2章" in content
        assert "phys 3-20" in content
        assert "张三" in content  # 专名
        assert "李四" in content
        assert "chapters" in content  # 章文件路径
        assert "corrections/02_corrections.json" in content  # 产出路径
        print("✅ test_basic_generation: brief 含章列表/phys范围/专名/产出路径")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_prev_experience():
    """② 前卷经验注入：高频错形出现在 brief 中。"""
    tmp = tempfile.mkdtemp(prefix="brief_prev_")
    try:
        work = _make_work(tmp)
        prev = _make_prev_work(tmp)
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "gen_dispatch_brief.py"),
                           "--work", work, "--prev-experience", prev], capture_output=True, text=True)
        assert r.returncode == 0, f"FAIL: exit {r.returncode}\n{r.stderr}"

        with open(os.path.join(work, "briefs", "group_02.md"), encoding="utf-8") as f:
            content = f.read()
        assert "前卷经验" in content
        assert "奕诉" in content
        assert "奕訢" in content
        assert "提示性" in content
        print("✅ test_prev_experience: 前卷高频错形注入，标注提示性")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_index_generation():
    """③ _index.md 概览生成。"""
    tmp = tempfile.mkdtemp(prefix="brief_idx_")
    try:
        work = _make_work(tmp)
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "gen_dispatch_brief.py"),
                           "--work", work], capture_output=True, text=True)
        assert r.returncode == 0

        with open(os.path.join(work, "briefs", "_index.md"), encoding="utf-8") as f:
            content = f.read()
        assert "测试书" in content
        assert "前辅文" in content
        assert "第1-2章" in content
        assert "group_NN.md" in content
        print("✅ test_index_generation: _index.md 含分组概览表")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_group_plan():
    """④ 缺 group_plan.json 时明确报错（exit 2）。"""
    tmp = tempfile.mkdtemp(prefix="brief_noplan_")
    try:
        work = _make_work(tmp)
        os.remove(os.path.join(work, "group_plan.json"))
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "gen_dispatch_brief.py"),
                           "--work", work], capture_output=True, text=True)
        assert r.returncode == 2, f"FAIL: expected exit 2, got {r.returncode}"
        assert "group_plan.json" in r.stderr
        print("✅ test_missing_group_plan: 缺 group_plan.json 明确报错 exit 2")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_basic_generation()
    test_prev_experience()
    test_index_generation()
    test_missing_group_plan()
    print("\nALL PASS · gen_dispatch_brief.py 派工 brief 自动生成（P1-A1）")
