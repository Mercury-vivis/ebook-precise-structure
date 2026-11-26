#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_footnote_seed.py —— 生成脚注号形态归一种子 `corrections/00z_footnote_form.json`

为什么单独一步：脚注号形态归一（`[12〕`/`〔12]`/`[12]` → `〔12〕`）是对正文的**改动**，
必须作为 corrections 条目存在，才能进 04 §一、被闸B 反向复现、被闸B 正向验残留。
写在施加器里就是账外改动——改了字却查不到账。

调用时机
  · 路径三（bookocr 三件套）：`00_ingest_bookocr.py` 产出 fulltext_struct.txt 后自动调用，
    新书不需要单跑本脚本。
  · 路径一/二（PDF 文字层 / EPUB）：底本就绪（Stage 1+2 完成）后跑一次；
    重跑 assemble 之前若增删过底本，也应重跑一次。
  · 存量书：本脚本幂等——种子已存在就不动（可能已被人工改宽），已标 `status:deleted`
    就尊重撤销、不重建。故重复跑安全。

判据（全部可判、无猜测，详见 `_common.seed_footnote_form`）
  · 西文书（CJK 占比 < 10%）：不生成。`[N]` 是西文脚注的正规形态，`[1795]` 是书目年，
    归一即改写语义。
  · 全书已是标准〔N〕：不生成（无形态不一致可纠）。
  · 其余：生成一条 `phys=ALL` + `regex` + `systematic` 的条目，只匹配 1–3 位编号
    （4 位以上按年份/书目对待，不归一）。

用法：python gen_footnote_seed.py --work <work> [--quiet]
退出码：0 = 已判定（生成/已存在/无需生成/西文书跳过）；2 = work 下无 fulltext_struct.txt。
"""
import os
import sys
import argparse

from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

from _common import seed_footnote_form  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="生成脚注号形态归一种子（corrections/00z_footnote_form.json）")
    ap.add_argument("--work", required=True, help="工作目录（含 fulltext_struct.txt）")
    ap.add_argument("--quiet", action="store_true", help="只出错误，不出摘要")
    args = ap.parse_args()

    info = seed_footnote_form(args.work, quiet=args.quiet)
    if info["status"] == "no_struct":
        print(f"❌ 未找到 fulltext_struct.txt：{os.path.join(args.work, 'fulltext_struct.txt')}")
        print("   先跑完 Stage 1+2（底本就绪）再生成归一种子。")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
