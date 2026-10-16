#!/usr/bin/env python3
# 01a_ocr.py —— Stage 1：用 RapidOCR 对 PDF 重 OCR，产出法医底本
#
# 适用场景（两种）：
#   1. 原 PDF 无文字层（纯扫描图像）—— 01a_raw.py 的 get_text() 取不到任何字；
#   2. 原 PDF 文字层质量太差（字形混淆成规模，如「載車↔戰車」「問↔間」「讓↔护」），
#      需要换更强引擎重 OCR，从源头根治，而不是在脏文字层上逐条打补丁。
#
# 产出与 01a_raw.py **完全同构**：fulltext_raw.txt（`<<<PAGE phys=N>>>` + 逐页文本）
# 与 page_index.json。下游直接接 `01b_struct.py --from-raw`，流程不变。
#
# ── 引擎选择（关键！决定成败，2026-09-23 实测立）────────────────────────────
#   --engine v6（默认，推荐）：det=PP-OCRv5_server + rec=PP-OCRv6_medium
#   --engine v5            ：det=PP-OCRv5_server + rec=PP-OCRv5_server
#   --engine v3            ：rapidocr 自带 PP-OCRv3（旧默认，繁体/长行均差，仅作兜底）
#   --engine v5m           ：全 mobile（快，但长行会被截断，只适合短行/预览）
#
#   三条硬经验（换书同样适用）：
#   ① **检测必须用 server 模型**：mobile 检测对小字漏行（整行丢失，比错字更致命）。
#   ② **识别不能用 mobile**：mobile rec 有输出长度上限，书页行宽 ~30+ 字会被截尾
#      （实例：`無法行動`→`無法動`、`再度易手`→`再度易`）。长行书页一律 server/medium。
#   ③ **检测分辨率不能用默认**：默认 `limit_type=min, limit_side_len=736` 会把整页强缩
#      到 736px，小字整行漏检。必须 `--det-limit-type max --det-limit-side-len >=页面长边`
#      （默认 2000，即"原分辨率检测"，无论是否 --scale 放大）。
#
#   模型自动下载：从 ModelScope 的 RapidAI/RapidOCR 仓库取 ONNX 权重（含内嵌字典），
#   缓存到 --model-dir（默认探测序列 $OCR_MODEL_DIR → 技能目录/models → ~/.cache/ocr_models，
#   详见 default_model_dir()，不硬编码任何本机盘符路径）。
#
# 用法：
#   python 01a_ocr.py <book.pdf> <WORK目录> [--engine v6] [--workers 6] [--resume]
#                     [--scale 1.0] [--dpi 300] [--det-limit-type max] [--det-limit-side-len 2000]
#                     [--model-dir DIR] [--min-score 0.0] [--start N] [--limit N]
#
# 依赖：pymupdf、rapidocr-onnxruntime（onnxruntime + opencv + numpy）
# 说明：优先取页面内嵌图像（原生分辨率，快）；无内嵌图则按 --dpi 渲染。
#       逐页结果缓存于 <WORK>/_ocr_cache/p<phys>.txt，--resume 可断点续跑。
#       多进程并行：每进程独立加载引擎与 PDF，--workers 建议 ≤ CPU 核数/2。

import os, json, argparse, time

# ---- 解释器守卫（v1.30.0）：先切到 EBOOK_PY 指定的 venv，再 import 第三方库 ----
# 本脚本的 pymupdf / rapidocr / cv2 / numpy 均为函数内延迟 import，文件头切换即可全覆盖。
# 详见 _interp_guard.py。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

MODELSCOPE_BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/master/"

# 引擎预设：名称 -> (det 相对路径|None 表示用自带, rec 相对路径|None)
ENGINES = {
    "v3":  (None, None),                                                    # 自带 PP-OCRv3
    "v5":  ("onnx/PP-OCRv5/det/ch_PP-OCRv5_det_server.onnx",
            "onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_server.onnx"),
    "v6":  ("onnx/PP-OCRv5/det/ch_PP-OCRv5_det_server.onnx",
            "onnx/PP-OCRv6/rec/PP-OCRv6_rec_medium.onnx"),
    "v5m": ("onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx",
            "onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx"),
}


