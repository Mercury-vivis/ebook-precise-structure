# 图版嵌入工具链（§6.4 详注）

> 本文是 SKILL.md §6.4 的展开，梳理 06/07/08 系列脚本的分工、调用顺序、Tier 分流与常见坑。
> 适用对象：需要给电子书插图版的执行者。SKILL.md 主表是速查，本文是操作手册。

---

## 一、总览：三条路径，两种图型

| 路径 | 图从哪来 | 整页图版页 | 正文内嵌图版 |
|------|---------|-----------|-------------|
| **路径一：OCR-PDF** | PDF 页渲染提图 | `06_embed_pdf_figures.py` | `07_` 链（probe→classify→embed） |
| **路径二：EPUB** | EPUB zip 内 `<img>` | `06b_embed_epub_figures.py`（图号通道）／`06c_embed_epub_slice_figs.py`（切片占位通道） | —（EPUB 走 06b/06c） |
| **路径三：PaddleOCR 三件套** | book_ocr 已裁好 `imgs/p{phys}_{label}_{n}.jpg` | **跳过 06/07**，复用裁图 | **跳过 07_**，复用裁图 |

**两种图型的本质区别**：
- **整页图版页**：整页就是一张图（地图/作战示意图），文字层只有散字碎片，图号在文字层（如「附图1」）。→ `06_` 系列。
- **正文内嵌图版**：图与正文同页，图只占页面一块，图题在页面文字层（如「图2 德军反击作战」），需按 bbox 裁切。→ `07_` 链（仅 PDF 路径）。

---

## 二、通用纪律（所有图版脚本共守）

1. **纯插入、零改写**：不删不改原页任何散字，只在锚后插图片引用行。散字保留供检索。
2. **必须在 assemble 生成 03 之后跑**：assemble 整体重生成会把 03 重置为无图版。顺序固定为 **assemble → 嵌图**，每次重跑 assemble 都要重嵌（脚本幂等，重嵌安全）。
3. **默认 dry-run，`--apply` 才落盘**：不加 `--apply` 只打印计划，防止误改。
4. **幂等**：03 中已引用同名图即跳过，不会重复嵌。
5. **图落子目录**：06/06b 落 `fig/`，06c 落 `imgs/`，07c 落 `fig/`。**不交图目录＝03 图全部断链**——交付时必须把 `fig/` 或 `imgs/` 整目录同步到交付目录。
6. **改前自动备份**：`--apply` 时先备份 03 为 `*.bak-embed-*`，LF 写盘。

---

## 三、路径一：OCR-PDF

### 3.1 整页图版页 → `06_embed_pdf_figures.py`

**适用**：书末附图、地图页——整页即图，图号「附图N」在文字层。

```bash
python scripts/06_embed_pdf_figures.py \
    --pdf <原书.pdf> \
    --md <03_带页码全文.md> \
    --pages 516-524 \          # 图版页物理页号，支持 "516-524" 或 "516,520,524"
    [--out-dir <目录>]          # 默认 = <03 所在目录>/fig
    [--width 1300]              # 输出图宽 px
    [--quality 85]              # JPEG 质量
    [--num-label 附图]          # 图号前缀，用于生成题注
    [--apply]                   # 不加为 dry-run
```

**输出**：
- `<out-dir>/fig_<num>.jpeg`
- 03 每个图版页锚 `<<<PAGE phys=N book=M>>>` 后插入 `![<num-label><num> <图题>](fig/fig_<num>.jpeg)`

**坑**：`--pages` 必填——本脚本不自动扫描哪些页是图版页。自动扫描见 3.2 的 07_probe（但那是正文内嵌图版的探测，整页图版页仍需人工给页范围或从目录页识别）。

### 3.2 正文内嵌图版 → `07_` 链（四步）

```
07_probe_page_figures.py   粗探：哪几页有图 + 大致矩形
       ↓
07e_figcrop_refine.py      精修裁切（OCR 文字层当掩膜）—— 旁路可选
       ↓                   （07d_figcrop_core.py 是精裁核心，被 07e 动态加载）
07b_classify_figures.py    判别：线描地图/示意图 vs 实地照片
       ↓
07c_embed_figures.py       嵌入 03（只嵌线描型且面积足够的图幅）
```

#### 第一步：`07_probe_page_figures.py` — 粗探

