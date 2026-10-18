# EPUB 路径细则（组装踩坑 / 印刷页码回填）

> 本文件展开 `SKILL.md` §3E 的两处细则。锚点体系、`book_meta.json` schema、NCX 节锚细化、
> 组装命令的权威定义仍在 `SKILL.md` §3E，此处不重述。

## 一、组装路径踩坑

- **`book_meta.json` 是 EPUB 的「换算表」**：`chapters[].phys_start` 必须填该篇首个 SEC idx；漏填/错填会让该篇被判「未覆盖」。

- **笔记 `## N.` 的 N 必须是全局篇序**：`split_chapters` 用全局篇序过滤，若按单元写局部编号 → 该篇整篇被跳过，02 概念/名句静默全空。

- **卡片小节标题必须是「独立粗体行」、内容另起行**：`SUBSEC_RE` 只认行尾即结束的独立粗体行。写成 `- **X**：` 或 `**X**：内容` 都匹配失败 → 卡片漏抽。

## 二、印刷页码回填

`01b_extract_epub.py` 已探测印刷页码锚并写入 `page_index.json`。把探到的 `print_page` 回填进 `fulltext_struct.txt` 锚的 `book` 字段。护栏与约束：

- **仅在真实探到 print_page（正 int>0）时启用**；否则 `book` 回退=SEC idx。

- **`phys` 始终=SEC idx 不变**——回填 `book` 不影响任何匹配逻辑。

- **`book_meta.json` 的 `chapters[].phys_start` 仍填 SEC idx**——印页信息由 03 锚的 `book` 字段承载。
