#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ocr_confusion_dict.py —— OCR 混淆词典（P0 改造1：独立内容正确性基准）

根因：v1.16.2 三道闸证明的是「台账 ↔ 03 自洽」，不懂「03 文本本身正确」。
账本没列的错误（磨宋 / 剌史 / 待臣）所有闸门全盲。本词典提供一份**独立于台账**的
OCR 混淆基准，让闸2 拿它去扫 03，堵死「闭环 PASS 但内容有错」的假通过。

三类条目（设计核心：禁裸计数，避免反杀合法字）：
  compound   : 复合错形 → 正字（≥2 字，串本身即诊断，绝不会是合法正字，可直接 count 阻断）
  general    : 通用 OCR 混淆对（跨书适用），**必须带上下文边界**（pattern 正则）。
               它防的是「已知事故错形复发」，不负责发现新错字——见下方实测判据。
  char_class : 裸单字混淆类（禁止裸计数！）—— 仅用于核查侧「上下文正则」扫描，
               施加以侧放行（避免误伤磨勘/磨练/秋毫 等合法单字同形词）

通用桶（general）的入库判据（2026-10-17 实测立，禁凭直觉添条）：
  1. 错形本身不是任何合法词，且**不是正字的跨词边界拼接**；
  2. 候选先在全库已交付 03 语料上跑裸串统计，逐条看左右邻字分布；
     任一合法组合可复现（如「提出上诉」「攻占代州」「开战土耳其」「李慈铭日记」
     「职工家属」「灭绝人类」「一幕一幕地」「同时问起」「共工族」「堂上室内」
     「通商入蜀」「汉人型」），该条或加边界、或直接否决；
  3. 加边界后须能通过 test_confusion_general.py 的正例/反例双向断言。
  实测否决（不得再凭直觉加回）：
    日的 / 问的 / 面已 / 上要 / 全而 / 因面 / 从面 / 方而 —— 正字跨词边界的产物
        （节日的时间 / 顾问的关系 / 方面已经 / 早上要 / 为了安全而 / 因面临 / 从面向 / 交战方而存在），
        裸串即是合法文本，属"假诊断"，永远不进通用桶；
    深人 / 出上 / 商入 / 战土 / 工家 / 绝人 / 人型 / 幕地 / 铭日 / 时问 / 工族 / 上室 / 礼会 ——
        合法上下文占比高（深人防 / 提出上诉 / 通商入蜀 / 开战土耳其 / 职工家属 / 才智绝人 /
        汉人型 / 一幕地 / 李慈铭日记 / 同时问 / 共工族 / 堂上室内 / 仲礼会合），
        简单断言排除不干净，交由本书台账逐处处置。
  一句话：通用桶只收「裸串即错」的高置信条目；覆盖面有限是设计取舍，不是缺陷。。

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


# ---- C 类：通用 OCR 混淆对（跨书适用，2026-10-17 立）----
# 入库判据与实测否决清单见模块 docstring——**只收「裸串即错」的高置信条目**，
# 每条 pattern 必须能用 test_confusion_general.py 的正例/反例双向断言守住。
# book_types 省略＝全类型适用（形近字误读与书型无关）。
GENERAL: dict[str, dict] = {
    # 己↔已：古籍「自已」= 自止（《三国志》「先除大害，小害自已」），后接而/矣/也 时放行
    "自已": {"right": "自己", "pattern": r"自已(?!而|矣|也)", "why": "己↔已；文言「自已」后接 而/矣/也 时合法"},
    # 占↔古：实测「攻占代州」为合法跨词，排除地名后缀
    "占代": {"right": "古代", "pattern": r"占代(?!州|县|郡|府|道)", "why": "占↔古；「攻占代州」合法"},
    "考占": {"right": "考古", "pattern": r"考占", "why": "占↔古；「考占专业」系 OCR 误读"},
    "股墟": {"right": "殷墟", "pattern": r"股墟", "why": "股↔殷；「殷墟」为专名，「股墟」非词"},
    "殷塘": {"right": "殷墟", "pattern": r"殷塘", "why": "塘↔墟；无「殷塘」一词"},
    "幕葬": {"right": "墓葬", "pattern": r"幕葬", "why": "幕↔墓；无「幕葬」一词"},
    "幕志": {"right": "墓志", "pattern": r"幕志", "why": "幕↔墓；「幕志」非词，系「墓志」误读"},
    "扩人": {"right": "扩大", "pattern": r"扩人", "why": "人↔大；无「扩人」一词"},
    "贯族": {"right": "贵族", "pattern": r"贯族", "why": "贯↔贵；无「贯族」一词"},
}


