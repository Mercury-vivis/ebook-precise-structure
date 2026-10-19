# ebook-precise-structure v2.0.0 发布说明

> 版本：2.0.0 ｜ 协议：[MIT](./LICENSE) ｜ © 2026 老陈 / 帮帮

## 这个版本是什么

把一本电子书（PDF / EPUB）变成**内容正确、可引用、可检索、可回溯原文**的结构化笔记——章节脉络、人名·战役·概念索引、时间轴表格、引用卡。核心保证：每个字要么来自原书字节，要么来自带痕纠错；所有改动可逆、可溯源。

## 核心能力

- **三入口自动识别**（v2.0.0 落地）：OCR-PDF（有文字层）/ EPUB / PaddleOCR 三件套，路径自动识别，无需手工指定；统一 SEC 锚点体系（`<<<PAGE phys=N>>>`）。
- **交付五件（00–04）**：补丁 `corrections` 台账 + `patch_manifest` 索引（删除型标 `deleted`，不物理删，原字节保留可追溯）+ `04_改错留痕.md`（台账主源，diff 兜底）。
- **两道闸（闸A / 闸B）**：`check_book.py` 是唯一阻断点。
  - **闸A 可读性抽检**：随机抽页扫混淆词典，输出「phys + 疑似片段」清单交人核。
  - **闸B 书账互释**：03 改动 ↔ 04 记录双向对得上；不一致即拒交付、不自动重算。
- **来源四级标签**：陈述强制标注〔A 原意〕〔B 事实〕〔C 推断〕〔D 未验证〕，`scan_source_tags.py` 自动核查漏标。
- **引文自动核对（C 口径）**：`04_check_citations.py --auto` 只核笔记「关键原句摘录」小节（笔记里唯一声称「原书原话」的地方），与原书底本（raw∪clean）归一化全文子串匹配，四类分栏（✅03 逐字 / ⚠️仅 raw / ⚠️03 疑似残留 / ❌均无）。与两路径 assemble 的引文逐字率**同源同函数**；四类均不阻断、须人核，产出 `work/citation_report.md`。
- **修订路径**：已交付且人已校对的书，书为真相源、定点改、04 留痕、禁止重算。
- **多卷合订本拆分**：`split_volumes.py` 在 phys 页 ≥ 3000 自动触发，拆分后每卷独立跑单书流水线。
- **跨书专名字典**：`references/proper_names_cross_book.json` 单一真相源，增量抽取默认 `confirmed=false` 防污染。

## v2.0.0 阶段三收尾（本次一并纳入）

- **引文逐字率口径统一为 C 口径**：只核「关键原句摘录」，与 assemble 的 gate4 同源同函数；旧的全篇引号扫描（`--all-quotes`）仍可用但改为显式选项、默认不启用。
- **`gen_reading_report.py` §5.7 去 `≥95%` PASS 阈值**：改为 C 口径如实报告（四类均不阻断、须人核，明细见 `work/citation_report.md`）。
- **测试加固**：`01a_ocr` 抽样命名逻辑拆为无守卫纯模块 `_ocr_partial_naming`，补 `test_01a_ocr_partial`（16 断言全过；全量 23 个测试文件无回归）。

## 安装

见 [README.md](./README.md)「安装」「快速上手」小节。需要 Python ≥ 3.9 及分组依赖（单一真相源 `skill-dependencies.json`，用 `scripts/check_environment.py` 自检）。

## 版本沿革要点

- **v1.35.0**：交付件五件化 + 闸体系收敛为两道。
- **v2.0.0**：三入口落地（OCR-PDF / EPUB / PaddleOCR 路径自动识别），旧闸链退出阻断位；阶段三完成引文 C 口径统一与测试加固。