**为什么不能用常规办法**：整页扫描型 PDF 每页只 1 张图（`get_image_info()` bbox 即整页），矢量层恒空（`get_drawings()`），「页面积占比≥35%」全命中，「低文字密度」把文章尾页也误报。

**判据**：
- **页级（文字层，廉价）**：图题行——①以「图」结尾（…示意图/地形图），长度 6–32、无句末标点；②以「图+数字」开头（「图2接敌运动」），长度 4–32、剔正文引用（「图1所示」）。共用：行宽 < 页宽×0.6、非目录条目。
- **图幅级（像素层，仅候选页）**：分块暗率连通域，取面积前 K 个为图幅外接矩形。
- **无图题回退**（`--dense-fallback auto`，默认）：图题命中页 < 3 时，对未命中页做大块图区扫描补出「图题嵌在图内/无图题」的图版页。
- **人工矩形**（`--rect-json`）：自动探不到的图，人工给页号+矩形直接采用。

```bash
python scripts/07_probe_page_figures.py \
    --pdf <原书.pdf> \
    --out <输出目录> \
    [--text-source pdf|ocr|auto]   # 自带层好用 pdf；重 OCR 过用 ocr
    [--ocr-text <struct.txt>]      # --text-source ocr 时的结构化文本
    [--dense-fallback auto|on|off]
    [--rect-json <rects.json>]     # 人工矩形回灌
    [--overlay]                    # 叠框整页图，供人工核验
    [--anchor-md <03文件>]          # 把 phys 映射到 03 锚
```

**输出**：
- `probe_figures.json` — 机器可读清单（含 caption_source / dense / rect）
- `probe_report.md` — 人读报告
- `probe_review.md` — 低置信待人工核验清单（无图幅/回退命中/方向 unknown/近整页/图题多于图幅）
- `fig_pNNNN_k.png` — 图幅裁剪图（k 从 1 起，按面积降序）
- `full_pNNNN.png` — 整页图（供看版式）

#### 第二步（旁路可选）：`07e_figcrop_refine.py` — 精修裁切

> **当前状态：旁路可选，未完全接线。** 产物 `figcrop_refined.json` 无自动消费者，且裁图命名 `figNN_pNNN.png` 与 07b 期望的 `fig_pNNNN_k.png` 不一致。默认流程可跳过本步，直接用 07_probe 的粗矩形。

**原理**：用 OCR 文字层当掩膜，把「页面文字」与「图内标注」分开——正文长行/段落行/标题行/图题行/页边距行遮蔽掉，剩下的暗像素才是图墨。再做种子定位→链式生长→回纳标注→净空自检→硬约束（框底边不越过图题上沿）。

**核心模块 `07d_figcrop_core.py`**：定义文字分类（hard/body/title/cap/marg）、遮蔽策略、图墨计算、定位与净空自检。被 07e 以 importlib 动态加载。

**裁切代价不对称**：少裁（多留白）只是难看，多裁（切掉图角/标注）是错。故默认偏保守（净空阈值低、外扩上限 30pt、四边加 2pt 余量）。

```bash
python scripts/07e_figcrop_refine.py \
    --pdf <原书.pdf> \
    --in <probe_figures.json 或 候选.json> \
    --out <目录> \
    [--dpi 220] [--cut-dir <fig目录>] [--prefix fig]
```

#### 第三步：`07b_classify_figures.py` — 图版性质判别

**为什么需要**：07_probe 探出的候选图版，图题都叫「XX图」，但其中一部分是**实地照片**（关城、长城一角、碉堡）。照片不该嵌进 03（嵌了也无检索价值，且占体积），只嵌线描地图/示意图。

**判别原理**（纯 Python，无 numpy/cv2，对裁切 PNG 直接计算）：
- 线描地图/示意图：白纸+墨线 → 白像素占比高、熵低、边缘锐利、有长墨线
- 实地照片（网点印）：连续调灰 → 白像素占比低、熵高、边缘过渡软、无长墨线

| 特征 | 含义 |
|------|------|
| white | 灰度≥220 像素占比 |
| ent | 256 桶直方图香农熵/8（0~1） |
| sharp | 水平梯度≥60 占比（锐边） |
| mid | 8≤梯度<60 占比（软过渡） |
| lrun | 含长暗游程的行占比（长墨线，阈值=max(30px, 行宽×12%)） |

**判定**：`photo` = white<0.45 或（ent≥0.55 且 mid>sharp）；其余为 `line`。

