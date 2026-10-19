#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_error_forms.py — 从 corrections 台账自动生成 error_forms 候选清单

用途：分章精读完成后、assemble 之前，从 corrections/*.json 中提取「值得全书复扫的错形」，
去重后在 fulltext_struct.txt 中扫描出现次数、剔除底本 0 命中项，输出候选清单供人工确认。
确认后重命名为 error_forms.txt，供 assemble 的错字清单复扫使用（报告项：只点名残留，不拦交付）。

用法：
  python scripts/gen_error_forms.py --work <work目录>

输出：
  <work>/error_forms.candidates.txt — 候选清单（含出现次数佐证）

设计原则：
  - 收录范围（与代码一致，2026-10-19 校正文档）：
      ① systematic:true 的补丁全收——已由 Agent 判层，是"全书高频同形误"。
      ② 逐页（per-phys）补丁只收 old ≥2 字的——高频错形（如「基志」）本就该被
         全书复扫抓住，而裸单字/双字常用字进清单会制造成片误报。
     旧版本此处写「只提取 systematic、零散 per-phys 不提取」，与实际行为不符：
     v1.16.2 起已放开 per-phys（≥2 字），是为了不让 per-phys 里的高频错形漏检。
  - 去重时保留 reason 最完整的一条
  - 在 fulltext_struct.txt（未纠错底本）中扫描出现次数，作为"高频"佐证
  - 输出前剔除 old 在底本中 0 命中的候选（跨行上下文切断 / 正则原文无字面命中）——
    与 assemble 的方向校验（wrong not in struct）同口径，免人工精简轮次；剔除数在 stdout 报告
  - 输出候选而非直接写 error_forms.txt——有歧义的错字对（如"孙琳"可能是另一人名）
    须人工语境确认后才能进入正式清单
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime


# read_json_cn 已收敛到 _common（2026-10-04 审查 P2-15），本文件通过顶部 import 引用。
from _common import read_json_cn, read_text_cn, expand_patch_data


def main():
    ap = argparse.ArgumentParser(description="从 corrections 台账生成 error_forms 候选清单")
    ap.add_argument("--work", required=True, help="本书 work 目录（含 corrections/ 和 fulltext_struct.txt）")
    ap.add_argument("--inherit", nargs="*", default=[], help="P1-E2：跨卷继承——已完成卷的 error_forms.txt 路径（可多个），高频项自动加入候选并标注「跨卷继承」")
    args = ap.parse_args()
    B = args.work

    # 1. 读取所有 corrections 文件，提取 systematic 补丁
    corr_files = sorted(glob.glob(os.path.join(B, "corrections", "*.json")))
    if not corr_files:
        print("ERROR: corrections/ 为空，无法生成候选清单", file=sys.stderr)
        sys.exit(1)

    candidates = {}  # old -> {new, reason, source_file}
    n_sys_total = 0
    for cf in corr_files:
        try:
            patches = read_json_cn(cf)
        except Exception as e:
            print(f"WARNING: 无法读取 {os.path.basename(cf)}: {e}", file=sys.stderr)
            continue
        # v1.16.2 兼容包裹层；2026-10-05 收敛到 _common.expand_patch_data 单一真相源
        patches = expand_patch_data(patches) if isinstance(patches, (dict, list)) else []
        for p in patches:
            if not isinstance(p, dict):
                continue
            if p.get("merge"):
                continue
            old = (p.get("old") or "").strip()
            new = (p.get("new") or "").strip()
            if not old or not new:
                continue
            systematic = bool(p.get("systematic")) or str(p.get("phys", "")) == "ALL"
            # v1.16.2（修复环5 闭环自证）：systematic 通改全收；
            # per-phys 补丁只收 old≥2 字的有意义错形（裸单字/双字常用字会误报，
            # 已由补丁回放逐补丁校验覆盖，不进 error_forms）。这样 per-phys 高频错形（如「基志」）
            # 也能进清单被错字清单复扫检测，不再漏检。
            if not systematic and len(old) < 2:
                continue
            n_sys_total += 1
            reason = (p.get("reason") or "").strip()
            src = os.path.basename(cf)
            # 去重：同一 old 保留 reason 更长的一条
            if old not in candidates or len(reason) > len(candidates[old]["reason"]):
                candidates[old] = {"new": new, "reason": reason, "source": src}

    if not candidates:
        print("WARNING: corrections/ 中无 systematic 补丁，候选清单为空", file=sys.stderr)
        # 仍然输出空候选文件（含头部说明），供主控方手动补充

    # 2. 在 fulltext_struct.txt 中扫描每个 old 的出现次数（高频佐证）
    struct_path = os.path.join(B, "fulltext_struct.txt")
    struct_text = ""
    if os.path.isfile(struct_path):
        with open(struct_path, encoding="utf-8") as f:
            struct_text = f.read()
    else:
        print(f"WARNING: {struct_path} 不存在，跳过出现次数扫描", file=sys.stderr)

    # ---- P1 改造9：OCR 混淆词典扫描（打破 N3 循环论证）----
    # 仅从已有台账提取会漏掉「Agent 漏掉的错字」；此处独立扫 ocr_confusion_dict 的
    # compound 错形对 fulltext_struct 的命中，把漏网复合错形反向补为候选（标注「词典扫描」）。
    # 裸单字类（char_class）不入候选——错字清单复扫不查裸单字、且裸单字易误报，须走台账复合补丁。
    try:
        from ocr_confusion_dict import COMPOUND
        n_dict = 0
        for wrong, right in COMPOUND.items():
            if not wrong or wrong in candidates:
                continue
            cnt = struct_text.count(wrong) if struct_text else 0
            if cnt > 0:
                candidates[wrong] = {"new": right,
                                     "reason": f"词典扫描：struct命中{cnt}处（来源：ocr_confusion_dict）",
                                     "source": "ocr_confusion_dict"}
                n_dict += 1
        if n_dict:
            print(f"  词典扫描补充 {n_dict} 条候选（标注「词典扫描」，详见输出）")
    except Exception as e:
        print(f"WARNING: 词典扫描失败（{e}），跳过", file=sys.stderr)

    # ---- 术语字典提示扫描（resources/term_dict.json）----
    # term_dict 的「OCR 错形对照」是**提示性**资源：同一错形常有歧义（如「重氏」既可能是
    # 「孟氏」也可能是「贾林」），故此处只把 struct 命中的错形补为**候选**，依据栏标注
    # 「术语字典提示」，由人工按语境 / 引文反查确认后才可进入 error_forms.txt。
    # 本步骤不做任何自动替换——定案权始终在人工（见 SKILL.md §3.2 / §3.3）。
    try:
        td_path = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "resources", "term_dict.json"))
        n_td = 0
        if struct_text and os.path.isfile(td_path):
            td = read_json_cn(td_path)
            for bucket, body in (td.items() if isinstance(td, dict) else []):
                if str(bucket).startswith("_") or not isinstance(body, dict):
                    continue  # 元键（_version/_说明/_使用约定）与书型桶外的杂项
                for key, val in body.items():
                    if "错形" not in str(key) or not isinstance(val, dict):
                        continue  # 「标准清单」是 list（人工参照源），不参与候选扫描
                    for right, wrongs in val.items():
                        if not isinstance(wrongs, list):
                            continue
                        for wrong in wrongs:
                            wrong = (wrong or "").strip()
                            if not wrong or wrong in candidates:
                                continue
                            cnt = struct_text.count(wrong)
                            if cnt > 0:
                                candidates[wrong] = {
                                    "new": right,
                                    "reason": "术语字典提示：struct命中%d处（错形可能有歧义，须语境/引文反查确认）" % cnt,
                                    "source": "term_dict"}
                                n_td += 1
            if n_td:
                print(f"  术语字典补充 {n_td} 条提示性候选（标注「术语字典提示」，须人工语境确认）")
    except Exception as e:
        print(f"WARNING: 术语字典扫描失败（{e}），跳过", file=sys.stderr)

    # 3. 排除跨行形 / 底本 0 命中候选（v1.25.0 · 2026-10-11）
    # corrections 来源的 per-phys 补丁 old 常带上下文，若上下文跨行切断，
    # plain-count 在 struct 中 0 命中——进 error_forms 后被错字清单方向校验
    # 误报为「凭空制造」，须人工精简消除。自动过滤，减少假阳性轮次。
    # P1-E2（v1.27.0）跨卷继承：从已完成卷的 error_forms.txt 提取高频项，加入候选并标注「跨卷继承」
    n_inherited = 0
    for inherit_path in args.inherit:
        if not os.path.isfile(inherit_path):
            print(f"WARNING: 继承文件不存在：{inherit_path}", file=sys.stderr)
            continue
        inherit_text = read_text_cn(inherit_path)
        for line in inherit_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "|" not in line:
                continue
            parts = line.split("|")
            if len(parts) < 2:
                continue
            wrong = parts[0].strip()
            right = parts[1].strip()
            if not wrong or not right or wrong in candidates:
                continue
            # 只继承复合错形（≥2字），裸单字易误报
            if len(wrong) < 2:
                continue
            candidates[wrong] = {
                "new": right,
                "reason": f"跨卷继承（来源：{os.path.basename(inherit_path)}）·待本书确认",
                "source": "跨卷继承",
            }
            n_inherited += 1
    if n_inherited:
        print(f"  跨卷继承：从 {len(args.inherit)} 个文件继承 {n_inherited} 条高频错形（标注「跨卷继承」，须本书语境确认）")

    # dict / term_dict 来源已在采集时做 cnt>0 过滤，此处统一再验一遍。
    n_crossline_skip = 0
    if struct_text:
        _before = len(candidates)
        candidates = {k: v for k, v in candidates.items() if k in struct_text}
        n_crossline_skip = _before - len(candidates)

    # 3b. P1-B4（v1.27.0）同族聚类：同一正字的多种OCR错形自动聚类，推荐升级为 systematic 通改
    same_new_groups = {}  # new -> [(old, count), ...]
    for old, info in candidates.items():
        new = info["new"]
        if not new:
            continue
        cnt = struct_text.count(old) if struct_text else 0
        same_new_groups.setdefault(new, []).append((old, cnt))
    # 只保留正字≥2种错形的族（单一错形无需聚类推荐）
    cluster_recommendations = {new: olds for new, olds in same_new_groups.items() if len(olds) >= 2}

    # 4. 输出候选清单
    out_path = os.path.join(B, "error_forms.candidates.txt")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 高频错字清零候选清单（自动生成，须人工确认后重命名为 error_forms.txt）",
        "# 格式：错误形态|正确形态|判定依据",
        f"# 生成时间：{now}",
        f"# 来源：corrections/ 台账 systematic 补丁（共 {n_sys_total} 条，去重后 {len(candidates)} 项）",
        "#",
        "# 【人工确认要点】",
        "# 1. 错误形态必须是「OCR 专有错误」——该字形不会作为正确字出现（如「荀或」不是任何人名）；",
        "# 2. 有歧义的错字对（如「孙琳」理论上可能是另一人名）须语境核实——同段已有正确形态可证、",
        "#    或全书正确形态占绝对多数（同书反证法），才能确认是 OCR 误读；",
        "# 3. 确认后删除本行及以上注释，重命名为 error_forms.txt；",
        "# 4. 可手动补充 corrections 中未覆盖的高频错字（Agent 漏掉的）。",
        "# 5. 标注「词典扫描」的候选来自 ocr_confusion_dict 独立基准（非台账反推），用于补全 Agent 漏掉的复合错形；",
        "#    裸单字类不入候选（须走台账复合补丁，避免误报）。",
        "#",
    ]

    # P1-B4：同族推荐段落（同一正字≥2种错形，建议合并为一条 systematic 通改）
    if cluster_recommendations:
        lines.append("# 【同族推荐通改】以下正字有多种OCR错形，建议合并为一条 systematic 通改（人工确认后）：")
        for new, olds in sorted(cluster_recommendations.items(), key=lambda x: -len(x[1])):
            total = sum(c for _, c in olds)
            old_list = "、".join(f"「{o}」({c}处)" for o, c in sorted(olds, key=lambda x: -x[1]))
            lines.append(f"#   →「{new}」：{len(olds)}种错形共{total}处 —— {old_list}")
        lines.append("#")

    for old, info in sorted(candidates.items()):
        cnt = struct_text.count(old) if struct_text else -1
        cnt_note = f"struct中出现{cnt}处" if cnt >= 0 else "出现次数未知"
        reason = info["reason"] or "systematic通改"
        # reason 中可能已含括号，避免重复
        if "（" in reason:
            full_reason = f"{reason}；{cnt_note}（来源：{info['source']}）"
        else:
            full_reason = f"{reason}（{cnt_note}；来源：{info['source']}）"
        lines.append(f"{old}|{info['new']}|{full_reason}")

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    print(f"已生成候选清单：{out_path}")
    print(f"  systematic 补丁：{n_sys_total} 条 → 去重后 {len(candidates)} 项")
    if n_crossline_skip:
        print(f"  已排除跨行形/底本0命中候选 {n_crossline_skip} 项（plain-count 在 struct 不命中，进清单会误报方向错误）")
    if struct_text:
        print(f"  出现次数已扫描（fulltext_struct.txt）")
    print(f"  下一步：人工确认后重命名为 error_forms.txt，然后跑 assemble（清单复扫为报告项，只点名不拦交付）")


if __name__ == "__main__":
    main()
