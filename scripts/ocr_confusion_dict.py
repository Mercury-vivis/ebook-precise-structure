#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ocr_confusion_dict.py —— OCR 混淆词典（P0 改造1：独立内容正确性基准）

根因：v1.16.2 三道闸证明的是「台账 ↔ 03 自洽」，不懂「03 文本本身正确」。
账本没列的错误（磨宋 / 剌史 / 待臣）所有闸门全盲。本词典提供一份**独立于台账**的
OCR 混淆基准，让闸2 拿它去扫 03，堵死「闭环 PASS 但内容有错」的假通过。

两类条目（设计核心：禁裸单字计数，避免反杀合法字）：
  compound   : 复合错形 → 正字（≥2 字，串本身即诊断，绝不会是合法正字，可直接 count 阻断）
  char_class : 裸单字混淆类（禁止裸计数！）—— 仅用于核查侧「上下文正则」扫描，
               施加以侧放行（避免误伤磨勘/磨练/秋毫 等合法单字同形词）。

书型分桶：char_class 的 book_types 控制「哪些书型扫这条」（"*" = 全类型）。
取值须与 `book_meta.json` 的 `type` 字段主类型一致——即 ①–⑩ 类号前缀
（如 "④ 制度/沿革型"、"⑥ 通史/断代型"），用类号作前缀匹配。
词典种子来自赖瑞和三书本轮 99_*.json（制度史/职官/通史书型 → 映射 ④/⑥）。

版本：v1.17.0 引入（重大功能）。
"""
from __future__ import annotations

# ---- A 类：复合错形（≥2 字，直接 count，安全）----
# 赖瑞和（唐代制度史/职官/通史）书型种子：唐↔磨 复合串 + 共享混淆 + 专名 OCR 错
COMPOUND: dict[str, str] = {
    # —— 唐↔磨 复合串（本轮三书实测漏网，已修）——
    "磨宋": "唐宋",
    "磨语林": "唐语林",
    "两磨书": "两唐书",
    "两《磨书》": "两《唐书》",
    "磨长安": "唐长安",
    "磨朝": "唐朝",
    "磨方镇": "唐方镇",
    "磨蕃": "唐蕃",
    "磨“望秩": "唐“望秩",
    "(磨)张彦远": "(唐)张彦远",
    "磨县": "唐县",
    "磨(张说墓志)": "唐(张说墓志)",
    "《归磨书》": "《旧唐书》",
    "I.磨": "I.唐",
    "磨史": "唐史",
    "磨尚书省": "唐尚书省",
    "磨研究": "唐研究",
    "磨践贞": "唐践贞",
    "磨史研究丛稿": "唐史研究丛稿",
    "中晚磨使府": "中晚唐使府",
    "磨两京城坊考": "唐两京城坊考",
    "(磨)杜牧": "(唐)杜牧",
    "磨文难灭": "唐文难灭",
    # —— 共享混淆（剌↔刺 / 待↔侍 / 弟↔第 等）——
    "剌史": "刺史",
    "待臣": "侍臣",
    "势门子第": "势门子弟",
    # —— 墓志类（墓志/墓誌 误作 基志/慕志）——
    "基志": "墓志",
    "慕志": "墓志",
    # —— 专名 OCR 错（赖瑞和书系，本轮 98 文件）——
    "桂齐逊": "桂齐迈",
    "王助成": "王勋成",
    # "王堆": "王维",  # 2026-10-12 注释：误报风险高——"马王堆"（长沙汉墓）为合法地名，军事史/史料类书高频出现，compound裸串会误报；如需保留应改char_class带ctx
    "韩黎": "韩昌黎",
    "同文儒": "阎文儒",
    "毛耆": "毛汉光",
    "傅璜琮": "傅璇琮",
    "傅璇琼": "傅璇琮",
}

# ---- B 类：裸单字混淆（禁止裸计数，仅核查侧 ctx 正则扫描）----
# 键=错字；right=正字；ctx=触发右/左邻正则；book_types=适用书型（"*" 全类型）
CHAR_CLASS: dict[str, dict] = {
    "磨": {
        "right": "唐",
        # 仅当 磨 后跟这些「唐-复合」典型接续字时才判错；磨勘/磨练/磨灭/折磨/研磨/消磨/
        # 磋磨/磨砺/磨损/磨合/打磨/琢磨 均不匹配（后接 勘/炼/灭/磨/研/消/磋/砺/损/合/打/琢）。
        "ctx": [r"磨(?=宋|语林|书|朝|蕃|长安|方镇|研究|史|两京|“|\)|（|县|文)"],
        "book_types": ["④", "⑥"],
    },
    "剌": {
        "right": "刺",
        "ctx": [r"剌(?=史)"],
        "book_types": ["*"],
    },
}


def book_type_match(book_type: str, allowed: list) -> bool:
    """书型是否命中条目的 book_types（含 '*' 全类型）。

    book_type 形如 '④ 制度/沿革型（职官）' → 取主类型（括号前）与 allowed 比对。
    比对规则：精确相等 **或** allowed 中任一值为 bt 的前缀（类号前缀匹配，
    与 _common.is_narrative 的判据口径一致），以兼容 '④' / '④ 制度/沿革型' 两种写法。
    """
    if not allowed:
        return True
    if "*" in allowed:
        return True
    if not book_type:
        return False
    bt = book_type.split("（")[0].strip()
    return any(bt == a or bt.startswith(a) for a in allowed)


def scan(clean_text: str, book_type: str = "") -> list:
    """扫描 03 干净正文，返回 content_residual 列表（独立于台账的内容错字）。

    - compound：直接 count（安全，≥2 字串绝不合法）；
    - char_class：仅本书型命中，用 ctx 正则扫描（禁裸计数，避免误伤合法同形字）。
    返回格式：["错形（词典复合错形，应为「正字」，N处）", ...]
    """
    hits: list = []
    # A 类 compound
    for wrong, right in COMPOUND.items():
        if not wrong:
            continue
        n = clean_text.count(wrong)
        if n > 0:
            hits.append(f"{wrong}（词典复合错形，应为「{right}」，{n}处）")
    # B 类 char_class（仅书型命中）
    import re
    for ch_wrong, spec in CHAR_CLASS.items():
        if not book_type_match(book_type, spec.get("book_types", [])):
            continue
        n = 0
        for rx in spec.get("ctx", []):
            try:
                n += len(re.findall(rx, clean_text))
            except re.error:
                pass
        if n > 0:
            hits.append(f"{ch_wrong}（词典混淆类，应为「{spec.get('right', '')}」，上下文命中 {n}处）")
    return hits
