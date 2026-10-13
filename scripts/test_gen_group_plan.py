"""回归测试：gen_group_plan.py 分组方案自动推荐（P0-A2）。

验证核心分组逻辑：
- 前辅文独立成组（no=0 / 第一章名含前辅文关键词）；
- 连续小章按目标字符数合并、且保持章节连续性；
- 单个超长章独占一组并给出"章内拆分"提示（不拆章）；
- 卷边界（vol_map/juan_map）不跨组；
- 字符数守恒、组内章号连续。

运行：python scripts/test_gen_group_plan.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gen_group_plan import (
    recommend_groups, is_preface_chapter, detect_vol_boundaries, count_body_chars
)


def _ch(no, name, pstart, pend):
    return {"no": no, "name": name, "phys_start": pstart, "phys_end": pend}


def _pair(ch, nchars):
    return (ch, nchars)


def test_preface_independent():
    """前辅文必须独立成组，不与正文混组。"""
    data = [
        _pair(_ch(0, "前辅文（导言/目录）", 1, 20), 13000),
        _pair(_ch(1, "第一章 开端", 21, 50), 30000),
        _pair(_ch(2, "第二章 发展", 51, 80), 30000),
    ]
    groups = recommend_groups(data, target_chars=40000)
    assert groups[0]["is_preface"] is True, "FAIL: 第一组应为前辅文"
    assert groups[0]["chapters"] == [0], "FAIL: 前辅文组应只含 no=0"
    assert all(not g["is_preface"] for g in groups[1:]), "FAIL: 正文组不应标记前辅文"
    print("✅ test_preface_independent: 前辅文独立成组")


def test_small_chapters_merge():
    """连续小章应合并到同一组，直到接近目标字符数。"""
    data = [
        _pair(_ch(0, "前辅文", 1, 10), 5000),
        _pair(_ch(1, "第一章", 11, 30), 15000),
        _pair(_ch(2, "第二章", 31, 50), 15000),
        _pair(_ch(3, "第三章", 51, 70), 15000),
    ]
    groups = recommend_groups(data, target_chars=40000)
    # 前辅文独立；正文 15000+15000=30000（不超52000），再加15000=45000（仍不超52000？）
    # 15000+15000+15000=45000 < 40000*1.3=52000，三章合并
    body_groups = [g for g in groups if not g["is_preface"]]
    assert len(body_groups) == 1, f"FAIL: 三章应合并为一组（got {len(body_groups)}）"
    assert body_groups[0]["chapters"] == [1, 2, 3], "FAIL: 合并组章号应为[1,2,3]"
    print("✅ test_small_chapters_merge: 连续小章合并")


def test_long_chapter_independent():
    """单个超长章独占一组，不拆章，并给出章内拆分提示。"""
    data = [
        _pair(_ch(0, "前辅文", 1, 10), 5000),
        _pair(_ch(1, "第一章 大事件", 11, 120), 80000),
        _pair(_ch(2, "第二章", 121, 140), 20000),
    ]
    groups = recommend_groups(data, target_chars=40000)
    big = [g for g in groups if 1 in g["chapters"]][0]
    assert big["chapters"] == [1], "FAIL: 超长章应独占一组"
    assert "章内拆分" in big["notes"], "FAIL: 超长章应给出章内拆分提示"
    # 第二章不应并入超长章
    assert 2 not in big["chapters"], "FAIL: 后续章不应并入超长章"
    print("✅ test_long_chapter_independent: 超长章独立 + 拆分提示")


def test_continuity_and_conservation():
    """组内章号连续、phys_range 正确、字符数守恒。"""
    data = [
        _pair(_ch(0, "前辅文", 1, 10), 5000),
        _pair(_ch(1, "第一章", 11, 40), 30000),
        _pair(_ch(2, "第二章", 41, 70), 30000),
        _pair(_ch(3, "第三章", 71, 100), 30000),
    ]
    groups = recommend_groups(data, target_chars=40000)
    # 每章 30000，两两合并（30000+30000=60000 > 52000 → 不合并），每章独立
    # 校验所有章号被覆盖且无重复
    all_nos = []
    total = 0
    for g in groups:
        nos = g["chapters"]
        # 连续性
        assert nos == list(range(nos[0], nos[-1] + 1)), f"FAIL: 组内章号不连续 {nos}"
        all_nos.extend(nos)
        total += g["chars"]
    assert sorted(all_nos) == [0, 1, 2, 3], "FAIL: 章号覆盖不全或重复"
    assert total == 95000, f"FAIL: 字符数不守恒（got {total}）"
    print("✅ test_continuity_and_conservation: 连续性 + 字符数守恒")


def test_vol_boundaries():
    """卷边界不跨组：vol_map 中每卷首章 phys_start 触发结组。"""
    meta = {
        "vol_map": [
            {"phys_start": 1},
            {"phys_start": 50},
            {"phys_start": 100},
        ]
    }
    boundaries = detect_vol_boundaries(meta)
    assert boundaries == {1, 50, 100}, f"FAIL: 卷边界解析错误 {boundaries}"
    data = [
        _pair(_ch(1, "卷一·章一", 1, 49), 20000),
        _pair(_ch(2, "卷二·章一", 50, 99), 20000),
        _pair(_ch(3, "卷三·章一", 100, 130), 20000),
    ]
    groups = recommend_groups(data, target_chars=60000, vol_boundaries=boundaries)
    # 即使 target 允许合并，卷边界也强制分组
    assert len(groups) == 3, f"FAIL: 卷边界应强制分3组（got {len(groups)}）"
    print("✅ test_vol_boundaries: 卷边界不跨组")


def test_is_preface_and_count():
    """前辅文识别与字符统计。"""
    assert is_preface_chapter(_ch(0, "前辅文", 1, 10), 0) is True
    assert is_preface_chapter(_ch(1, "编例", 1, 10), 0) is True
    assert is_preface_chapter(_ch(1, "第一章 正文", 11, 30), 0) is False
    # 非第一章即使含"序"也不判前辅文（防正文误匹配）
    assert is_preface_chapter(_ch(5, "某序章", 80, 90), 4) is False
    # 字符统计剥锚与空白
    n = count_body_chars("<<<PAGE phys=1 book=1>>>\n正文 内容\n\n")
    assert n == 4, f"FAIL: 字符统计错误（got {n}）"
    print("✅ test_is_preface_and_count: 前辅文识别 + 字符统计")


if __name__ == "__main__":
    test_preface_independent()
    test_small_chapters_merge()
    test_long_chapter_independent()
    test_continuity_and_conservation()
    test_vol_boundaries()
    test_is_preface_and_count()
    print("\nALL PASS · 分组方案推荐逻辑（前辅文独立/连续合并/长章独立/卷边界/守恒）验证通过。")
