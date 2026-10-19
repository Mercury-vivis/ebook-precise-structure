#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_01a_ocr_partial.py —— 01a_ocr 抽样输出命名判定（A2 事故根防）。

只测 _ocr_partial_naming 的纯函数：无解释器守卫、无第三方依赖，可在任意解释器下稳定收集。
覆盖 01a_ocr 原逻辑的全部判定分支：
  - 全书扫描（start=1 且未带/带满 limit）→ 正式底本（无 .partial）
  - 抽样（start≠1，或 limit<total）→ .partial，绝不落到正式底本
  - build_output_paths 文件名与 work 目录拼接正确
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from _ocr_partial_naming import (
    is_full_scan,
    decide_output_suffix,
    build_output_paths,
)


def check(cond, msg):
    if not cond:
        raise SystemExit(f"❌ {msg}")
    print(f"✓ {msg}")


def main():
    # ---- is_full_scan ----
    # 全书：start=1, limit=0（默认） → True
    check(is_full_scan(1, 0, 167) is True, "is_full_scan(1,0,167)=True（默认未带 limit）")
    # 全书：start=1, limit==total → True
    check(is_full_scan(1, 167, 167) is True, "is_full_scan(1,167,167)=True（limit 覆盖全书）")
    # 抽样：start=1, limit<total → False
    check(is_full_scan(1, 100, 167) is False, "is_full_scan(1,100,167)=False（limit 不足）")
    # 抽样：start≠1（即使不带 limit）→ False
    check(is_full_scan(20, 0, 167) is False, "is_full_scan(20,0,167)=False（start≠1）")
    # 抽样：start≠1, limit<total → False
    check(is_full_scan(20, 10, 167) is False, "is_full_scan(20,10,167)=False")

    # ---- decide_output_suffix ----
    check(decide_output_suffix(1, 0, 167) == "", "全书 → suffix=''")
    check(decide_output_suffix(1, 167, 167) == "", "limit 覆盖全书 → suffix=''")
    check(decide_output_suffix(1, 100, 167) == ".partial", "limit 不足 → suffix='.partial'")
    check(decide_output_suffix(20, 0, 167) == ".partial", "start≠1 → suffix='.partial'")
    check(decide_output_suffix(20, 10, 167) == ".partial", "start≠1+limit → suffix='.partial'")

    # ---- build_output_paths：文件名与 work 拼接 ----
    work = "/tmp/work"
    p_full_t, p_full_i = build_output_paths(work, 1, 0, 167)
    check(p_full_t == os.path.join(work, "fulltext_raw.txt"), "全书 → fulltext_raw.txt")
    check(p_full_i == os.path.join(work, "page_index.json"), "全书 → page_index.json")

    p_part_t, p_part_i = build_output_paths(work, 20, 10, 167)
    check(p_part_t == os.path.join(work, "fulltext_raw.partial.txt"),
          "抽样 → fulltext_raw.partial.txt（不覆盖正式底本）")
    check(p_part_i == os.path.join(work, "page_index.partial.json"),
          "抽样 → page_index.partial.json（不覆盖正式底本）")

    # 抽样文件名绝不出现正式底本名（防东方主义式覆盖事故）
    check("fulltext_raw.partial.txt" != "fulltext_raw.txt", "抽样文件名 ≠ 正式底本名")
    check(".partial" in p_part_t and ".partial" in p_part_i, "抽样两路径均带 .partial")

    print("\n✅ test_01a_ocr_partial 全部通过。")


if __name__ == "__main__":
    main()
