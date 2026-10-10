# ebook-precise-structure

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

1. 准备 Python ≥ 3.9 及其 venv（**不要用编辑器 / 客户端自带解释器**——它会被整体重建，已装依赖会蒸发）。
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
"$PY" scripts/00_ingest_bookocr.py <你的书.pdf>   # 入书
# …按 SKILL.md 的 Stage 流程推进…
"$PY" scripts/assemble.py <WORK目录>              # 组装三件套
```

## 关键机制

- **SEC 锚点体系**：`<<<PAGE phys=N>>>` 等锚点把笔记挂回原书页码，可回溯原文。
- **corrections 台账**：OCR 纠错集中在 `04_改错留痕.md`，`patch_manifest` 索引管理，删除型不物理删。
- **pre-delivery verifier 闸 2**：03 与台账双向机械校验，不一致即拒交付——保证质量。

## 依赖

见 `skill-dependencies.json`（分组：pdf-core / epub / figure / reocr / optional-tools / stdlib-only）。

## 协议

[MIT](./LICENSE)。© 2026 老陈 / 帮帮。
