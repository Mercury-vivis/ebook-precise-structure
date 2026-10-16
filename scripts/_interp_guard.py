#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_interp_guard.py —— 解释器守卫（v1.30.0，2026-10-16 新增）

用途：把「本技能脚本跑在哪个 Python 上」的选择权从调用方收回脚本自身。

背景（为什么需要它）：WorkBuddy 等 agent 平台会强制用自带/托管解释器唤起脚本
（平台规则优先级高于技能文档），而用户的依赖装在自建 venv（EBOOK_PY 指向）。
结果是：每次跑技能都现场往托管解释器补装依赖；客户端一升级，托管解释器整体
重建、已装依赖蒸发，再缺再装——「反复安装依赖」的死循环。本守卫让脚本无论
被哪个解释器唤起，都自动切到 EBOOK_PY 指定的 venv 重跑，依赖永远在 venv 里找。

机制：
  - EBOOK_PY 未设置 / 目标文件不存在 → 返回，按当前解释器跑（旧行为不变）；
  - 目标 == 当前解释器 → 返回（零开销，venv 内手动跑时不发生任何事）；
  - 否则 os.execv 切到目标解释器重跑本脚本（同 argv、同环境变量、同 cwd）。
    切换后 EBOOK_PY == sys.executable，守卫直接返回，不会循环。
  - 目标解释器不可执行（损坏 / 权限）时静默降级用当前解释器，不阻断流程。

调用约束：必须在 import 第三方库（fitz / pymupdf / cv2 / ebooklib / numpy /
rapidocr / opencc / pdfplumber 等）**之前**调用，否则切换前就已 ImportError。

接入方式（按脚本 import 顺序二选一）：
  - import 了 _common / _fig_common 的脚本：两共享模块顶部已调用，无需再加；
  - 其余脚本（第三方 import 在共享模块之前，或不 import 共享模块）：
    在文件头第三方 import 之前加两行——
        from _interp_guard import ensure_interpreter
        ensure_interpreter()
    作为主脚本运行时 sys.path[0] 即脚本所在目录，import 必然可解析。
"""

import os
import sys


def ensure_interpreter():
    """确保本进程运行在 EBOOK_PY 指向的解释器上；条件不满足时静默返回。"""
    target = os.environ.get("EBOOK_PY")
    if not target:
        return
    try:
        if not os.path.isfile(target):
            return
        if os.path.normcase(os.path.abspath(target)) == os.path.normcase(
            os.path.abspath(sys.executable)
        ):
            return
        os.execv(target, [target] + sys.argv)
    except OSError:
        # 目标解释器不可执行（损坏 / 权限问题）：不阻断流程，按当前解释器继续
        return
