#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_tests.py —— 技能脚本回归测试统一入口（N 系列 2026-10-04 审查新增）。

扫描本目录下 test_*.py 并逐个以当前解释器运行，任一失败即退出码 1。
用法：
    <PY> scripts/run_tests.py            # 跑全部
    <PY> scripts/run_tests.py <名称...>  # 只跑指定（如 run_tests.py emit）
（PY 解析顺序见 SKILL.md §1 / skill-dependencies.json，勿用 WorkBuddy 自带解释器。）

已固化测试：
  - test_apply_corrections_core.py  补丁施加核心：通改全书生效 / body0 批量消解（防嵌套留痕）/
                                    留痕与转录层 / deleted 不施加 / 单字通改护栏 /
                                    两种补丁文件格式 / 幻觉补丁记 miss
  - test_gate1b_e2e.py              端到端真跑 assemble_epub：错字清单残留只报告（不阻断）/ 清零如实报 0 /
                                    交付目录无 .bak（备份落点隔离）/ 前置缺失明确报错
  - test_emit_apply_report.py       应用报告残留口径：全文权威 / A+C 修正 / 留痕豁免 / 两路径一致
  - test_logical_join.py            跨行连行纠错：换行容忍匹配 + 原坐标回拆
  - test_derived_sync_e2e.py         派生件一致性：notes 同步 / 04 §四留痕 / 派生件残留只报告 /
                                    清单缺失明写跳过 / 前置不留半成品
  - test_patch_shape_and_anchors.py  补丁形态与锚点：单 dict 台账一致 / 部分锚丢失硬失败 / char_class 书型
  - test_pdf_sync_e2e.py             PDF 路径端到端：02 同源同步（P0）/ 装配中断零残留

端到端用例（test_*_e2e.py）会真跑 assemble，耗时数秒；只跑单元用例可用
`<PY> scripts/run_tests.py core emit join`。
"""
import glob, os, runpy, sys

# ---- 解释器守卫（v1.30.0）：先切到 EBOOK_PY 指定的 venv，再跑测试 ----
# 详见 _interp_guard.py。本入口以当前解释器（runpy 同进程）驱动全部 test_*.py，
# 此处切换一次，所有被测脚本即全部落在 venv 上。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    pats = argv[1:]
    tests = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    if pats:
        tests = [t for t in tests if any(p in os.path.basename(t) for p in pats)]
    if not tests:
        print("未找到匹配的 test_*.py")
        return 1
    failed = []
    for t in tests:
        name = os.path.basename(t)
        print(f"\n===== 运行 {name} =====")
        try:
            runpy.run_path(t, run_name="__main__")
        except SystemExit as e:
            if e.code not in (0, None):
                failed.append(name)
        except Exception as e:
            print(f"❌ {name} 异常：{type(e).__name__}: {e}")
            failed.append(name)
    if failed:
        print(f"\n❌ {len(failed)} 个测试失败：{failed}")
        return 1
    print(f"\n✅ 全部 {len(tests)} 个测试文件通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
