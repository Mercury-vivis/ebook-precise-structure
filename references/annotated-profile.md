# 注本/校点本 EPUB 语义抽取 profile（01b_annotated.py）

> 引擎：`scripts/01b_annotated.py`。本文只讲 profile 怎么写，不讲脚本实现。
> 通用 EPUB（无注本结构）仍用 `01b_extract_epub.py`，不进本流程。

## 0. 什么时候必须用本流程

底本 HTML 把**注、校记、内条序号**做成了独立 `class`（或独立属性），而正文与注文在版面上
是「大字夹小字」——一旦按块压平，注文就并进正文。判据只有一条：

```bash
python 01b_annotated.py <book.epub> <WORK> --dump-classes
```

普查结果里若有「数量大（万级/千级）+ 中位长度明显短于正文段 + 文本为训诂/音义/校语」
的 class，就是注或校记，必须走本流程。

**为什么不能压平**：注本引文一旦把胡三省注当《通鉴》原文引用，属史学引用的硬错误
（注 ≠ 正文 ≠ 校记）。且压平后注文无法检索、无法与正文分栏排版。

## 1. profile 文件放哪

profile 是**书级数据**，不进技能库：放该书加工目录 `<WORK>/_profile_annotated.json`。
（技能库只存脚本与本文；书级 class 名千书千样，塞进技能会变成一堆死配置。）

## 2. 最小可用示例

```json
{
  "unit_label": "卷{no:03d}",
  "unit_doc_re": "<title>某书/卷(\\d+)",
  "unit_doc_max_bytes": 1200,
  "classes": {
    "title":        ["title"],
    "emperor":      ["emperor"],
    "reign_title":  ["reign-title"],
    "inline_note":  ["note", "note9"],
    "block_note":   ["note2"],
    "collation":    ["note5", "note8"],
    "index_number": ["number"],
    "imprint":      ["note3"]
  },
  "imprint_re": "电子版雠覆|标点|覆校",
  "title_fix":  {"59": "汉纪五十一", "279": "后唐纪八"},
  "front_docs": {"text/part0000.html": "书名页"},
  "merge_docs": [{"doc_re": "text/part000[3-9]\\.html", "title": "校勘述略"}]
}
```

## 3. 键位说明

| 键 | 必填 | 作用 | 判错后果 |
| --- | --- | --- | --- |
| `classes` | ✅ | 六类语义的 class 名（数组，可多值） | 缺 → 硬失败；填错 → 注入正文（静默改书） |
| `classes.title` | 否 | 卷题块（`<h2>` 之外还有 `<span class=title>` 的书要填） | 卷题识别不到 → 卷首散行被当正文 |
| `classes.emperor` | 否 | 帝王行（成 SEC 边界，加粗） | 不填 → 帝王与纪年混行，锚无法定位到帝 |
| `classes.reign_title` | 否 | 纪年行（成 SEC 边界，`###`） | 不填 → 全书只剩卷级锚，一年内数百条无法定位 |
| `classes.inline_note` | 否 | 行内注（注码原位加 〖注〗） | 不填 → 注入正文 |
| `classes.block_note` | 否 | 成段注（整段包 〖注〗） | 同上 |
| `classes.collation` | 否 | 校记（〖校〗） | 不填 → 校记入正文，与正文混淆 |
| `classes.index_number` | 否 | 内条序号（转 `〔N〕`） | 不填 → 退化为裸数字粘句首（`1初命晋大夫…`），不可检索 |
| `classes.imprint` | 否 | 电子版版记行（雠覆/标点/覆校） | 不填 → 版记混入卷末正文 |
| `imprint_re` | 否 | 版记行的文本判据（正则） | 不填＝该类整行全剔（风险：误剔正文） |
| `unit_label` | 否 | SEC 命名模板，默认 `卷{no:03d}` | — |
| `unit_doc_re` | 否 | 从文档 `<title>` 抓单元号（捕获组 1） | 不填 → 全书成一节 |
| `unit_doc_max_bytes` | 否 | 小于该字节数的「单元文档」判为题衔页，套话不入正文 | 不填 → 题衔页套话（编者/出版行）混入卷首 |
| `title_fix` | 否 | 底本卷题**自身**错字/脱字修正（号→正题） | 不填 → 目录与锚永久带错字 |
| `front_docs` | 否 | 前辅文文档 → 节名 | 未登记 → 该文档整篇丢弃并计数告警 |
| `merge_docs` | 否 | 多个连续文档并成一节（正则→节名） | 同上 |

## 4. 三条硬规矩

1. **不做隐式猜测**。脚本不会「看着像注就当注」——class 名必须显式声明。理由：语义分流
   猜错＝静默改书，比少标更糟（`零静默改动` 铁律）。
2. **不静默丢弃**。未登记的文档会进 `annotated_report.md` 的「未归类」计数并在 stderr 点名；
   `版记剔除` 逐处计数入报告，供 00/04 引用。
3. **卷题修正只改卷题，不改正文**。`title_fix` 只作用于锚 title 与卷首题名；正文里的同一
   错字由 `corrections/*.json` 补丁处理（标题层补丁够不着锚属性，两条通道各管一层）。

## 5. 产出与下游

- `fulltext_struct.txt`：`<<<PAGE phys=N book=N title=…>>>`，直接喂 `assemble_epub.py`。
- `annotated_report.md`：六项分流计数，写 00「底本与抽取口径」、04「预处理处置登记」时引用。
- 不产出 `chapters/`：按卷切章由 `pipeline_split.py <WORK> book_meta.json` 统一生成
  （唯一来源，避免两处切分口径不一致）。

## 6. 已验证案例

`资治通鉴_司马光`（胡三省注·邹寰宇校注，604 文档 / 294 卷）：

| 分流项 | 处数 |
| --- | --- |
| 行内注 `span.note` | 93,046 |
| 成段注 `p.note2` | 17 |
| 校记 `span.note5/note8` | 2,650 |
| 内条序号 `span.number` | 21,579 |
| 版记剔除 `p.note3` | 66 处（其中 34 卷卷末各 3 行，其余在题衔页，一并不入正文） |
| 漏标注 `span.note9` | 8（并入行内注） |
| 印章图 `img.ytz` 不计占位 | 637（`img_marker:false`；开着会在正文留 637 条「图片不可提取」） |
| SEC | 2,071（卷 294 + 帝王/纪年 1,777） |

## 7. 抽完之后：03 装法与这条链路的关系

本书 03 单件近 1,900 万字节，交付时用 `assemble_epub.py --split03` 按 `book_meta.books`
的 SEC 区间切成 16 册（对应 16 纪），`03_带页码全文.md` 退为索引页。

与抽取器的关系只有一条要记住：**分册按 `phys` 区间切，所以 `phys` 必须连续、无洞**。
本脚本的 `phys` 就是 SEC 序号（1..N 顺排），天然满足；若某书用 `02_ncx_split_epub.py`
重排过 SEC，须在重排之后再切分（否则区间与分册错位，闸B 会报大面积「真未登记」）。

