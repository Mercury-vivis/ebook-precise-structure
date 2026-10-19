#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_ocr_partial_naming.py —— 01a_ocr 抽样命名判定（纯函数，无解释器守卫）。

为什么独立成模块：01a_ocr.py 在 import 期触发 _interp_guard.ensure_interpreter()，
一旦被测试 import 就会切换/重跑解释器，导致单元测试无法在托管解释器下稳定收集。
本模块只含「本次 OCR 是否覆盖全书 → 输出文件名是否带 .partial」的纯判定逻辑，
不 import 任何第三方库、不触发守卫，供 01a_ocr 运行时调用，也供 test_01a_ocr_partial.py
直接 import 验证（2026-10-19 拆出：补 A2 抽样改名自动化测试）。

判定口径（与 01a_ocr 原逻辑逐字一致）：
  full_scan = (start == 1) 且 (未带 --limit 或 --limit 覆盖全书)
  --limit 0 等同未带（默认即 0）；--start != 1（即使不带 limit）也算抽样。
全书扫描 → 写正式底本 fulltext_raw.txt / page_index.json；
抽样扫描 → 写 fulltext_raw.partial.txt / page_index.partial.json，绝不覆盖正式底本。
"""

import os


def is_full_scan(start, limit, total):
    """True 当且仅当本次 OCR 覆盖全书（写正式底本而非 .partial 抽样）。

    start: --start（默认 1）；limit: --limit（默认 0 等同未带）；total: 全书页数。
    """
    return (start == 1) and (not limit or limit >= total)


def decide_output_suffix(start, limit, total):
    """全书扫描返回 ''；抽样扫描返回 '.partial'。"""
    return "" if is_full_scan(start, limit, total) else ".partial"


def build_output_paths(work, start, limit, total):
    """返回 (fulltext_raw 路径, page_index 路径)；抽样自动落到 .partial 文件名。"""
    suffix = decide_output_suffix(start, limit, total)
    return (
        os.path.join(work, f"fulltext_raw{suffix}.txt"),
        os.path.join(work, f"page_index{suffix}.json"),
    )
