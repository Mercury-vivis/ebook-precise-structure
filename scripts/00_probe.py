# Step 0 格式识别与文本层判断
# 用法：python 00_probe.py <book.pdf>
#
# P0 #5（2026-09-12）：原代码把用户私人书籍的绝对路径写死为「无参回退值」——
#   ① 共享技能目录（OneDrive 双机同步）内泄露私人目录结构与书目；
#   ② 缺参运行时会**真的去打开**那本硬编码的书（实测读到 430 页的真实书）。
#   现改为必填参数，缺参即退出并给用法。
import sys

# ---- 解释器守卫（v1.30.0）：先切到 EBOOK_PY 指定的 venv，再 import 第三方库 ----
# 详见 _interp_guard.py；必须位于 import fitz 之前。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

if len(sys.argv) < 2:
    sys.exit("用法: python 00_probe.py <book.pdf>\n"
             "作用：探测文字层。若「有文本层页占比」为 0%，说明是纯扫描件——"
             "不必退回用户，改跑 01a_ocr.py 重 OCR 即可（见 SKILL.md §任务受理）。")

PDF = sys.argv[1]

try:
    import fitz
except ImportError:
    sys.exit("❌ 缺少依赖 pymupdf。请在技能规定的 venv 中运行（见 SKILL.md §1 环境准备）：\n"
             "   python scripts/00_probe.py <book.pdf>")

# 🟡（2026-09-12）：畸形/损坏 PDF 原先抛裸 traceback，对用户不可读
try:
    doc = fitz.open(PDF)
except Exception as e:
    sys.exit(f"❌ 无法打开 PDF：{PDF}\n   {type(e).__name__}: {e}\n"
             f"   文件不存在？不是 PDF？或已损坏？请检查路径与文件完整性。")

total = len(doc)
# 🟡（2026-09-12）：零页 PDF 原先触发 ZeroDivisionError（nonempty/total）
if total == 0:
    sys.exit(f"❌ 该 PDF 页数为 0，无法探测：{PDF}")

nonempty = 0
for page in doc:
    if len(page.get_text().strip()) > 20:
        nonempty += 1

print(f"总页数: {total}")
print(f"有文本层页占比: {nonempty/total:.0%}")
print("书名:", doc.metadata.get("title"))
print("作者:", doc.metadata.get("author"))
toc = doc.get_toc()
print("书签目录条数:", len(toc), "（0 表示无书签，需从目录页重建）")
if toc:
    for lvl, title, pno in toc[:10]:
        print(f"  {'  '*lvl}{title} -> 物理页{pno}")

# 无文字层（占比 0%）＝**纯扫描件**，按 SKILL.md §任务受理：不必退回用户，
# 直接用 `01a_ocr.py` 内置 RapidOCR 重出文字层（本脚本只诊断、不是闸门，故 exit 0）。
# 此前此处 sys.exit 非零，与「不把用户推回、直接走重 OCR」的现行约定相反，2026-09-26 改回。
if nonempty == 0:
    print("\n[提示] 有文字层页占比 0%：纯扫描件，本技能不必退回用户——"
          "请改跑 `01a_ocr.py <pdf> <work> [--engine v6]`，产物与 01a_raw.py 同构，"
          "随后照常走 Stage 2/3。（见 SKILL.md §任务受理）")
