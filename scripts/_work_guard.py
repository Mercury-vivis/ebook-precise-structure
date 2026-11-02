# -*- coding: utf-8 -*-
"""工作目录守卫：`<work>` 只允许在已存在的 `<加工根>/.work/books/` 下新建。

问题（2026-10-25）：加工层移出 OneDrive 后，旧默认 `<交付库根>/.work/books/<书名>`
会落在同步盘里；入口脚本此前一律 `os.makedirs(args.work, exist_ok=True)`，指向错的
根也照建，几分钟就在 OneDrive 里长出一整棵加工树，并持续产生同步流量。库手册明令
「加工层不得静默建目录」，但执行层一直没落。

判据不是「`<work>` 存不存在」，而是「**父目录**存不存在」：
- 父目录在（= `<加工根>/.work/books`）→ 根指对了，新书首跑允许建；
- 父目录不在 → 根指错了，硬失败并给出应有形态。
这样既堵住凭空造树，又不挡新书第一次跑。

与 `_interp_guard.py` 同构：入口处调用一次，失败即 `sys.exit(2)`。
"""
import difflib
import os
import sys

ENV = "EBOOK_WORK_ROOT"


def work_root():
    """加工层 `.work` 目录（EBOOK_WORK_ROOT 的值就是它本身）；未设则返回 None，不猜路径。"""
    v = os.environ.get(ENV)
    return os.path.abspath(v) if v else None


def _siblings(parent):
    try:
        return sorted(d for d in os.listdir(parent)
                      if os.path.isdir(os.path.join(parent, d)) and not d.startswith((".", "_")))
    except OSError:
        return []


def ensure_work_dir(work, tool=""):
    """返回可用的工作目录绝对路径；父目录不存在则退出（exit code 2）。"""
    w = os.path.abspath(work)
    parent = os.path.dirname(w)
    name = os.path.basename(w)
    tag = "[%s] " % tool if tool else ""

    if os.path.isdir(w):
        # 已存在：不动它，只在形态异常时提醒（可能是同步盘里的旧残留）
        pn = os.path.basename(parent)
        gn = os.path.basename(os.path.dirname(parent))
        if not (pn == "books" and gn == ".work"):
            print("%s注意：工作目录 %s 不在 `<加工根>/.work/books/` 形态下，"
                  "确认它不是同步盘内的旧残留。" % (tag, w), flush=True)
        return w

    sibs = _siblings(parent)  # 先取既有兄弟，避免把刚建出来的自己算进"相近"

    if not os.path.isdir(parent):
        root = work_root()
        guess = os.path.join(root, "books", name) if root else None
        msg = [
            "%s拒绝创建：%s" % (tag, w),
            "%s其父目录不存在：%s" % (tag, parent),
            "%s正确形态是 <加工根>/.work/books/<书名>——父目录不在，通常说明加工根指错了。" % tag,
            "%s本机加工根由环境变量 %s 指定（当前：%s）。" % (tag, ENV, root or "未设置"),
        ]
        if guess:
            msg.append("%s按该环境变量推算应为：%s" % (tag, guess))
        msg.append("%s确要新建整套加工层，先手工建出 `%s` 再重跑——"
                   "守卫只允许在既有 books/ 下加新书，不许凭空造树。" % (tag, parent))
        print("\n".join(msg), file=sys.stderr, flush=True)
        sys.exit(2)

    os.makedirs(w, exist_ok=True)
    out = ["%s新建工作目录：%s（父目录既有 %d 个书名目录）" % (tag, w, len(sibs))]
    near = difflib.get_close_matches(name, sibs, n=3, cutoff=0.7)
    if near:
        out.append("%s⚠️ 相近的既有目录：%s —— 若是同一本书请改名后重跑，"
                   "否则会分叉成两套。" % (tag, "、".join(near)))
    print("\n".join(out), flush=True)
    return w
