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
  - 否则切到目标解释器重跑本脚本（同 argv、同环境变量、同 cwd），并**透传返回码**。
    切换后 EBOOK_PY == sys.executable，守卫直接返回，不会循环。
  - 目标解释器不可执行（损坏 / 权限）时静默降级用当前解释器，不阻断流程。

Windows 分支为何不用 os.execv（2026-10-17 实测修复）：
  MSVCRT 的 _execv 在 Windows 上是「spawn 新进程 + 当前进程立即退出」，且当前进程
  退出码恒为 0。于是无论脚本成功还是失败，调用方拿到的退出码都是 0——本技能整套
  「退出码即闸」的判定（闸0/闸1b/闸2/复扫/check_patch/verify_delivery）在设置了
  EBOOK_PY 的机器上全部失效，失败被伪装成成功。
  实测对照：同一 verify_delivery 失败用例，带 EBOOK_PY 得 0、清空 EBOOK_PY 得 1。
  故 Windows 改为 subprocess 转发 + sys.exit(rc)；POSIX 保留 execv（无此问题、零额外进程）。

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
    """确保本进程运行在 EBOOK_PY 指向的解释器上；条件不满足时静默返回。

    切换后**必须把目标解释器的返回码透传出去**——调用方（agent / bat / CI）以退出码
    判定闸是否通过，吞掉返回码等于把所有失败伪装成成功（Windows 实测教训见模块头）。
    """
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
    except OSError:
        return

    argv = [target] + sys.argv
    if os.name == "nt":
        # Windows：execv 由 CRT 实现为 spawn + 立即退出(码 0)，会吞掉失败退出码。
        # 改为子进程转发（同 argv、继承 stdio），显式透传返回码。
        import subprocess

        try:
            rc = subprocess.call(argv)
        except OSError:
            return  # 目标不可执行：按当前解释器继续，不阻断流程
        sys.exit(rc)
    try:
        os.execv(target, argv)  # POSIX：真正替换进程，返回码原样保留
    except OSError:
        return