def default_model_dir():
    # P1-11（2026-10-04 审查）：去掉硬编码本机路径兜底，改探测序列。
    # 优先 OCR_MODEL_DIR → 技能目录/models → ~/.cache/ocr_models；都不存在时落到
    # ~/.cache/ocr_models（由 ensure_model 自动下载权重，避免硬编码本机路径泄漏）。
    _sd = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.environ.get("OCR_MODEL_DIR"),
                 os.path.join(os.path.dirname(_sd), "models"),
                 os.path.join(os.path.expanduser("~"), ".cache", "ocr_models")):
        if cand and os.path.isdir(cand):
            return cand
    return os.environ.get("OCR_MODEL_DIR") or os.path.join(os.path.expanduser("~"), ".cache", "ocr_models")


def ensure_model(model_dir, rel):
    """确保模型存在；缺失则从 ModelScope 下载。返回本地路径。"""
    name = rel.rsplit("/", 1)[-1]
    dst = os.path.join(model_dir, name)
    if os.path.exists(dst) and os.path.getsize(dst) > 1000:
        return dst
    os.makedirs(model_dir, exist_ok=True)
    url = MODELSCOPE_BASE + rel
    print(f"[model] 下载 {name} ...", flush=True)
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        tmp = dst + ".part"
        with urllib.request.urlopen(req, timeout=180) as r, open(tmp, "wb") as w:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                w.write(chunk)
        os.replace(tmp, dst)
    except Exception as e:
        raise SystemExit(f"[model] 下载失败：{name}（{type(e).__name__}）\n"
                         f"        可手动下载 {url}\n"
                         f"        放到 {model_dir}/ 后重跑，或改用 --engine v3（自带模型，质量较低）。")
    print(f"[model] OK {name} {os.path.getsize(dst)} bytes", flush=True)
    return dst


