#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_systematic_overshoot.py — v1.34.1 任务E / v1.34.2 修正：加字型 systematic 通改损伤检测

覆盖：头部加字误伤检出 / 重复加字（底本已是正确 new）检出 / 标点后独立成词不误报 /
ASCII+中文引号独立成词不误报 / 尾部加字误伤与重复加字双检出 / 纯替换型跳过 /
裸单字跳过 / 全量计数与样例截取 / 端到端 check_patch WARN。
"""
import os, sys, json, tempfile, subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from check_patch import _check_systematic_overshoot, _PUNCT_SET


def test_overshoot_head():
    """头部加字：old='个社会', new='一个社会'，底本含'整个社会'→应检出误伤"""
    struct = "这是整个社会的问题。一个社会的责任。"
    na, nb, samples = _check_systematic_overshoot("个社会", "一个社会", struct)
    assert na >= 1, f"应检出误伤，实际 na={na}"
    assert "整个社会" in samples[0][1], f"样例应含'整个社会'，实际 {samples}"
    print("✅ test_overshoot_head: 头部加字误伤检出")


def test_duplicate_insert():
    """v1.34.2：底本已是正确 new（'一个社会'），应检出重复加字而非静默"""
    na, nb, samples = _check_systematic_overshoot("个社会", "一个社会", "一个社会的责任。")
    assert nb == 1 and na == 0, f"应检出重复加字 1 处，实际 na={na} nb={nb}"
    assert samples[0][0] == "重复加字"
    print("✅ test_duplicate_insert: 重复加字（B 类）检出")


def test_no_overshoot_at_punct():
    """old 在标点后（独立成词），不算误伤"""
    na, nb, _ = _check_systematic_overshoot("个社会", "一个社会", "因此，个社会应当负责。")
    assert (na, nb) == (0, 0), f"标点后独立成词不应告警，实际 na={na} nb={nb}"
    print("✅ test_no_overshoot_at_punct: 标点后独立成词不误报")


def test_quote_punct_in_set():
    """v1.34.2：ASCII 双引号与中文引号须在标点集内（引号后独立成词不告警）"""
    for q in ('"', "“", "‘", "　"):  # ASCII 双引号 / 弯双引号 / 弯单引号 / 全角空格
        assert q in _PUNCT_SET, f"{q!r} 应在 _PUNCT_SET 内"
        na, nb, _ = _check_systematic_overshoot("个社会", "一个社会", f"他说{q}个社会应当负责。”")
        assert (na, nb) == (0, 0), f"引号 {q!r} 后独立成词不应告警，实际 na={na} nb={nb}"
    print("✅ test_quote_punct_in_set: 引号/全角空格后独立成词不误报")


def test_duplicate_with_punct_ins():
    """v1.34.2 回归：ins 本身是标点（old='唐会要》', new='《唐会要》'），
    '《唐会要》' 须判重复加字，不得被「标点后独立成词」分支吞掉（实证曾致 95 处 '《《'）"""
    na, nb, samples = _check_systematic_overshoot("唐会要》", "《唐会要》", "见《唐会要》卷六二。唐会要》残。")
    assert nb == 1 and na == 0, f"'《唐会要》' 应判重复加字，实际 na={na} nb={nb}"
    assert samples[0][0] == "重复加字"
    print("✅ test_duplicate_with_punct_ins: 标点型插入串的重复加字检出")


def test_tail_overshoot_and_duplicate():
    """尾部加字：old='社会', new='社会化'，'社会学'→误伤、'社会化进程'→重复加字，双检出"""
    na, nb, _ = _check_systematic_overshoot("社会", "社会化", "他学社会学。这是社会化进程。")
    assert na == 1 and nb == 1, f"应误伤1+重复1，实际 na={na} nb={nb}"
    print("✅ test_tail_overshoot_and_duplicate: 尾部加字双检出")


def test_full_count_vs_samples():
    """v1.34.2：计数为全量，样例至多 3 条"""
    struct = "整个社会。每个社会。全个社会。这个社会。各个社会。"  # 5 处误伤
    na, nb, samples = _check_systematic_overshoot("个社会", "一个社会", struct)
    assert na == 5 and nb == 0, f"应全量计数 5 处误伤，实际 na={na}"
    assert len(samples) == 3, f"样例应截取 3 条，实际 {len(samples)}"
    print("✅ test_full_count_vs_samples: 全量计数 + 样例截取")


def test_non_additive_skipped():
    """纯替换型（new 非 old 超串）跳过"""
    na, nb, _ = _check_systematic_overshoot("股墟", "殷墟", "股墟卜辞。殷墟卜辞。")
    assert (na, nb) == (0, 0), f"纯替换型应跳过，实际 na={na} nb={nb}"
    print("✅ test_non_additive_skipped: 纯替换型跳过")


def test_bare_char_skipped():
    """裸单字跳过"""
    na, nb, _ = _check_systematic_overshoot("己", "已", "自己有")
    assert (na, nb) == (0, 0), f"裸单字应跳过，实际 na={na} nb={nb}"
    print("✅ test_bare_char_skipped: 裸单字跳过")


def test_e2e_check_patch():
    """端到端：check_patch.py 对含误伤与重复加字的 systematic 补丁报 WARN"""
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "corrections"))
        with open(os.path.join(d, "fulltext_struct.txt"), "w", encoding="utf-8") as f:
            f.write("整个社会都在关注。一个社会的责任。")
        with open(os.path.join(d, "book_meta.json"), "w", encoding="utf-8") as f:
            json.dump({"total_phys": 1, "chapters": []}, f)
        with open(os.path.join(d, "corrections", "t.json"), "w", encoding="utf-8") as f:
            json.dump([{"old": "个社会", "new": "一个社会", "phys": "ALL", "systematic": True}], f)
        r0 = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, "check_patch.py"), d],
                            capture_output=True, text=True, encoding="utf-8")
        assert "加字通改误伤 1" in r0.stdout, f"摘要应含类别计数，输出：{r0.stdout}"
        r = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, "check_patch.py"), d, "--verbose"],
                           capture_output=True, text=True, encoding="utf-8")
        assert "重复加字 1 处" in r.stdout, f"明细应报重复加字计数，输出：{r.stdout}"
        assert "误伤正确词 1 处" in r.stdout, f"明细应报误伤计数，输出：{r.stdout}"
        print("✅ test_e2e_check_patch: 端到端 WARN 检出（含分型计数）")


if __name__ == "__main__":
    test_overshoot_head()
    test_duplicate_insert()
    test_no_overshoot_at_punct()
    test_quote_punct_in_set()
    test_duplicate_with_punct_ins()
    test_tail_overshoot_and_duplicate()
    test_full_count_vs_samples()
    test_non_additive_skipped()
    test_bare_char_skipped()
    test_e2e_check_patch()
    print("\n✅ test_systematic_overshoot 全部通过")
