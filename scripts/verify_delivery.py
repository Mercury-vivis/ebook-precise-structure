#!/usr/bin/env python3
# verify_delivery.py —— 交付一键验证（P1-F3，v1.27.0；v1.34.0 增指纹与图引用）
# 【已被 check_book.py（交付完整性：五件齐全 / BOM·CRLF / 图引用） 取代（2026-10-18）】本文件保留供历史核对，新流程不要调用。
#
# 用法：python verify_delivery.py <OUT交付目录> [--prefix 文件名前缀]
# 检查：六件齐全 / CRLF残留 / BOM残留 / 占位符残留 / 文件非空
#       + 交付指纹（04 记录的 00/01/02/03 sha256 ↔ 实件，防 assemble 后手改）
#       + 图引用实体（03 引用的 imgs/、fig/ 文件是否真的落在交付目录）
# 替代交付后人工逐项检查。退出码：0 通过；1 未通过；2 目录/前缀无法定位。

import os, sys, argparse, re, glob, hashlib

# ---- 解释器守卫（v1.30.0）：先切到 EBOOK_PY 指定的 venv，再 import 第三方库 ----
# 详见 _interp_guard.py。本脚本当前纯标准库，接入是为口径统一。
from _interp_guard import ensure_interpreter  # noqa: E402

ensure_interpreter()

# 交付指纹行由 pipeline_assemble / assemble_epub 在定稿前写入 04 末尾，形如：
#   ## 五、交付指纹
#   > 00=sha256:<64hex>；01=sha256:…；02=sha256:…；03=sha256:…
# 04 自身不参与（自指无意义），05 在指纹之后由 gen_reading_report 生成。
_FP_RE = re.compile(r"(00|01|02|03)=sha256:([0-9a-f]{64})")


def check_file(filepath):
    """检查单个文件：BOM / CRLF / 占位符 / 非空"""
    issues = []
    with open(filepath, "rb") as f:
        raw = f.read()
    # BOM
    if raw.startswith(b"\xef\xbb\xbf"):
        issues.append("BOM残留(UTF-8 BOM)")
    elif raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        issues.append("BOM残留(UTF-16)")
    # CRLF
    crlf_count = raw.count(b"\r\n")
    if crlf_count > 0:
        issues.append(f"CRLF残留{crlf_count}处（应为LF）")
    # 非空
    if len(raw.strip()) == 0:
        issues.append("文件为空")
    # 占位符（文本文件才查）
    try:
        text = raw.decode("utf-8", errors="ignore")
        placeholders = []
        # v1.32.1：原裸词「占位」会把正文正常词组一并命中（实测本书「夺占位于赵、魏之间」
        # 被判占位符残留，交付验证误报不通过）。收紧为「占位符」及明确标记形态。
        for pat in [r"<!--\s*待补充\s*-->", r"TODO", r"FIXME", r"\bXXX\b", r"待填",
                    r"占位符", r"占位标记", r"占位待补"]:
            matches = re.findall(pat, text)
            if matches:
                placeholders.append(f"{pat}({len(matches)}处)")
        if placeholders:
            issues.append(f"占位符残留：{', '.join(placeholders)}")
    except Exception:
        pass
    return issues


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_fingerprint(out_dir, expected):
    """交付指纹 ↔ 实件 sha256。

    返回 (issues, present)：
      present=False → 04 里没有指纹行（v1.34.0 之前的旧交付件），只提示不阻断，
                      否则历史交付件会被一刀切判不通过。
    """
    issues = []
    p04 = os.path.join(out_dir, expected["04"])
    if not os.path.isfile(p04):
        return issues, False
    try:
        txt = open(p04, encoding="utf-8", errors="ignore").read()
    except Exception:
        return ["04 读取失败，交付指纹无法核验"], False
    recorded = dict(_FP_RE.findall(txt))
    if not recorded:
        return issues, False
    for key in ("00", "01", "02", "03"):
        p = os.path.join(out_dir, expected[key])
        if key not in recorded or not os.path.isfile(p):
            continue
        if _sha256(p) != recorded[key]:
            issues.append(f"{key} 内容与 assemble 定稿不一致（sha256 不符——疑似 assemble 之后手改；"
                          f"请把改动写回 corrections 台账并重跑 assemble）")
    return issues, True


