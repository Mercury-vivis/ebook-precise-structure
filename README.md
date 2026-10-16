# ebook-precise-structure

> ⭐ 如果这个工具帮到了你，欢迎点个 Star —— 是我继续维护的动力。
> 当前版本：**v1.30.0**

## 安装为 AI 技能

本仓库是一个 **AI 助手技能**（`SKILL.md` + 脚本的标准技能结构，不绑定任何特定产品：WorkBuddy、Claude Code、Codex CLI 等均可用）。把仓库克隆（或下载解压）到所用工具的技能目录，重启或新开会话即可在对话中调用：

```bash
# WorkBuddy（用户级技能目录）——其他 AI 工具同理：放进各自技能目录
git clone git@github.com:Mercury-vivis/ebook-precise-structure.git "%USERPROFILE%\.workbuddy\skills\ebook-precise-structure"
```

- **不用 AI 工具也能用**：`scripts/` 下是独立 Python 脚本，直接命令行运行，见下方「安装」「快速上手」。
- 技能运行还需 Python ≥ 3.9 及分组依赖，见下方「安装」小节。

> 电子书精确梳理（正确性优先）：把一本电子书变成「内容正确、可引用、可检索、可回溯原文」的结构化笔记。

## 这是什么

把一本电子书（PDF / EPUB）变成结构化笔记——章节脉络、人名·战役·概念索引、时间轴表格、引用卡。
核心保证是**内容正确**与**可回溯原文**：每个字要么来自原书字节，要么来自带痕纠错，所有改动可逆、可溯源。

## 支持的输入路径（自动识别）

- **OCR-PDF（有文字层）**：直接读文字层 + 锚点体系。
- **EPUB**：解包提取正文与图版。
- **PaddleOCR 三件套**：文字层缺失 / 劣化时走重 OCR 路径。

## 给谁用

- 需要把一本书沉淀为可引用知识资产的研究者、教师、读书笔记爱好者。
- 对「带页码、回原文核对、OCR 纠错、按章节梳理」有硬性要求的场景。

## 安装

1. 准备 Python ≥ 3.9 及其 venv，并把 `EBOOK_PY` 指向该 venv 的解释器（依赖装在 venv；脚本自带解释器守卫，被客户端/托管解释器唤起时自动切到该 venv，不会把依赖装进随升级蒸发的客户端解释器）。
2. 依赖清单的单一真相源在 `skill-dependencies.json`。用本技能自带的 `scripts/check_environment.py` 自检：

   ```bash
   PY="${EBOOK_PY:-python3}"
   "$PY" scripts/check_environment.py             # 全量自检
   "$PY" scripts/check_environment.py --group epub  # 只查某一组
   ```

3. 按输出提示用 `"$PY" -m pip install <包>` 补齐各组依赖（pdf-core / epub / figure / reocr / optional-tools / stdlib-only）。

## 快速上手

```bash
PY="${EBOOK_PY:-python3}"
"$PY" scripts/00_probe.py <你的书.pdf>               # 探测文字层：有文字层走 01a_raw，纯扫描件走 01a_ocr
# …按 SKILL.md 的 Stage 流程推进…
"$PY" scripts/pipeline_assemble.py --work <WORK目录> --out <交付目录> --meta <book_meta.json>   # PDF 路径组装六件

# 多卷合订本（phys 页 ≥ 3000，或已知是多卷合订）先拆后读：
"$PY" scripts/split_volumes.py <WORK目录> --apply   # 阈值默认 3000；连续编码（book==phys）加 --title-pattern '<卷标题正则>'
```

## 关键机制

- **SEC 锚点体系**：`<<<PAGE phys=N>>>` 等锚点把笔记挂回原书页码，可回溯原文。
- **corrections 台账**：OCR 纠错集中在 `04_改错留痕.md`，`patch_manifest` 索引管理，删除型不物理删。
- **pre-delivery verifier 闸 2**：03 与台账双向机械校验，不一致即拒交付——保证质量。
- **来源四级标签（v1.28）**：陈述标注〔A 原意〕〔B 事实〕〔C 推断〕〔D 未验证〕，`scripts/scan_source_tags.py` 自动核查「推断 / 未验证」是否漏标。
- **引文自动核对（v1.28）**：`scripts/04_check_citations.py --auto` 从笔记抽取引文句，与原书字节（raw∪struct∪clean）比对，三级判定（精确 / 标点差异 / 未能验证），保证引用可追溯。
- **跨书专名字典（v1.28）**：`references/proper_names_cross_book.json` 为单一真相源，`scripts/export_proper_names.py` 从纠错与书元增量抽取、默认 `confirmed=false` 防污染。
- **多卷合订本拆分（v1.29）**：`scripts/split_volumes.py` 在 phys 页 ≥ 3000 自动触发，优先 `book=` 印页重置检测、连续编码回退语义识别卷/册标题；拆分后每卷独立跑现有单书流水线（详见 `SKILL.md` §7），性能问题与正确性双解。
- **图实体随六件落交付目录（v1.29.1）**：03 的 `imgs/p{phys}_*.jpg` 是相对交付目录的路径，交付时把 `work/imgs/` 整目录同步拷入交付目录 `imgs/`，核验口径＝03 引用集合 ⊆ 交付 imgs/ 文件集合（详见 `SKILL.md` §0.3）。

## 依赖

见 `skill-dependencies.json`（分组：pdf-core / epub / figure / reocr / optional-tools / stdlib-only）。

## 协议

[MIT](./LICENSE)。© 2026 老陈 / 帮帮。
