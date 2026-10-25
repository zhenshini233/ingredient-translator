#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配料表翻译官 · 自检脚本

一条命令验证安装与数据完整性（零依赖，Python 3.7+）：
    python scripts/selftest.py

覆盖三层：
  1. 数据库完整性（字段齐全、无重名、无别名冲突、what 不超 40 字）
  2. 解析器金样本（嵌套括号/功能类别/营养素容器/OCR 错字/误配防护）
  3. 端到端评分回归（demo 样本分数与金标准一致）

任一失败即退出码 1，并打印失败明细。
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import scan  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name} {detail}")
        FAILURES.append(name)


def _key(s):
    return re.sub(r"[\s\-–—_·.、,（）()]", "", s.lower())


def test_db():
    print("[1/3] 数据库完整性")
    base = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
    db = json.load(open(os.path.join(base, "references", "additives.json"), encoding="utf-8"))
    entries = db["entries"]
    check("version 字段存在", bool(db.get("version")), f"当前: {db.get('version')!r}")
    check(f"条目数 >= 350（当前 {len(entries)}）", len(entries) >= 350)

    names = [e["name"] for e in entries]
    dups = sorted(n for n in set(names) if names.count(n) > 1)
    check("无重名条目", not dups, str(dups))

    bad_schema = [e["name"] for e in entries
                  if not (e.get("name") and e.get("cat") and e.get("kind") in ("additive", "sugar", "fat", "ingredient")
                          and e.get("risk") in (0, 1, 2) and e.get("what"))]
    check("字段齐全（name/cat/kind/risk/what）", not bad_schema, str(bad_schema[:5]))

    bad_len = [(e["name"], len(e["what"])) for e in entries if len(e["what"]) > 40]
    check("what 均 ≤ 40 字", not bad_len, str(bad_len[:5]))

    seen = {}
    alias_conflicts = []
    for e in entries:
        for a in [e["name"]] + e.get("aliases", []):
            k = _key(a)
            if k in seen and seen[k] != e["name"]:
                alias_conflicts.append(f"{a}->{seen[k]}&{e['name']}")
            seen[k] = e["name"]
    check("别名无跨条目冲突", not alias_conflicts, str(alias_conflicts[:5]))

    for cfg in ("trans_fat_keywords", "nitrite_names", "allergens"):
        check(f"配置段 {cfg} 存在", cfg in db)


def test_parser_and_resolve():
    print("[2/3] 解析器与别名归一（金样本）")

    def items_of(text):
        return scan.analyze(text)["items"]

    def resolved(text):
        return {i["name"]: (i["entry"]["name"] if i["entry"] else None) for i in items_of(text)}

    # 嵌套括号 + 备注
    r = resolved("食品添加剂（阿斯巴甜（含苯丙氨酸））")
    check("嵌套备注保留且正确解析", r.get("阿斯巴甜") == "阿斯巴甜", str(r))

    # 功能类别引导
    r = resolved("水，增稠剂（卡拉胶，刺槐豆胶）")
    check("功能类别展开子项", r.get("卡拉胶") == "卡拉胶" and r.get("刺槐豆胶") == "刺槐豆胶", str(r))

    # 营养素容器（2026-10 实拍修复项）
    r = resolved("小麦粉，维生素（醋酸维生素A，核黄素），矿物质（碳酸钙，焦磷酸铁）")
    check("维生素/矿物质容器只留具体品种",
          r.get("醋酸维生素A") == "维生素A" and r.get("核黄素") == "维生素B2"
          and r.get("碳酸钙") == "碳酸钙", str(r))
    check("营养素不计入添加剂", scan.resolve("维生素") is None and scan.resolve("B族维生素") is None)

    # OCR 错字
    check("OCR 错字：黄元胶→黄原胶", scan.resolve("黄元胶") and scan.resolve("黄元胶")["name"] == "黄原胶")
    check("OCR 错字：安塞蜜→安赛蜜", scan.resolve("安塞蜜") and scan.resolve("安塞蜜")["name"] == "安赛蜜")

    # 误配防护
    check("淀粉不误配变性淀粉", scan.resolve("淀粉") is None)
    check("E492 不再误配吐温65", scan.resolve("山梨醇酐三硬脂酸酯")["name"] == "山梨醇酐三硬脂酸酯")
    check("E 编号可查", scan.resolve("E211") and scan.resolve("E211")["name"] == "苯甲酸钠")

    # 复合配料展开
    its = items_of("植脂末（葡萄糖浆，氢化植物油）")
    check("复合配料展开子项", any(i["parent"] == "植脂末" for i in its), str([i["name"] for i in its]))


def test_end_to_end():
    print("[3/3] 端到端评分回归（demo 金标准）")
    base = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
    golden = {
        "sample_tea.txt": (5, "绿灯"),
        "sample_yogurt.txt": (30, "黄灯"),
        "sample_spicy_snack.txt": (48, "橙灯"),
        "sample_milk_drink.txt": (66, "橙灯"),
    }
    for fname, (score, tier) in golden.items():
        text = open(os.path.join(base, "demo", fname), encoding="utf-8").read()
        res = scan.analyze(text)
        check(f"{fname} = {score}分/{tier}", res["score"] == score and res["tier"] == tier,
              f"实际 {res['score']}分/{res['tier']}")
        check(f"{fname} 无未收录项", res["counts"]["unknown"] == 0)


def main():
    print(f"配料表翻译官 · 自检（数据库版本 {scan.DB.get('version', '?')}，共 {len(scan.ENTRIES)} 条）\n")
    test_db()
    print()
    test_parser_and_resolve()
    print()
    test_end_to_end()
    print()
    if FAILURES:
        print(f"结果：{len(FAILURES)} 项失败 —— {FAILURES}")
        sys.exit(1)
    print("结果：全部通过 ✅")


if __name__ == "__main__":
    main()
