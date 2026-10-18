# 多卷合订本处理（split_volumes.py）

> 本文件是 `SKILL.md` §7 的全文展开。**触发条件**：输入的是一本合并 PDF（各卷/册合订），
> 且 `fulltext_struct.txt` 的 phys 页总数 > 3000（默认阈值）。

> **判定前提（用户裁定 2026-10-15）**：多卷合订本被当成「一本巨书」喂入，是本技能唯一已知的
> 性能/可用性崩溃模式（实测 IndianPeople_Majumdar 11 卷合并 14843 页卡死；≤1135 页合并本可跑动）。
> **两类形态，只处理前者**：① 一本合并 PDF（各卷/册合订）→ 走本节拆分器；
> ② 已分卷 PDF（每卷独立成册）→ 直接按单本跑、各成独立文件夹，**不走本流程**。

## 7.1 规范模型：马克思混合模型（定为规范）

每卷 = 一本普通书，按现有单书流水线产出完整五件 **00_梳理总览与目录对照 / 01_章节详解 / 02_速查卡 / 03_带页码全文 / 04_改错留痕**；
整套另合成一份**总 00**（统一目录对照 + 成书过程），汇总各卷的页码体系与遗留问题。

- 即「每卷完整 00–04」再加一套级包装——00 若也 per 卷拆碎，既重复又失总览。
- 01–04 本质都是 per 卷的（04 由台账 + diff 兜底生成、02/01 跟 notes 每卷走），无法「共享成一份」而不丢每卷保真。

## 7.2 拆分器 split_volumes.py

- **触发**：`fulltext_struct.txt` 的 phys 页总数 > `--threshold`（默认 **3000**）→ 判定多卷合订，需拆。
- **边界检测（高置信优先）**：
  1. **显式 spec**（最可靠）：`--volumes spec.json` 或 `book_meta.json` 的 `"volumes"` 字段 `[{no,title,phys_start,phys_end}]`。
  2. **book= 印页重置（自动）**：扫描 `<<<PAGE phys=N book=M>>>`，当 book 由大值跌回低值（≤100，典型新卷起印页=1）→ 新卷起点。适用于「各卷印页独立、phys 连续」的合订本；**连续编码（book==phys 单调增）不会误触发**。
  3. **语义识别（连续编码回退）**：扫描正文卷/册标题（默认 Volume/Book/Part + 中文 卷/册，可用 `--title-pattern` 追加书专属正则，捕获组1=卷序号）。⚠ 易误命中（如 TOC 引用他卷），故 `--apply` 时需再带 `--confirm` 才落盘。
- **切片**：`volumes/V{NN}_{slug}/` 下生成 fulltext_struct/raw/clean 三件套切片（**绝对 phys 保留、不重基**，保全源映射）+ 卷级 `book_meta.json` + 整套 `_manifest.json`（供 00/05 合成）。
- **非破坏**：原合并件始终保留；默认 `--dry-run` 只预览，加 `--apply` 才落盘。

```bash
# 预览（自动判定边界，不落盘）
python scripts/split_volumes.py <WORK>
# 连续编码书（如 Indian）：书专属标题正则 + 语义路径需 --confirm
python scripts/split_volumes.py <WORK> --title-pattern "History and Culture of the Indian People, Volume (\d+)" --apply --confirm
# 已确知卷边界：显式 spec
python scripts/split_volumes.py <WORK> --volumes vol_spec.json --apply
```

## 7.3 拆分后流程（下游，当前手工/半自动）

- 各 `volumes/V{NN}/` 当作独立工作区，跑现有单书流水线（Stage 0–6）→ 产出该卷 00–05。
- 整套 00/05 由主控在收口阶段聚合各卷 04 摘要生成（00 统一目录对照、05 精读汇报）。**整套级 00/05 合成器为后续待建项，当前按马克思样本手工完成。**
- 实拆验证（IndianPeople_Majumdar）：11 卷工作子目录已生成（phys 2–812 / 813–2003 / … / 13149–14842），页覆盖零篡改；源既有 2 重复页（1659/11659）+ 2 缺失页（如 1958）为 OCR 缺陷，如实保留。
