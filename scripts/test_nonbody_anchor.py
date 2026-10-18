#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_nonbody_anchor.py —— preprocess_nonbody v1.32.0 锚定与分类纯函数单测。

覆盖（2026-10-16 六书实测形态）：
  - cluster_anchor：混注页（正文行少）/ 单行正文 / 孤立装饰大字防劫持 /
    回目大字 / 单峰同字号 / 空输入
  - classify_nonbody：通栏注文〖注〗 / 页边小字〖批〗 / 页顶小字（眉批）〖批〗 /
    有色小字〖批〗 / 正文字号不标 / size/锚/bbox 无效容错
纯函数测试，不依赖 OCR 环境。
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "preprocess_nonbody", os.path.join(HERE, "preprocess_nonbody.py"))
pnb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pnb)

fails = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✔ {name}")
    else:
        fails.append(name)
        print(f"  ❌ {name} {detail}")


# ── cluster_anchor ────────────────────────────────────────────────────────
print("cluster_anchor：")

# 十一家注型：注文占多数、正文行少 → 锚必须落正文层（大字号层）
a = pnb.cluster_anchor([(35, 30), (35.2, 28), (28.6, 120), (27.8, 110),
                        (26.8, 100), (24.0, 2)])
check("混注页正文2行", 34 <= a <= 36, f"got {a}")

a = pnb.cluster_anchor([(35, 30), (28.6, 120), (27.8, 110), (26.8, 100),
                        (28.0, 90), (24.0, 2)])
check("混注页正文1行(字数≥5%)", 34 <= a <= 36, f"got {a}")

# 孤立装饰大字（单行 3 字）→ 跳过取正文层
a = pnb.cluster_anchor([(42.0, 3), (36.0, 20), (34.0, 25), (34.0, 25),
                        (29.3, 90), (29.0, 85), (28.0, 80), (27.0, 60)])
check("孤立装饰大字防劫持", 34 <= a <= 36, f"got {a}")

# 回目大字（1 行 18 字，占比 6% ≥5%）→ 可成锚（阈值升高只影响边界行，安全侧）
a = pnb.cluster_anchor([(37.0, 18), (35.0, 40), (34.5, 40), (34.0, 40),
                        (28.0, 90), (27.5, 80)])
check("回目大字成锚(安全侧)", 34 <= a <= 37.5, f"got {a}")

# 单峰同字号（校记页）：最大字号簇即正文层
a = pnb.cluster_anchor([(36.4, 60), (38.0, 55), (43.7, 50)])
check("单峰同字号", abs(a - 43.7) < 0.01, f"got {a}")

check("空输入", pnb.cluster_anchor([]) is None)

# ── classify_nonbody ─────────────────────────────────────────────────────
print("classify_nonbody：")
pw, ph = 1000.0, 1400.0

check("通栏注文→注",
      pnb.classify_nonbody(26.5, None, [200, 800, 840, 830], pw, ph, 35.0) == "zhu")
check("窄边距书通栏注文→注(版心界下不误判页边)",
      pnb.classify_nonbody(26.5, None, [110, 800, 930, 830], pw, ph, 35.0,
                           body_x1=100.0, body_x2=940.0) == "zhu")
check("页边左窄小字→批",
      pnb.classify_nonbody(27.0, None, [20, 700, 60, 730], pw, ph, 35.0) == "pi")
check("页边右窄小字→批",
      pnb.classify_nonbody(27.0, None, [950, 700, 990, 730], pw, ph, 35.0) == "pi")
check("页顶窄小字(眉批)→批",
      pnb.classify_nonbody(24.0, None, [300, 30, 900, 55], pw, ph, 35.0) == "pi")
check("通栏页顶小字→批",
      pnb.classify_nonbody(24.0, None, [100, 30, 900, 55], pw, ph, 35.0) == "pi")
check("正文字号不标",
      pnb.classify_nonbody(35.0, None, [100, 100, 900, 130], pw, ph, 35.0) == "")
check("阈值边界(≥锚×0.8)不标",
      pnb.classify_nonbody(29.5, None, [100, 100, 900, 130], pw, ph, 35.0) == "")
check("有色小字→批",
      pnb.classify_nonbody(20.0, 200, [100, 100, 900, 130], pw, ph, 35.0) == "pi")
check("size=None 容错",
      pnb.classify_nonbody(None, None, [1, 2, 3, 4], pw, ph, 35.0) == "")
check("锚=None 容错(整页跳)",
      pnb.classify_nonbody(20.0, None, [1, 2, 3, 4], pw, ph, None) == "")
check("bbox=None 容错(小字仍标注)",
      pnb.classify_nonbody(20.0, None, None, pw, ph, 35.0) == "zhu")
check("bbox 长度不足容错",
      pnb.classify_nonbody(20.0, None, [1, 2], pw, ph, 35.0) == "zhu")

# ── 版心界判定（2026-10-16 szyz 实测新增；锚 30 → 阈值 24，size 20 为小字）──
bx1, bx2 = 100.0, 900.0  # 版心 [100, 900]，页宽 1000，eps=24
check("段末短行贴版心右内缘→注(不误判页边)",
      pnb.classify_nonbody(20.0, None, [500, 742, 895, 762], pw, ph, 30.0,
                           body_x1=bx1, body_x2=bx2) == "zhu")
check("版心外左侧小字→批",
      pnb.classify_nonbody(20.0, None, [30, 700, 70, 730], pw, ph, 30.0,
                           body_x1=bx1, body_x2=bx2) == "pi")
check("版心外右侧小字→批",
      pnb.classify_nonbody(20.0, None, [930, 700, 970, 730], pw, ph, 30.0,
                           body_x1=bx1, body_x2=bx2) == "pi")
check("版心内通栏注文→注",
      pnb.classify_nonbody(20.0, None, [110, 800, 890, 830], pw, ph, 30.0,
                           body_x1=bx1, body_x2=bx2) == "zhu")
check("版心界缺省回退页宽比例法",
      pnb.classify_nonbody(20.0, None, [20, 700, 60, 730], pw, ph, 35.0) == "pi")

# ── 版心 y 界（2026-10-16 laozi 书眉实测新增）────────────────────────────
# 书眉/页码横向在版心内、y1=173 高于固定 8% 页高线 → 版心 y 界兜住
byt, byb = 250.0, 1300.0  # 版心 y 带 [250, 1300]，页高 1400
check("书眉在版心 y 界上带外→批",
      pnb.classify_nonbody(23.0, None, [61, 173, 104, 195], pw, ph, 34.0,
                           body_x1=bx1, body_x2=bx2,
                           body_y_top=byt, body_y_bot=byb) == "pi")
check("版心 y 界内注文→注",
      pnb.classify_nonbody(20.0, None, [500, 742, 895, 762], pw, ph, 30.0,
                           body_x1=bx1, body_x2=bx2,
                           body_y_top=byt, body_y_bot=byb) == "zhu")
check("y 界缺省回退页高比例法(页顶小字→批)",
      pnb.classify_nonbody(24.0, None, [300, 30, 900, 55], pw, ph, 35.0) == "pi")

# ── 汇总 ─────────────────────────────────────────────────────────────────
if fails:
    print(f"\n❌ {len(fails)} 项失败：{fails}")
    sys.exit(1)
print("\n✅ test_nonbody_anchor 全部通过。")