```bash
python scripts/07b_classify_figures.py \
    --probe <probe_figures.json> \
    --figdir <裁切图目录> \
    --out <figure_classify.json> \
    [--check]   # 输出人判真值对照（内置 16 张校准集）
```

#### 第四步：`07c_embed_figures.py` — 嵌入 03

**只嵌**：`verdict==line` 且图幅面积 ≥ 页面 `--min-area` 比例的图幅。photo 与碎块一律不嵌。

**做什么**：
- 按 bbox 从 PDF 渲染裁切（灰度 `--dpi`）
- 若该页注记判定「旋转90°」，裁切后顺时针转正
- 存 `<out-dir>/fig_pNNNN_k.png`
- 在 03 对应锚 `<<<PAGE phys=N book=M>>>` 之后插入 `![图题（原书页M）](fig/fig_pNNNN_k.png)`

```bash
python scripts/07c_embed_figures.py \
    --pdf <原书.pdf> \
    --md <03.md> \
    --probe <probe_figures.json> \
    --classify <figure_classify.json> \
    [--min-area 0.15]   # 图幅面积占页面最小比例
    [--dpi 200]
    [--apply]           # 不加为 dry-run
```

---

## 四、路径二：EPUB

EPUB 无印刷页，图从 EPUB zip 内 `<img>` 提取。两个通道互补，按 03 文本形态选择：

### 4.1 图号通道 → `06b_embed_epub_figures.py`

**适用**：03 文本里存有「图N/地图N」caption 行（〈西周的灭亡〉型）。

**匹配体系**（权威键是「图题」不是 img 顺序）：
- EPUB 侧注册（同号多候选按分值取高）：
  - 3 分：img 自带 id（figN/mapN/figureN）
  - 2 分：无 id 但窗口内有 `<p class="tuti">` 图题
  - 1 分：纯窗口就近命中「图N/地图N」（兜底）
- 03 侧插入点（三级优先，根治「插图落目次」）：
  - 正文 caption 行（行首「图N 题」且不在目次区）> 正文引用（（见图N））> 目次行兜底
- 目次区检测：同一 SEC 锚区间内行首图号行 ≥ 密度阈值判目次区

```bash
python scripts/06b_embed_epub_figures.py <原书.epub> <03.md> [--apply]
```

**输出**：原图复制到 03 同层 `fig/`，03 插入点后插入 `![图题](fig/fig_<编号或题名>.jpeg)`。

### 4.2 切片型/占位对齐通道 → `06c_embed_epub_slice_figs.py`

**适用**：切片型 EPUB——版面含海量小图元（脚注星号、生僻字 svg 字形图），03 里所有 `<img>` 都坍缩为同一占位符 `〔原书图字·图片，文字层不可提取〕`，无图号可依。（中华书局 2025 版 EPUB 为典型：3101 个 `<img>` 只有 132 张真图版。）

**机制（占位对齐）**：
1. 按 spine 序解析 EPUB，逐单元数 `<img>`；与 fulltext_struct 各 SEC 锚区间的占位符数逐一核对——**必须全等**，不等则硬失败（不静默错位）。
2. 真图版判据：src 以 .jpg/.jpeg 结尾且 zip 内体积 ≥ `--min-size`（默认 30000B）。小图元不嵌入，占位符原样保留。
3. 图题：取 img 前最近的 `<p>` 段落文本（剥标签/装饰），≤60 字；「续表」沿用上一真题+「（续）」；无题回退「图版」。
4. 图片复制到 `<md 所在目录>/imgs/`，文件名 `fig_<phys>_<单元内序>.<ext>`；03 内占位符原位替换为 `![题](imgs/fig_….jpg)`。

```bash
python scripts/06c_embed_epub_slice_figs.py <原书.epub> <03.md> \
    [--min-size 30000] [--apply]
```

---

## 五、路径三：PaddleOCR 三件套（Tier1）—— 跳过 06/07

book_ocr 三件套**本身已含图版**：`<名>.md` 里的 `imgs/p{phys}_{label}_{n}.jpg`（image 类）+ 结构化 `<table>`（table 类）均带 phys 定位，经 Stage 3 → assemble → 03 链路**自然保留**（assemble 不碰图，只转文本）。

