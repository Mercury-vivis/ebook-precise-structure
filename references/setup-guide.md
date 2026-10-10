# 环境安装指南（ebook-precise-structure）

依赖清单的单一真相源是技能包根的 **`skill-dependencies.json`**。本文件只给安装动作与踩坑说明；改依赖版本/分组请改 json，不要改本文件里的包名列表。

---

## 1 先自检，缺什么装什么

```bash
PY="${EBOOK_PY:-python3}"
"$PY" scripts/check_environment.py            # 全量自检
"$PY" scripts/check_environment.py --group epub   # 只查某一组
"$PY" scripts/check_environment.py --json         # 机器可读
```

退出码：`0` 必需齐备（可选缺失只告警）／`1` 有必需缺失／`2` 声明文件缺失或不可解析。
开局前跑一遍，比跑到 Stage 3 才发现 `ebooklib` 没装要省事得多。

---

## 2 Python 解释器（🔴 硬约束）

- 一律用 **本机自装 Python 及其派生 venv**（例如 `python3` 或你自己建的 venv 解释器路径）。
- **禁用 WorkBuddy 自带解释器**：它随客户端升级整体重建，已装依赖会蒸发，且不是真实运行环境。
- 脚本里统一这样取：

```bash
PY="${EBOOK_PY:-python3}"
```

---

## 3 建 venv 并分组安装

```bash
# 建环境
python3 -m venv <你的-venv-路径>
PY="<你的-venv-路径>/Scripts/python.exe"

# ① PDF 核心路径（PDF 书必装）
"$PY" -m pip install pymupdf

# ② EPUB 路径（EPUB 书必装）
"$PY" -m pip install ebooklib beautifulsoup4

# ③ 图版工具链（走图版书必装）
"$PY" -m pip install pymupdf opencv-python-headless numpy

# ④ 重 OCR 路径（仅文字层缺失/劣化时用）
"$PY" -m pip install rapidocr-onnxruntime opencv-python-headless numpy

# ⑤ 可选工具（缺则对应功能降级跳过）
"$PY" -m pip install opencc-python-reimplemented pdfplumber
```

> `fitz` 与 `pymupdf` 是 **同一包 PyMuPDF** 的新旧两个 import 名，本技能里两种写法并存（老脚本写 `import fitz`，新脚本写 `import pymupdf`）。装 `pymupdf` 即可，1.24+ 两个名都可用。

---

## 4 踩坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `UnicodeEncodeError` / 中文变问号 | 用 WorkBuddy 自带解释器，或系统默认 GBK 编码路径 | 换 §2 的解释器；本技能写文件一律 `encoding="utf-8", newline="\n"` |
| 交付件行尾变 CRLF | 中途用记事本/Excel 另存过 | 交付文本统一 LF；读取走 `read_text_cn`（utf-8-sig 容错） |
| `01a_ocr.py` 首次跑很慢/要联网 | 从 ModelScope 下载 ONNX 模型权重到本地缓存 | 正常。权重数据非代码，非「下载即执行」；离线环境改走文字层路径或预置缓存 |
| 重跑 pip 后依赖又没了 | 装进了 WorkBuddy 自带解释器 | 确认 `pip` 与 `python` 是同一个 venv（`"${PY}" -m pip`，不要裸 `pip`） |
