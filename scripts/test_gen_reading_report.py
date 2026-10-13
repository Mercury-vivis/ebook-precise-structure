"""回归测试：gen_reading_report.py D1/D2 新增函数（P0）。

验证：
- parse_audit_report：各闸数字提取（闸0/1/1b/2/3、trace/ocr/未命中）
- parse_apply_report：应用/未命中/残留/冲突计数
- load_manifest_stats：active/deleted 统计
- sum_read_lines：行范围求和 + 排除 phys/book 范围
- collect_doubts：存疑补丁提取（type=存疑 / reason 以存疑开头）
- select_key_patches：代表性纠错选取（去重、排除存疑/删除/已删）

运行：python scripts/test_gen_reading_report.py
"""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gen_reading_report import (
    parse_audit_report, parse_apply_report, load_manifest_stats,
    sum_read_lines, collect_doubts, select_key_patches
)


AUDIT_SAMPLE = """# 设闸审计报告
- 闸0 锚点有效性：03 含 `<<<PAGE` 锚 264 个
- 闸1 检索可达性：提供检索词 71 个，缺失 4 个（['词A', '词B']）
- 闸1b 高频错字清零：87 项，残留 0 项（全部清零，通过）
- 闸2 内容正确性（独立词典基准）：混淆词典扫描命中 = 0 类 → PASS
- 闸2 补丁生效审计（原 C1）：assemble 后回放 active 补丁 old 对 03 干净正文，真实残留 = 0 处 → PASS
- 闸3 02 完整性（原 C2）：⚠️ 1 章在笔记中无对应标题/事件 [(7, '第十八章')]，可能静默缺章
- 纠错留痕：本次应用 trace 型纠错 17 处 + 系统噪声通改 1 类 + 转录层纠错 91 处
- 补丁施用：corrections/ 共 4 份；未命中 0 处
- 静默删除（结构性噪音）：0 处
"""


def test_parse_audit():
    info = parse_audit_report(AUDIT_SAMPLE)
    assert info["gate0_anchors"] == 264
    assert info["gate1_terms"] == 71
    assert info["gate1_missing_n"] == 4
    assert info["gate1b_items"] == 87
    assert info["gate1b_residual"] == 0
    assert info["gate2_dict_hits"] == 0
    assert info["gate2_patch_residual"] == 0
    assert info["gate3_missing_chapters"] == 1
    assert info["trace_n"] == 17
    assert info["tongai_n"] == 1
    assert info["ocr_n"] == 91
    assert info["miss_n"] == 0
    print("✅ test_parse_audit: 各闸数字提取正确")


def test_parse_apply():
    text = """# 组装应用报告
- 已应用（active 补丁 old 已不在 03 干净正文）：108 处
- 底本 0 命中（补丁 old 不在 fulltext_struct，疑似错位）：0 处
- ⚠️ 残留（old 仍在 03 干净正文，未应用成功）：0 处
- 存疑待核（补丁冲突，同一原文多候选）：0 处
- 形近字裸串无上下文（须人工复核，防纠错造错）：0 处
"""
    info = parse_apply_report(text)
    assert info["applied"] == 108
    assert info["zero_hit"] == 0
    assert info["residual"] == 0
    assert info["conflict"] == 0
    assert info["bare_near"] == 0
    print("✅ test_parse_apply: 应用报告计数正确")


def test_load_manifest():
    d = tempfile.mkdtemp(prefix="manifest_test_")
    mp = os.path.join(d, "patch_manifest.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump({"patches": [
            {"id": "1", "old": "a", "new": "b", "status": "active"},
            {"id": "2", "old": "c", "new": "d", "status": "active"},
            {"id": "3", "old": "e", "new": "", "status": "deleted"},
        ]}, f)
    stats = load_manifest_stats(mp)
    assert stats["active"] == 2
    assert stats["deleted"] == 1
    assert stats["total"] == 3
    print("✅ test_load_manifest: active/deleted 统计正确")


def test_sum_read_lines_excludes_phys():
    """行范围求和排除 phys/book 范围。"""
    text = """## 组A
- 行范围：1-1042（phys1-72）
- 已读行数：1040
## 组B
- 行范围：1043-1918（phys73-126）
"""
    total = sum_read_lines(text)
    # 1-1042 = 1042行，1043-1918 = 876行，合计1918
    # phys1-72 和 phys73-126 应被排除
    assert total == 1918, f"FAIL: 应=1918，got={total}"
    print("✅ test_sum_read_lines_excludes_phys: 行范围求和 + 排除phys范围")


def test_collect_doubts():
    """存疑补丁提取：type=存疑 和 reason 以存疑开头。"""
    d = tempfile.mkdtemp(prefix="doubts_test_")
    os.makedirs(os.path.join(d, "corrections"))
    with open(os.path.join(d, "corrections", "01.json"), "w", encoding="utf-8") as f:
        json.dump([
            {"old": "踞踏", "new": "", "phys": 15, "type": "存疑",
             "reason": "存疑：踞踏非词，暂不改。"},
            {"old": "竞行", "new": "竟行", "phys": 15, "type": "ocr",
             "reason": "形近OCR误识。"},
            {"old": "振聋睛", "new": "振聋聩", "phys": 30, "type": "ocr",
             "reason": "存疑：成语振聋发聩，睛疑为聩，但字形相近度不足，存疑待考。暂不改。"},
        ], f)
    doubts = collect_doubts(d)
    assert len(doubts) == 2, f"FAIL: 应提取2条存疑，got={len(doubts)}"
    assert doubts[0][1] == 15  # phys
    assert "踞踏" in doubts[0][2]  # old
    print("✅ test_collect_doubts: 存疑补丁提取（type+reason双口径）")


def test_select_key_patches():
    """代表性纠错选取：去重new、排除存疑/删除/已删/systematic。"""
    patches = [
        {"old": "奕诉", "new": "奕訢", "phys": 1},
        {"old": "奕诉等", "new": "奕訢等", "phys": 2},  # new去重（奕訢已选）
        {"old": "大限", "new": "大隈", "phys": "ALL", "systematic": True},  # 排除systematic
        {"old": "踞踏", "new": "", "phys": 3, "type": "存疑"},  # 排除存疑+删除
        {"old": "旧词", "new": "", "phys": 4, "status": "deleted"},  # 排除已删
        {"old": "相形见细", "new": "相形见绌", "phys": 5},
    ]
    keys = select_key_patches(patches, n=10)
    news = [n for _, n in keys]
    assert "奕訢" in news
    assert "相形见绌" in news
    assert "大隈" not in news, "FAIL: systematic不应入选"
    assert news.count("奕訢") == 1, "FAIL: new应去重"
    print("✅ test_select_key_patches: 代表性纠错选取（去重+排除）")


if __name__ == "__main__":
    test_parse_audit()
    test_parse_apply()
    test_load_manifest()
    test_sum_read_lines_excludes_phys()
    test_collect_doubts()
    test_select_key_patches()
    print("\nALL PASS · gen_reading_report D1/D2 函数通过。")