**Tier1 纪律**：
- **默认不跑 06/07**——与 book_ocr 裁图完全重叠，误跑会导致重复嵌图。
- Stage 3 不得删 `imgs/` 引用。
- assemble 后核验：`grep -c 'imgs/p[0-9]' 03` 确认图引用进入 03。
- **图实体必须落交付目录**：03 里的 `imgs/p{phys}_*.jpg` 是相对 03 所在交付目录的路径，`00_ingest_bookocr.py` 只把源 imgs/ 同步到 `work/imgs/`（加工层），assemble 不碰图——只交五件 md 不交 imgs/＝03 图全部断链。交付时把 `work/imgs/` 整目录同步拷入交付目录。
- 核验口径：「03 引用集合 ⊆ 交付 imgs/ 文件集合」（只数引用条数验不出断链）。

---

## 六、`08_backfill_figures.py` — 补嵌框架（非成品）

> ⚠️ 这是**框架**，不是可直接批量跑的成品。`main()` 仅做参数解析与**单页**调用示例；批量执行需用户单独批准后另行驱动。

**用途**：把 `00d_scan_figure_gaps.py` 标出的「疑似图片文字层」页，从原 PDF 回渲整页/图块，补成 `imgs/p{phys}_backfill.jpg`，并生成 silent 补丁（图片+图题替换短行文字层，图外正文保留）。补丁形态与 `corrections/*.json` 一致（`{"phys","old","new","silent":true}`）。

**依赖**：pdfplumber（函数内延迟 import，未装时框架仍可 import 与审阅，单页函数调用时才报缺依赖）。

```bash
python scripts/08_backfill_figures.py --pdf <原书.pdf> --pages 406 --work <work目录>
# 只打印 backfill_one_page() 的返回补丁 dict，不写盘、不改 struct
```

---

## 七、常见坑速查

| 坑 | 现象 | 解法 |
|----|------|------|
| assemble 重跑后图没了 | assemble 重置 03 为无图版 | 顺序固定 assemble→嵌图，每次重跑 assemble 都重嵌（幂等） |
| 03 图引用断链 | 只交五件 md 没交 fig/imgs/ | 交付时把 fig/ 或 imgs/ 整目录同步到交付目录 |
| 07_probe 探不到图 | 图题嵌在图内、无文字层图题 | `--dense-fallback auto`（默认）或 `--rect-json` 人工矩形 |
| 07c 嵌了照片 | 07b 判别为 photo 但面积大 | photo 一律不嵌，是设计；若确需嵌照片需改 07c 过滤逻辑 |
| 06b 插图落目次 | 目次区密度阈值没触发 | 检查 03 目次区是否图号行密集；可调 TOC_DENSE（源码内常量） |
| 06c 占位符数不等 | EPUB img 数与 03 占位符数对不上 | 硬失败是故意的——检查 01b_extract_epub 是否漏了某些 SEC 单元 |
| Tier1 重复嵌图 | 跑了 book_ocr 又跑了 07_ | Tier1 默认不跑 06/07；已跑则需手动去重 |
| 07e 精裁图 07b 读不到 | 命名不一致（figNN_pNNN.png vs fig_pNNNN_k.png） | 07e 当前未完全接线，默认流程跳过精裁直接用 07_probe 粗矩形 |

---

## 八、脚本速查表

| 脚本 | 路径 | 图型 | 阶段 | 依赖 |
|------|------|------|------|------|
| `06_embed_pdf_figures.py` | PDF | 整页图版页 | 嵌入 | pymupdf |
| `06b_embed_epub_figures.py` | EPUB | 整页图版（图号通道） | 嵌入 | 标准库（zipfile） |
| `06c_embed_epub_slice_figs.py` | EPUB | 整页图版（切片占位通道） | 嵌入 | 标准库（zipfile） |
| `07_probe_page_figures.py` | PDF | 正文内嵌图版 | 探测 | pymupdf |
| `07e_figcrop_refine.py` | PDF | 正文内嵌图版 | 精裁（旁路） | pymupdf + numpy |
| `07d_figcrop_core.py` | PDF | 正文内嵌图版 | 精裁核心（被07e加载） | pymupdf + numpy |
| `07b_classify_figures.py` | PDF | 正文内嵌图版 | 判别 | 标准库（纯Python像素计算） |
| `07c_embed_figures.py` | PDF | 正文内嵌图版 | 嵌入 | pymupdf |
| `08_backfill_figures.py` | PDF | 缺口页补嵌 | 框架（非成品） | pdfplumber（延迟） |
| `_fig_common.py` | 共用 | — | 共享工具 | — |
