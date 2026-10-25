#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 additives.json 生成 references/quick_reference.md（无脚本环境的降级速查表）。

数据库更新后重新运行以保持同步：
    python scripts/gen_quick_ref.py
"""

import json
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
DB_PATH = os.path.join(BASE, "references", "additives.json")
OUT_PATH = os.path.join(BASE, "references", "quick_reference.md")

RISK_ICON = {0: "🟢", 1: "🟡", 2: "🔴"}
CAT_ORDER = [
    "防腐剂", "抗氧化剂", "着色剂", "护色剂", "漂白剂", "甜味剂", "增稠剂",
    "乳化剂", "稳定剂", "膨松剂", "酸度调节剂", "抗结剂", "水分保持剂",
    "增味剂", "面粉处理剂", "被膜剂", "消泡剂", "酶制剂", "香精香料",
]


def esc(s):
    return s.replace("|", "\\|").replace("\n", " ")


def main():
    db = json.load(open(DB_PATH, encoding="utf-8"))
    entries = db["entries"]
    version = db.get("version", "?")

    L = []
    L.append("# 速查表（无脚本降级路径）")
    L.append("")
    L.append(f"> 本文件由 `scripts/gen_quick_ref.py` 从 additives.json 自动生成（数据库版本 {version}），"
             "供宿主 Agent **无法运行脚本**时降级使用。能用脚本就优先用脚本："
             "`python scripts/scan.py --text \"配料表\"`。")
    L.append("")
    L.append("## 降级评估流程")
    L.append("")
    L.append("1. 拿到配料表文本（转写规则见 SKILL.md：只取配料栏、保持顺序、保留括号）。")
    L.append("2. 按逗号/顿号切分顶层配料，括号内的子配料单独切出（复合配料的子项单算，但不占顶层位次）。")
    L.append("3. 逐项在下表查风险级（🟢0/🟡1/🔴2）；查不到且名字像添加剂（以“剂/香精/香料/色素/酯/酸钠/胶…”结尾或为 E 编号）记“未收录疑似添加剂”。")
    L.append("4. 按下方计分表累加得近似狠活指数，对照灯档。")
    L.append("5. 输出时**明确告知**：本次为降级近似评估，建议在有脚本能力的环境复核。")
    L.append("")
    L.append("## 计分表（与 scan.py 一致）")
    L.append("")
    L.append("| 规则 | 分值 | 上限 |")
    L.append("|---|---|---|")
    L.append("| 添加剂每项 | +2.5 | 20 |")
    L.append("| 留意级（🟡）每项 | +4 | — |")
    L.append("| 特别关注级（🔴）每项 | +12 | — |")
    L.append("| 糖类排在第 1–3 位 | +10 | — |")
    L.append("| 糖类排在第 4–5 位 | +5 | — |")
    L.append("| 反式脂肪风险配料 | +20 | — |")
    L.append("| 糖排首位 + 反式风险（组合红旗） | +5 | — |")
    L.append("| 亚硝酸盐类护色剂 | +8 | — |")
    L.append("| 未收录疑似添加剂每项 | +2 | 6 |")
    L.append("")
    L.append("总分四舍五入、封顶 100。灯档：≤20 🟢绿 / ≤45 🟡黄 / ≤70 🟠橙 / >70 🔴红。")
    L.append("")
    L.append("## 红旗关键词与名单")
    L.append("")
    L.append(f"- **反式脂肪风险**（原料名命中即 +20）：{'、'.join(db.get('trans_fat_keywords', []))}")
    L.append(f"- **亚硝酸盐类**（命中即 +8）：{'、'.join(db.get('nitrite_names', []))}")
    sugar_names = [e["name"] for e in entries if e["kind"] == "sugar"]
    fat_names = [e["name"] for e in entries if e["kind"] == "fat"]
    L.append(f"- **常见糖类配料**（位次计分用）：{'、'.join(sugar_names)}")
    L.append(f"- **常见油脂配料**：{'、'.join(fat_names)}")
    L.append("")

    groups = {}
    for e in entries:
        if e["kind"] != "additive":
            continue
        groups.setdefault(e["cat"], []).append(e)
    ordered = [c for c in CAT_ORDER if c in groups] + sorted(c for c in groups if c not in CAT_ORDER)

    L.append("## 添加剂速查（按功能类别）")
    L.append("")
    for cat in ordered:
        L.append(f"### {cat}")
        L.append("")
        L.append("| 名称 | 级 | 说明 |")
        L.append("|---|---|---|")
        for e in sorted(groups[cat], key=lambda x: (x["risk"], x["name"])):
            en = f"{e['name']}（{e['e']}）" if e.get("e") else e["name"]
            what = esc(e["what"])
            if e.get("care"):
                what += "〔" + esc(e["care"]) + "〕"
            L.append(f"| {esc(en)} | {RISK_ICON[e['risk']]} | {what} |")
        L.append("")

    L.append("## 口径红线（降级模式同样适用）")
    L.append("")
    L.append("- 衡量配料复杂程度，**不是食品安全结论**；禁用“致癌/有毒/绝对不能吃”等断言。")
    L.append("- 未收录 ≠ 有害，如实标注“以包装标注为准”。")
    L.append("- 过敏原按配料名推断的软提示，提醒以包装过敏原标示为准。")
    L.append("- 不虚构国标限量数字，不做针对具体产品的健康建议。")
    L.append("")

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    n_rows = sum(len(v) for v in groups.values())
    print(f"已生成 {OUT_PATH}（{n_rows} 条添加剂，数据库版本 {version}）")


if __name__ == "__main__":
    main()
