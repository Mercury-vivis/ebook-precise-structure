#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_check_book.py —— 闸B 反向差异三类分流 + 交付指纹口径（2026-10-19）

守四件事：
  1. 记录源是 corrections（含 regex/systematic），不是 patch_manifest（manifest 只落 9 键）
  2. ① 已登记（消除误报）：old 与底本只差空白/全半角时，不得报成「未登记」
  3. ② 页归属错位：账 phys 写错但补丁按全局回退生效 → 报「台账 phys=A / 实测 B」，不算未登记
  4. ③ 真未登记与正向残留：③ 只报告（exit 0）；正向残留才是阻断（exit 1）
  5. 交付指纹不符只报告、不阻断（修订路径定点改 03 本来就该指纹变）

v2.3.1 增守（2026-10-24，《中国的历史脉动》实测三类假阳性）：
  6. old==new 空转记录（type=存疑「只登记不改」）不判残留、不阻断
  7. load_manual_revisions 只解析 04 §四 4.2；4.1 撤销补丁 / 弯引号行截断不得混入
  8. 占位符「待填」需伴随脚手架语法；正文「亟待填充」正当行文不误报

运行：python scripts/test_check_book.py
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
fails = []


def check(desc, cond):
    if not cond:
        fails.append(desc)


def run(work, out, delivery=False):
    cmd = [PY, os.path.join(HERE, "check_book.py"), "--work", work, "--out", out]
    if not delivery:
        cmd.append("--no-delivery")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def build(tmp, struct_pages, corr, out_pages, meta=None):
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "out")
    os.makedirs(os.path.join(work, "corrections"))
    os.makedirs(out, exist_ok=True)
    with io.open(os.path.join(work, "fulltext_struct.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(f"<<<PAGE phys={p}>>>\n{t}" for p, t in struct_pages))
    with io.open(os.path.join(work, "corrections", "00_corrections.json"), "w",
                 encoding="utf-8", newline="\n") as f:
        json.dump(corr, f, ensure_ascii=False)
    if meta is not None:
        with io.open(os.path.join(work, "book_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    with io.open(os.path.join(out, "书03_带页码全文.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(f"<<<PAGE phys={p}>>>\n{t}" for p, t in out_pages))
    return work, out


def test_three_classes():
    """① 宽容匹配已登记 / ② 页归属错位 / ③ 真未登记，三类互不混淆"""
    tmp = tempfile.mkdtemp(prefix="cb_three_")
    struct = [(1, "甲乙丙 丁戊己庚辛"),      # 底本带空格（① 用）
              (2, "他日自已当知此事也"),      # ② 的 old 实际所在页
              (3, "壬癸子丑")]
    out_pages = [(1, "甲乙丙丁戊己庚辛子"),   # ① 已按台账改成 new（空格被替换覆盖）
                 (2, "他日自己当知此事也"),   # ② 补丁按全局回退落在这页
                 (3, "壬癸子丑寅")]           # ③ 账上无人认领
    corr = [
        # ① old 无空格、底本有空格：只有放宽空白才命中
        {"phys": 1, "old": "甲乙丙丁戊己庚辛", "new": "甲乙丙丁戊己庚辛子", "reason": "t"},
        # ② 声明 phys=1，实际在 phys2（old ≥4 触发全局回退）
        {"phys": 1, "old": "他日自已当知此事", "new": "他日自己当知此事", "reason": "t"},
    ]
    work, out = build(tmp, struct, corr, out_pages)
    rc, o = run(work, out)
    check("三类场景应 exit 0", rc == 0)
    check("① 已登记消除误报 1 条", "① 已登记（消除误报）1 条" in o)
    check("② 页归属错位 1 条", "② 页归属错位 1 条" in o)
    check("② 报出台账页与实测页", "台账 phys=1 / 实测 2" in o)
    check("② 不得再报「账/底本 0 命中」", "全书 0 命中" not in o or "全书 0 命中 0 条" in o)
    check("③ 真未登记 1 页", "③ 真未登记 1 页" in o)
    check("③ 定位到 phys=3 的改动", "phys=3" in o.split("③ 真未登记")[1])
    print("✅ test_three_classes: ① 宽容已登记 / ② 页归属错位 / ③ 真未登记 三类分流")


def test_residual_blocks():
    """正向残留仍是唯一内容类阻断：old 还在 03 → exit 1"""
    tmp = tempfile.mkdtemp(prefix="cb_res_")
    struct = [(1, "他日自已当知此事也")]
    out_pages = [(1, "他日自已当知此事也")]   # 台账说要改，03 没改
    corr = [{"phys": 1, "old": "自已当知此事", "new": "自己当知此事", "reason": "t"}]
    work, out = build(tmp, struct, corr, out_pages)
    rc, o = run(work, out)
    check("正向残留必须 exit 1", rc == 1)
    check("残留要点名", "正向残留 1 处" in o)
    print("✅ test_residual_blocks: 正向残留阻断（exit 1）")


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def test_fingerprint_report_only():
    """指纹不符只报告、不阻断；相符时报「一致」"""
    tmp = tempfile.mkdtemp(prefix="cb_fp_")
    struct = [(1, "他日自己当知此事也")]
    out_pages = [(1, "他日自己当知此事也")]
    corr = [{"phys": 1, "old": "自已当知此事", "new": "自己当知此事", "reason": "t"}]
    work, out = build(tmp, struct, corr, out_pages)
    for name in ("00_梳理总览与目录对照.md", "01_章节详解.md", "02_脉络与速查卡.md"):
        io.open(os.path.join(out, "书" + name), "w", encoding="utf-8", newline="\n").write("内容\n")
    p04 = os.path.join(out, "书04_改错留痕.md")
    bad = "00=sha256:" + "0" * 64
    io.open(p04, "w", encoding="utf-8", newline="\n").write("## 五、交付指纹\n\n> " + bad + "\n")
    rc, o = run(work, out, delivery=True)
    check("指纹不符不得阻断", rc == 0)
    check("指纹不符要报出", "交付指纹与 04 登记不符" in o)

    rec = "；".join(f"{k}=sha256:{_sha(os.path.join(out, '书' + s))}"
                   for k, s in (("00", "00_梳理总览与目录对照.md"), ("01", "01_章节详解.md"),
                                ("02", "02_脉络与速查卡.md"), ("03", "03_带页码全文.md")))
    io.open(p04, "w", encoding="utf-8", newline="\n").write("## 五、交付指纹\n\n> " + rec + "\n")
    rc, o = run(work, out, delivery=True)
    check("指纹相符报一致", rc == 0 and "交付指纹：00/01/02/03 与 assemble 定稿一致" in o)

    # 05 不属于五件（指纹机制下由 gen_reading_report 事后生成）——缺 05 不算缺件
    check("五件不含 05", "缺失" not in o or "05" not in o.split("缺失")[1][:30])
    print("✅ test_fingerprint_report_only: 指纹不符只报告 / 相符报一致 / 05 不计入五件")


def test_hygiene_analytical_vs_scaffold():
    """S3（v2.2.0）：分析行文（描述 OCR 未识别字符）不误报；脚手架形态仍命中"""
    tmp = tempfile.mkdtemp(prefix="cb_ph_")
    struct = [(1, "他日自己当知此事也")]
    out_pages = [(1, "他日自己当知此事也")]
    corr = [{"phys": 1, "old": "自已当知此事", "new": "自己当知此事", "reason": "t"}]
    work, out = build(tmp, struct, corr, out_pages)
    for name in ("00_梳理总览与目录对照.md", "01_章节详解.md", "02_脉络与速查卡.md"):
        io.open(os.path.join(out, "书" + name), "w", encoding="utf-8", newline="\n").write("内容\n")
    io.open(os.path.join(out, "书04_改错留痕.md"), "w", encoding="utf-8", newline="\n").write(
        "## 四、撤销与人工修订\n- （无）\n")
    # ① 分析行文：notes/01 描述 OCR 未识别字符的正当使用（旧词表在此误报阻断）
    with io.open(os.path.join(out, "书01_章节详解.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("- phys 280：「i」为 OCR 占位符，无法辨识；「U伐」同。\n")
    rc, o = run(work, out, delivery=True)
    check("分析行文不得误报占位符", "占位符残留：" not in o)
    check("分析行文场景 exit 0", rc == 0)
    # ② 脚手架形态（带【】语法）仍命中——收窄不放漏
    with io.open(os.path.join(out, "书01_章节详解.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("- phys 280：【占位符】待补\n")
    rc, o = run(work, out, delivery=True)
    check("脚手架形态仍命中", "占位符残留：" in o)
    check("脚手架残留必须阻断（exit 1）", rc == 1)
    print("✅ test_hygiene_analytical_vs_scaffold: 分析行文不误报 / 脚手架仍命中")


def test_residual_context():
    """S6（v2.2.0）：正向残留行附 ±15 字实例上下文，合法同形一眼可判"""
    tmp = tempfile.mkdtemp(prefix="cb_ctx_")
    struct = [(1, "同殷辭与卜辞不同"), (2, "他日自已当知此事也")]
    out_pages = [(1, "同殷辭与卜辞不同"), (2, "他日自已当知此事也")]
    # 宽形 old「同殷」误中合法文本「同殷辭」（商殷语义）——靠上下文可判
    corr = [{"phys": 1, "old": "同殷", "new": "同毀", "reason": "t"}]
    work, out = build(tmp, struct, corr, out_pages)
    rc, o = run(work, out)
    check("残留阻断 exit 1", rc == 1)
    check("残留行附实例上下文", "残留实例" in o)
    check("实例含前后文（同殷辭）", "同殷辭" in o)
    print("✅ test_residual_context: 残留行附实例上下文（合法同形可判）")


def test_idle_record_not_residual():
    """v2.3.1：old==new（type=存疑「只登记不改」）不判正向残留、不阻断。

    实测：《中国的历史脉动》54 条存疑登记被旧口径判残留，闸B 假阻断 55 处
    （54 台账 + 1 条 04 §四 4.1 撤销补丁被 _REV_RE 截断出的假 old==new）。"""
    tmp = tempfile.mkdtemp(prefix="cb_idle_")
    struct = [(1, "他日自己当知此事也")]
    out_pages = [(1, "他日自己当知此事也")]
    corr = [{"phys": 1, "old": "自己当知此事", "new": "自己当知此事",
             "type": "存疑", "reason": "存疑：拿不准，只登记不改", "source": "t"}]
    work, out = build(tmp, struct, corr, out_pages)
    rc, o = run(work, out)
    check("空转记录不得判残留", "正向残留" not in o or "正向残留 0 处" in o)
    check("空转记录不阻断（exit 0）", rc == 0)
    print("✅ test_idle_record_not_residual: old==new 存疑登记不误判残留、不阻断")


def test_manual_revisions_only_42():
    """v2.3.1：load_manual_revisions 只解析 §四 4.2；4.1 撤销补丁不当修订。

    实测：《中国的历史脉动》04 §四 4.1 的 54 条撤销补丁被当人工修订解析——
    phys=45 一条因 old/new 含弯引号被截断成假 old==new（正向残留误阻断），
    phys=5/28 两条 new 被截成短串（反向复现删引文括注，假报 2 页未登记）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_book", os.path.join(HERE, "check_book.py"))
    cb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cb)
    tmp = tempfile.mkdtemp(prefix="cb_rev42_")
    out = os.path.join(tmp, "out")
    os.makedirs(out)
    p04 = os.path.join(out, "书04_改错留痕.md")
    io.open(p04, "w", encoding="utf-8", newline="\n").write(
        "## 四、撤销与人工修订\n\n"
        "### 4.1 经核实撤销的补丁\n\n"
        "- phys=45 「己出而后为子”（同前)，」→「己出而后为子”（同前)，」｜撤销理由：半角闭括号应为全角）。\n"
        "- phys=5 「交错杂陈。”（《动荡的清末民初时期》)」→「交错杂陈。”（《动荡的清末民初时期》）」｜撤销理由：出处括号。\n\n"
        "### 4.2 人工定点修订（修订路径：以书为准改 03 后回写）\n\n"
        "- phys=112 ｜ 原文「国民党车队」→ 改为「国民党军队」｜ 理由：OCR 军/车 形近\n\n"
        "### 4.3 派生件同步（notes → 01/02 与 03 一致）\n\n"
        "- 本次 assemble 未发生派生件同步。\n\n"
        "### 4.4 考据裁定与预处理处置登记\n\n"
        "- phys=9 「某词」→「某改」｜裁定不修。\n")
    revs = cb.load_manual_revisions(out, "书")
    check("只解析出 4.2 的 1 条真修订", len(revs) == 1)
    if revs:
        check("修订 phys 正确", revs[0]["phys"] == "112")
        check("修订 old 完整（含弯引号行不被截断）", revs[0]["old"] == "国民党车队")
        check("修订 new 完整", revs[0]["new"] == "国民党军队")
    print("✅ test_manual_revisions_only_42: 04 §四 只解析 4.2，撤销/同步/裁定节不参与")


def test_placeholder_daitian_tightened():
    """v2.3.1：「待填」裸词收窄为需脚手架语法——正文「亟待填充」不再误报。

    实测：《中国的历史脉动》03 正文「这种瓦解带来的空白亟待填充」含子串「待填」，
    旧模式报「占位符残留：待填(1处)」阻断交付，全书唯一命中即此正当行文。"""
    tmp = tempfile.mkdtemp(prefix="cb_dt_")
    struct = [(1, "他日自己当知此事也")]
    out_pages = [(1, "他日自己当知此事也")]
    corr = [{"phys": 1, "old": "自已当知此事", "new": "自己当知此事", "reason": "t"}]
    work, out = build(tmp, struct, corr, out_pages)
    for name in ("00_梳理总览与目录对照.md", "01_章节详解.md", "02_脉络与速查卡.md"):
        io.open(os.path.join(out, "书" + name), "w", encoding="utf-8", newline="\n").write("内容\n")
    io.open(os.path.join(out, "书04_改错留痕.md"), "w", encoding="utf-8", newline="\n").write(
        "## 四、撤销与人工修订\n- （无）\n")
    with io.open(os.path.join(out, "书03_带页码全文.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("<<<PAGE phys=1>>>\n这种瓦解带来的空白亟待填充，革命后各种各样构想涌现。\n")
    rc, o = run(work, out, delivery=True)
    check("正文「亟待填充」不得误报占位符", "占位符残留：" not in o)
    check("正当行文场景 exit 0", rc == 0)
    # 脚手架形态仍命中——收窄不放漏
    with io.open(os.path.join(out, "书03_带页码全文.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("<<<PAGE phys=1>>>\n此处留有【待填】标记。\n")
    rc, o = run(work, out, delivery=True)
    check("脚手架【待填】仍命中", "待填" in o and "占位符残留：" in o)
    check("脚手架残留必须阻断（exit 1）", rc == 1)
    print("✅ test_placeholder_daitian_tightened: 正文亟待填充不误报 / 脚手架【待填】仍阻断")


def test_preprocess_closure_zero_hit():
    """v2.3.1：碎片剥离审计 0 命中时，无 region_noise_log.md 属正常、不亮红旗。

    实测：《中国的历史脉动》region_noise_audit.md 结论「命中剥离页 0」（394 页中含
    table/img 页仅 1），旧逻辑无条件按 dry-run 红旗报，还连带误亮「有红旗项未在
    04 登记处置」。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_book", os.path.join(HERE, "check_book.py"))
    cb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cb)
    tmp = tempfile.mkdtemp(prefix="cb_pp_")
    work = os.path.join(tmp, "work")
    out = os.path.join(tmp, "out")
    os.makedirs(work)
    os.makedirs(out)
    audit = ("> 总页数：394；含 table/img 页：1；命中剥离页：**0**；"
             "整页排除：22；待补图页：0；疑似图片文字层（无 marker，仅标记）：0\n")
    io.open(os.path.join(work, "region_noise_audit.md"), "w", encoding="utf-8").write(audit)
    # ① 0 命中、无 log → 不亮红旗
    lines, red = cb.check_preprocess_closure(work, out, "书")
    check("审计 0 命中不得亮红旗", not red)
    check("审计 0 命中应报 ✅", any("命中剥离 0 页" in ln and "✅" in ln for ln in lines))
    # ② 反例：>0 命中、无 log → 仍亮红旗（收窄不放漏）
    audit2 = audit.replace("命中剥离页：**0**", "命中剥离页：**3**")
    io.open(os.path.join(work, "region_noise_audit.md"), "w", encoding="utf-8").write(audit2)
    lines2, red2 = cb.check_preprocess_closure(work, out, "书")
    check("命中 3 页无 log 仍亮红旗", red2)
    check("命中 3 页无 log 报 dry-run", any("仅 dry-run" in ln for ln in lines2))
    print("✅ test_preprocess_closure_zero_hit: 0 命中不误报 / >0 命中仍报 dry-run 红旗")


if __name__ == "__main__":
    test_three_classes()
    test_residual_blocks()
    test_fingerprint_report_only()
    test_hygiene_analytical_vs_scaffold()
    test_residual_context()
    test_idle_record_not_residual()
    test_manual_revisions_only_42()
    test_placeholder_daitian_tightened()
    test_preprocess_closure_zero_hit()
    if fails:
        print(f"❌ test_check_book 失败 {len(fails)} 项：")
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print("✅ test_check_book 通过")