def load_page_image(doc, phys, dpi, scale):
    """返回 BGR ndarray：优先内嵌图（原生分辨率），否则渲染；scale 可放大。"""
    import cv2
    import numpy as np
    page = doc[phys - 1]
    arr = None
    imgs = page.get_images(full=True)
    if imgs:
        parea = abs(page.rect.width * page.rect.height) or 1
        best = None
        for im in imgs:
            try:
                info = page.parent.extract_image(im[0])
            except Exception:
                continue
            w, h = info.get("width", 0), info.get("height", 0)
            if w * h > (best[1] if best else 0):
                best = (info, w * h)
        if best and best[1] >= 0.35 * parea * (72.0 / 96.0):
            info = best[0]
            arr = cv2.imdecode(np.frombuffer(info["image"], np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        pix = page.get_pixmap(dpi=dpi)
        arr = cv2.imdecode(np.frombuffer(pix.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
    if scale and abs(scale - 1.0) > 1e-6:
        arr = cv2.resize(arr, None, fx=scale, fy=scale,
                         interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    return arr


def make_engine(args):
    from rapidocr_onnxruntime import RapidOCR
    kw = {}
    det_rel, rec_rel = ENGINES[args.engine]
    if det_rel:
        kw["det_model_path"] = ensure_model(args.model_dir, det_rel)
        kw["rec_model_path"] = ensure_model(args.model_dir, rec_rel)
    kw["det_limit_type"] = args.det_limit_type
    kw["det_limit_side_len"] = args.det_limit_side_len
    return RapidOCR(**kw)


# 每进程全局（fork/spawn 后重新初始化）
_G = {}


def _init_worker(args):
    import warnings
    warnings.filterwarnings("ignore")
    import pymupdf
    _G["doc"] = pymupdf.open(args.pdf)
    _G["eng"] = make_engine(args)
    _G["args"] = args


def _do_page(phys):
    args = _G["args"]
    cfp = os.path.join(args.cache, f"p{phys}.txt")
    if args.resume and os.path.exists(cfp):
        return phys, open(cfp, encoding="utf-8").read(), 0
    arr = load_page_image(_G["doc"], phys, args.dpi, args.scale)
    res, _el = _G["eng"](arr)
    lines = []
    dropped = 0
    for item in (res or []):
        txt = (item[1] or "").strip()
        if not txt:
            continue
        try:
            score = float(item[2])
        except (IndexError, TypeError, ValueError):
            score = 1.0
        if score < args.min_score:
            dropped += 1
            continue
        lines.append(txt)
    text = "\n".join(lines)
    with open(cfp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return phys, text, dropped


def main():
    ap = argparse.ArgumentParser(description="Stage 1：RapidOCR 重 OCR 产出法医底本")
    ap.add_argument("pdf")
    ap.add_argument("work")
    ap.add_argument("--engine", default="v6", choices=sorted(ENGINES),
                    help="引擎预设（默认 v6：det v5_server + rec v6_medium，推荐）")
    ap.add_argument("--model-dir", default=None, help="ONNX 模型缓存目录")
    ap.add_argument("--det-limit-type", default="max", choices=["max", "min"],
                    help="检测缩放基准（默认 max：保证不被强缩）")
    ap.add_argument("--det-limit-side-len", type=float, default=2000,
                    help="检测边长上限（默认 2000，≥页面长边即原分辨率检测）")
    ap.add_argument("--scale", type=float, default=1.0, help="图像放大倍数（默认 1.0 原生）")
    ap.add_argument("--dpi", type=int, default=300, help="无内嵌图时的渲染 DPI")
    ap.add_argument("--workers", type=int, default=1, help="并行进程数（默认 1）")
    ap.add_argument("--start", type=int, default=1, help="从第 N 物理页开始")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 页（调试用）")
    ap.add_argument("--resume", action="store_true", help="跳过已缓存页")
    ap.add_argument("--min-score", type=float, default=0.0, help="丢弃置信度低于此值的行")
    args = ap.parse_args()

    import pymupdf
    args.model_dir = args.model_dir or default_model_dir()
    os.makedirs(args.work, exist_ok=True)
    args.cache = os.path.join(args.work, "_ocr_cache")
    os.makedirs(args.cache, exist_ok=True)

    doc = pymupdf.open(args.pdf)
    TOTAL = len(doc)
    doc.close()
    end = TOTAL if not args.limit else min(TOTAL, args.start - 1 + args.limit)
    targets = list(range(args.start, end + 1))

    t0 = time.time()
    results = {}
    total_dropped = 0
    if args.workers and args.workers > 1:
        import multiprocessing as mp
        with mp.Pool(args.workers, initializer=_init_worker, initargs=(args,)) as pool:
            done = 0
            for phys, text, d in pool.imap_unordered(_do_page, targets, chunksize=1):
                results[phys] = text
                total_dropped += d
                done += 1
                print(f"[{done}/{len(targets)}] phys={phys} chars={len(text)}", flush=True)
    else:
        _init_worker(args)
        for i, phys in enumerate(targets, 1):
            phys, text, d = _do_page(phys)
            results[phys] = text
            total_dropped += d
            print(f"[{i}/{len(targets)}] phys={phys} chars={len(text)}", flush=True)

    blocks = [f"<<<PAGE phys={p}>>>\n{results[p]}" for p in sorted(results)]
    out = os.path.join(args.work, "fulltext_raw.txt")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(blocks))
    idx = [{"phys": p, "book": None} for p in sorted(results)]
    with open(os.path.join(args.work, "page_index.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)
    print(f"01a_ocr 完成：{len(blocks)} 页 -> {out}"
          f"（engine={args.engine}，{time.time()-t0:.0f}s，原样未剥书眉/页脚）")
    if args.min_score > 0:
        print(f"⚠ 低分丢弃行数：{total_dropped}（min_score={args.min_score}，这些行未进入底本；"
              f"若占比偏高说明引擎对该页置信度低，建议降低 min_score 或换引擎重跑）")


if __name__ == "__main__":
    main()