def check_images(out_dir, expected):
    """03 图引用 ⊆ 交付目录实体文件。

    03 里的 `imgs/p{phys}_*.jpg`（book_ocr 裁图 / 08 补嵌）与 `fig/fig_pNNNN_k.png`
    （07_ 正文内嵌图）是**相对交付目录**的路径；只交六件 md 不交图目录＝全部断链，
    而「只数引用条数」验不出断链，故按集合包含关系核验。
    """
    p03 = os.path.join(out_dir, expected["03"])
    if not os.path.isfile(p03):
        return []
    try:
        txt = open(p03, encoding="utf-8", errors="ignore").read()
    except Exception:
        return []
    refs = set(re.findall(r"((?:imgs|fig)/[^)\s\"'>\]]+?\.(?:jpg|jpeg|png|gif|webp|svg))",
                          txt, re.I))
    if not refs:
        return []
    missing = sorted(r for r in refs if not os.path.isfile(os.path.join(out_dir, r)))
    if not missing:
        return []
    head = "、".join(missing[:5]) + ("…" if len(missing) > 5 else "")
    return [f"图引用断链 {len(missing)}/{len(refs)} 个（{head}）——03 的图路径是相对交付目录，"
            f"须把 work 的 imgs/ 整目录同步到交付目录"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--prefix", default=None, help="文件名前缀（如 六十年来中国与日本_第3卷_王芸生），不指定则自动检测")
    args = ap.parse_args()

    out_dir = args.out
    if not os.path.isdir(out_dir):
        print(f"❌ 交付目录不存在：{out_dir}", file=sys.stderr)
        sys.exit(2)

    # 自动检测前缀
    # 2026-10-17 修：原用 glob("*00_*.md") + split("00_")[0]，书名含 "00_" 时前缀被截断
    # （实测《Firearms_A_Global_History_to_1700_...》→ 前缀成了 "..._to_17"，六件全报缺失、
    #  指纹与图引用静默跳过）。改为按固定的 00 文件名精确匹配、再截去后缀。
    prefix = args.prefix
    if not prefix:
        _suffix00 = "00_梳理总览与目录对照.md"
        md_files = glob.glob(os.path.join(out_dir, "*" + _suffix00))
        if md_files:
            base = os.path.basename(md_files[0])
            prefix = base[: -len(_suffix00)] if base.endswith(_suffix00) else base.split("00_")[0]
        else:
            print("❌ 未找到 00_*.md，无法自动检测前缀，请用 --prefix 指定", file=sys.stderr)
            sys.exit(2)

    # 六件清单
    expected = {
        "00": f"{prefix}00_梳理总览与目录对照.md",
        "01": f"{prefix}01_章节详解.md",
        "02": f"{prefix}02_脉络与速查卡.md",
        "03": f"{prefix}03_带页码全文.md",
        "04": f"{prefix}04_改错留痕.md",
        "05": f"{prefix}05_精读汇报.md",
    }

    print("=" * 68)
    print(f"交付一键验证：{out_dir}")
    print(f"前缀：{prefix}")
    print("=" * 68)

    all_issues = []
    missing = []
    for key, fname in expected.items():
        fpath = os.path.join(out_dir, fname)
        if not os.path.isfile(fpath):
            missing.append(key)
            print(f"❌ {key} 缺失：{fname}")
            continue
        issues = check_file(fpath)
        size = os.path.getsize(fpath)
        if issues:
            all_issues.append((key, fname, issues))
            print(f"⚠ {key} {fname}（{size}字节）：{'; '.join(issues)}")
        else:
            print(f"✅ {key} {fname}（{size}字节）：通过")

    # ---- 交付指纹（v1.34.0）----
    fp_issues, fp_present = check_fingerprint(out_dir, expected)
    if fp_issues:
        all_issues.append(("指纹", "00/01/02/03", fp_issues))
        print("❌ 交付指纹不符：")
        for s in fp_issues:
            print(f"   - {s}")
    elif fp_present:
        print("✅ 交付指纹：00/01/02/03 与 assemble 定稿一致（无 assemble 后手改）")
    else:
        print("⚠ 交付指纹：04 无指纹行（v1.34.0 之前的旧交付件，或 04 被替换）——本项未核验")

    # ---- 图引用实体（v1.34.0）----
    img_issues = check_images(out_dir, expected)
    if img_issues:
        all_issues.append(("图引用", expected["03"], img_issues))
        print(f"❌ 图引用：{img_issues[0]}")
    else:
        print("✅ 图引用：03 引用的图均在交付目录内（或无图引用）")

    # 汇总
    print("\n" + "=" * 68)
    if missing:
        print(f"❌ 缺失 {len(missing)} 件：{missing}")
    if all_issues:
        print(f"⚠ {len(all_issues)} 件有问题")
        for key, fname, issues in all_issues:
            print(f"  - {key}: {'; '.join(issues)}")
    if not missing and not all_issues:
        print("✅ 全部六件齐全，无BOM/CRLF/占位符残留，指纹与图引用一致，交付验证通过")
        print("=" * 68)
        sys.exit(0)
    else:
        print("❌ 交付验证未通过，须修正后重跑")
        print("=" * 68)
        sys.exit(1)


if __name__ == "__main__":
    main()