def general_items(book_type: str = "") -> list:
    """返回本书型适用的通用条目 [(key, pattern, right), ...]。

    供两处消费：① `scan()` 内容扫描；② `_common.final_error_form_scan` 交付级复扫
    （让 00/01/02 派生件也受独立基准保护）。pattern 是正则原文，调用方须用
    `re.finditer` 而非 `str.count`。
    """
    import re as _re

    out = []
    for key, spec in GENERAL.items():
        if not book_type_match(book_type, spec.get("book_types", [])):
            continue
        out.append((key, spec.get("pattern") or _re.escape(key), spec.get("right", "")))
    return out


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


def scan(clean_text: str, book_type: str = "", whitelist: list = None) -> list:
    """扫描 03 干净正文，返回 content_residual 列表（独立于台账的内容错字）。

    - compound：直接 count（安全，≥2 字串绝不合法）；
    - char_class：仅本书型命中，用 ctx 正则扫描（禁裸计数，避免误伤合法同形字）。
    - whitelist：专名/地名白名单（如 ["四盘磨"]），仅当命中字符落在白名单词自身范围内时跳过，
      避免地名/专名被误报；白名单词附近的真实错字仍正常报告。
    返回格式：["错形（词典复合错形，应为「正字」，N处）", ...]
    """
    hits: list = []
    wl = [w for w in (whitelist or []) if w]

    # 预计算白名单词在文本中的所有位置范围（精确匹配，不误伤附近真实错字）
    import re as _re
    _wl_ranges: list = []  # [(start, end), ...]
    for w in wl:
        for m in _re.finditer(_re.escape(w), clean_text):
            _wl_ranges.append((m.start(), m.end()))

    def _in_whitelist_range(pos: int) -> bool:
        """检查匹配位置是否落在某个白名单词自身的字符范围内。"""
        for s, e in _wl_ranges:
            if s <= pos < e:
                return True
        return False

    # A 类 compound
    for wrong, right in COMPOUND.items():
        if not wrong:
            continue
        if wl:
            # 逐位置检查，跳过白名单词自身范围内的命中
            n = 0
            for m in _re.finditer(_re.escape(wrong), clean_text):
                if not _in_whitelist_range(m.start()):
                    n += 1
        else:
            n = clean_text.count(wrong)
        if n > 0:
            hits.append(f"{wrong}（词典复合错形，应为「{right}」，{n}处）")
    # C 类 general（通用混淆对：正则 + 白名单范围跳过；入库判据见模块 docstring）
    for key, pat, right in general_items(book_type):
        n = 0
        try:
            for m in _re.finditer(pat, clean_text):
                if not _in_whitelist_range(m.start()):
                    n += 1
        except _re.error:
            continue
        if n > 0:
            hits.append(f"{key}（通用混淆，应为「{right}」，{n}处）")
    # B 类 char_class（仅书型命中）
    import re
    for ch_wrong, spec in CHAR_CLASS.items():
        if not book_type_match(book_type, spec.get("book_types", [])):
            continue
        n = 0
        for rx in spec.get("ctx", []):
            try:
                if wl:
                    # 逐位置检查，跳过白名单词自身范围内的命中
                    for m in re.finditer(rx, clean_text):
                        if not _in_whitelist_range(m.start()):
                            n += 1
                else:
                    n += len(re.findall(rx, clean_text))
            except re.error:
                pass
        if n > 0:
            hits.append(f"{ch_wrong}（词典混淆类，应为「{spec.get('right', '')}」，上下文命中 {n}处）")
    return hits
