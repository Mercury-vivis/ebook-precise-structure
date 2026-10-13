#!/usr/bin/env python3
# verify_delivery.py —— 交付一键验证（P1-F3，v1.27.0）
#
# 用法：python verify_delivery.py <OUT交付目录> [--prefix 文件名前缀]
# 检查六件齐全 / CRLF残留 / BOM残留 / 占位符残留 / 文件非空 / 换行符统一。
# 替代交付后人工逐项检查。

import os, sys, argparse, re, glob


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
        for pat in [r"<!--\s*待补充\s*-->", r"TODO", r"FIXME", r"\bXXX\b", r"待填", r"占位"]:
            matches = re.findall(pat, text)
            if matches:
                placeholders.append(f"{pat}({len(matches)}处)")
        if placeholders:
            issues.append(f"占位符残留：{', '.join(placeholders)}")
    except Exception:
        pass
    return issues


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
    prefix = args.prefix
    if not prefix:
        md_files = glob.glob(os.path.join(out_dir, "*00_*.md"))
        if md_files:
            base = os.path.basename(md_files[0])
            prefix = base.split("00_")[0]
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

    # 汇总
    print("\n" + "=" * 68)
    if missing:
        print(f"❌ 缺失 {len(missing)} 件：{missing}")
    if all_issues:
        print(f"⚠ {len(all_issues)} 件有问题")
        for key, fname, issues in all_issues:
            print(f"  - {key}: {'; '.join(issues)}")
    if not missing and not all_issues:
        print("✅ 全部六件齐全，无BOM/CRLF/占位符残留，交付验证通过")
        print("=" * 68)
        sys.exit(0)
    else:
        print("❌ 交付验证未通过，须修正后重跑")
        print("=" * 68)
        sys.exit(1)


if __name__ == "__main__":
    main()
