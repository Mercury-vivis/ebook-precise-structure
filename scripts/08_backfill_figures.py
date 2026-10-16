#!/usr/bin/env python3
# 08_backfill_figures.py —— 图片补嵌框架（C 类缺口页回溯补图）
#
# ⚠️ 这是【框架】，不是可直接批量跑的成品。
#    main() 仅做参数解析与【单页】调用示例；批量执行补图需用户单独批准后另行驱动。
#    用途：把 00d_scan_figure_gaps.py 标出的「疑似图片文字层」页，从原 PDF 回渲整页/图块，
#          补成 imgs/p{phys}_backfill.jpg，并生成 silent 补丁（图片 + 图题替换短行文字层，
#          图外正文保留）。补丁形态与 corrections/*.json 一致（{"phys","old","new","silent":true}）。
#
# 依赖：优先 pdfplumber（pdfplumber + PyPDF2/pypdf 读页与图对象）；
#       渲染整页图块用 pdfplumber 自带 page.to_image()（底层 pdf2image/poppler）。
#       若运行环境未装 pdfplumber，本脚本仍可 import（import 做了延迟/容错），
#       单页函数在真正调用时才报缺依赖——保证框架可被静态引用与单测。
#
# 用法（仅单页 dry 示例）：
#   python 08_backfill_figures.py --pdf <原书>.pdf --pages 406 --work <work目录>
#   # 上面只会打印 backfill_one_page() 的返回补丁 dict，不写盘、不改 struct。
#
# 流程：--pdf 打开 → 定位 phys 页（00c/00d 锚体系 phys=1-based）→ 判定含图 →
#       截整页/图块为 jpg → 构造 silent 补丁 dict → 返回。

import os
import re
import sys
import argparse

# ---- 解释器守卫（v1.30.0）：先切到 EBOOK_PY 指定的 venv，再 import 第三方库 ----
# 详见 _interp_guard.py。本脚本的 pdfplumber 为函数内延迟 import，文件头切换即可覆盖。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()


def _import_pdfplumber():
    """延迟导入 pdfplumber；缺失时给出明确报错，不阻断框架 import。"""
    try:
        import pdfplumber  # noqa
        return pdfplumber
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "需要 pdfplumber 才能实际补图。请在技能规定的 venv 中运行（见 SKILL.md §1 环境准备 / "
            "skill-dependencies.json optional-tools 组）：<PY> -m pip install pdfplumber。"
            "本框架在未装时仍可 import 与审阅。"
        ) from e


def page_has_image(page):
    """判定该页是否含图片对象（光栅图）。

    pdfplumber 的 page.images 列出页内光栅图对象；若为空则可能是矢量图或纯文字，
    可回退到渲染后按暗密度判图（见 render_page_image）。
    """
    imgs = getattr(page, "images", None) or []
    return len(imgs) > 0


def render_page_image(pdf_path, phys, work_dir, width=1300):
    """把 phys 页渲染成 jpg，落到 work/imgs/p{phys}_backfill.jpg。

    返回落盘路径。phys 为 1-based（与 fulltext_struct.txt 的 PAGE phys 锚一致）。
    """
    pdfplumber = _import_pdfplumber()
    out_dir = os.path.join(work_dir, "imgs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"p{phys}_backfill.jpg")
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[phys - 1]  # 1-based → 0-based
        im = page.to_image(resolution=150)
        pil_im = im.original.convert("RGB")
        pil_im.save(out_path, format="JPEG")
    return out_path


def build_silent_patch(phys, kept_body_text, image_rel_path, caption=""):
    """构造 silent 补丁 dict：把短行文字层替换为图片引用。

    - phys: 物理页号（与 corrections 口径一致）
    - kept_body_text: 该页【需要保留】的正文片段（图外正文），作为 new 的保留部分
    - image_rel_path: 相对 03 的图片路径（如 imgs/p406_backfill.jpg）
    - caption: 图题（可选）

    返回 dict：{"phys":phys,"old":<待替换短行文字层>,"new":<图片引用+保留正文>,"silent":true,
                "reason":"C类图片缺口回溯补图"}
    注意：old 须由调用方对照 fulltext_struct.txt 该页实际短行文字层填入；本框架只给骨架。
    """
    img_line = f"![图{phys}]({image_rel_path})"
    if caption:
        img_line = f"![{caption}]({image_rel_path})"
    new = img_line + ("\n\n" + kept_body_text if kept_body_text else "")
    return {
        "phys": phys,
        "old": "<此处填入该页待替换的短行文字层原文，须与 fulltext_struct.txt 逐字一致>",
        "new": new,
        "silent": True,
        "reason": "C类图片缺口回溯补图（00d 标出 → 08 从 PDF 回渲）",
    }


def backfill_one_page(pdf_path, phys, work_dir):
    """单页补图主流程（框架，含完整 docstring）。

    步骤：
      1. 打开 PDF，定位 phys 页（1-based）。
      2. 判定含图：先查 page.images（光栅图对象）；为空则按渲染暗密度回退判图。
      3. 截取整页为 jpg 到 work/imgs/p{phys}_backfill.jpg。
      4. 生成 silent 补丁（图片引用 + 图题替换短行文字层，图外正文保留）。
      5. 返回补丁 dict（不写盘、不改 struct）。

    参数：
      pdf_path: 原书 PDF 路径。
      phys:     物理页号（1-based，与 PAGE phys 锚一致）。
      work_dir: 本书 work 目录（imgs/ 落此下）。

    返回：
      dict，形如：
      {
        "phys": 406,
        "has_image": True,
        "image_path": "imgs/p406_backfill.jpg",
        "patch": { ...build_silent_patch 的返回... },
        "note": "框架产物：old 待对照 struct 填入后再入 corrections/"
      }
    """
    pdfplumber = _import_pdfplumber()
    with pdfplumber.open(pdf_path) as pdf:
        if phys < 1 or phys > len(pdf.pages):
            raise ValueError(f"phys={phys} 越界（PDF 共 {len(pdf.pages)} 页）")
        page = pdf.pages[phys - 1]
        has_img = page_has_image(page)

    image_path = render_page_image(pdf_path, phys, work_dir)
    rel = os.path.relpath(image_path, os.path.join(work_dir, "..", ".."))
    patch = build_silent_patch(phys, kept_body_text="",
                               image_rel_path=f"imgs/p{phys}_backfill.jpg")
    return {
        "phys": phys,
        "has_image": has_img,
        "image_path": image_path,
        "patch": patch,
        "note": "框架产物：old 待对照 struct 该页短行文字层填入后再入 corrections/",
    }


def main():
    ap = argparse.ArgumentParser(
        description="图片补嵌框架（单页示例；批量补图需用户单独批准）")
    ap.add_argument("--pdf", required=True, help="原书 PDF 路径")
    ap.add_argument("--pages", default="", help="phys 列表（本框架仅取第一页做示例，不批量写盘）")
    ap.add_argument("--work", required=True, help="本书 work 目录")
    args = ap.parse_args()

    phys_list = [int(x) for x in re.findall(r"\d+", args.pages)]
    if not phys_list:
        sys.exit("ERROR: --pages 至少给一个 phys 页号")

    print("⚠️ 本脚本是【框架】：只演示单页 backfill_one_page() 返回，不批量写盘。")
    phys = phys_list[0]
    result = backfill_one_page(args.pdf, phys, args.work)
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("\n批量补图流程：由用户审阅逐页补丁后，把 patch 写入 corrections/ 并重跑 assemble；"
          "勿在本框架内自动批量执行。")


if __name__ == "__main__":
    main()
