#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配料表翻译官 —— 主入口。

用法（任意工作目录均可）：
  python scan.py --text "水,白砂糖,食品添加剂(柠檬酸,食用香精)" [--name "产品名"] [--json]
  python scan.py 配料表.txt [--name "产品名"] [--json]

输出：Markdown 报告（默认）或 JSON（--json）。
评分规则见 references/scoring_rules.md。纯标准库，零依赖。
"""

import argparse
import difflib
import html as _html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from label_parser import parse_label, looks_like_additive  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.normpath(os.path.join(BASE, os.pardir, "references", "additives.json"))

with open(DB_PATH, encoding="utf-8") as fh:
    DB = json.load(fh)
ENTRIES = DB["entries"]

RISK_ICON = {0: "🟢", 1: "🟡", 2: "🔴"}
RISK_LABEL = {0: "常规安心", 1: "留意", 2: "特别关注"}


# ---------------------------------------------------------------- 查询索引

def _key(s):
    return re.sub(r"[\s\-–—_·.、,（）()]", "", s.lower())


BY_KEY = {}
BY_NAME = {}
for _e in ENTRIES:
    BY_NAME[_key(_e["name"])] = _e
    for _a in [_e["name"], _e.get("en", ""), _e.get("e", "")] + _e.get("aliases", []):
        _k = _key(_a)
        if _k and _k not in BY_KEY:
            BY_KEY[_k] = _e


def resolve(name):
    """别名归一：精确 -> 包含（最长优先）-> 模糊（容忍 OCR 错字）。"""
    kk = _key(name)
    if not kk:
        return None
    if kk in BY_KEY:
        return BY_KEY[kk]
    best = None
    for key, entry in BY_KEY.items():
        # 方向一：已知别名(≥2字)出现在未知名里（如"优质果胶"含"果胶"）
        # 方向二：未知名(≥4字)完整出现在已知别名里（防止"淀粉"误配"二淀粉磷酸酯"）
        if len(key) >= 2 and key in kk or (len(kk) >= 4 and kk in key):
            if best is None or len(key) > len(best[0]):
                best = (key, entry)
    if best:
        return best[1]
    # 模糊匹配（容忍 OCR 错字）：候选含所有别名 key；
    # 相似度同分时按字符交集破平（"维生紊C"→维生素C 而非 维生素A）
    m = difflib.get_close_matches(kk, list(BY_KEY.keys()), n=5, cutoff=0.74)
    if m:
        best = max(m, key=lambda k: (
            difflib.SequenceMatcher(None, kk, k).ratio(),
            len(set(kk) & set(k)),
        ))
        return BY_KEY[best]

    # 短词宽松匹配：3-4 字配料名的单字 OCR 错字
    # （首字相同、长度差≤1、匹配字符数 ≥ 短边长度-1，如"黄元胶"→黄原胶）
    if 3 <= len(kk) <= 4:
        cands = []
        for key, entry in BY_KEY.items():
            if len(key) >= 3 and abs(len(key) - len(kk)) <= 1 and key[0] == kk[0]:
                sm = difflib.SequenceMatcher(None, kk, key)
                matched = sum(b.size for b in sm.get_matching_blocks())
                if matched >= min(len(kk), len(key)) - 1:
                    cands.append((matched, len(key), entry))
        if cands:
            return max(cands, key=lambda t: (t[0], t[1]))[2]
    return None


# ---------------------------------------------------------------- 分析

def analyze(text):
    raw_items = parse_label(text)
    if not raw_items:
        return None

    items = []
    for it in raw_items:
        entry = resolve(it["name"])
        kind = entry["kind"] if entry else (
            "additive" if looks_like_additive(it["name"], it.get("declared_class")) else "ingredient"
        )
        items.append({
            **it,
            "entry": entry,
            "kind": kind,
            "display": (f"{it['parent']} › " if it.get("parent") else "") + it["name"],
        })

    additives = [i for i in items if i["kind"] == "additive"]
    sugars = [i for i in items if i["kind"] == "sugar"]
    fats = [i for i in items if i["kind"] == "fat"]
    mains = [i for i in items if i["kind"] == "ingredient"]
    unknowns = [i for i in additives if i["entry"] is None]
    known_additives = [i for i in additives if i["entry"] is not None]

    risk1 = sum(1 for i in known_additives if i["entry"]["risk"] == 1)
    risk2 = sum(1 for i in known_additives if i["entry"]["risk"] == 2)
    risk2_names = [i["entry"]["name"] for i in known_additives if i["entry"]["risk"] == 2]

    # 反式脂肪红旗：按原料名关键字匹配（含复合配料的父项与子项）
    trans_kw = DB.get("trans_fat_keywords", [])
    trans_hits = []
    for i in items:
        for kw in trans_kw:
            if kw in i["name"] or (i["entry"] and kw in i["entry"]["name"]):
                trans_hits.append(i["display"])
                break

    # 糖位次（仅顶层配料参与排序）
    top_sugar_pos = None
    for i in sugars:
        if not i.get("parent"):
            if top_sugar_pos is None or i["pos"] < top_sugar_pos:
                top_sugar_pos = i["pos"]

    # 过敏原（启发式，软提示）
    allergens = []
    for a in DB.get("allergens", []):
        hit = False
        for i in items:
            nm = i["name"]
            if any(m in nm for m in a["match"]) and not any(x in nm for x in a.get("exclude", [])):
                hit = True
                break
        if hit:
            allergens.append(a["key"])

    nitrite_names = set(DB.get("nitrite_names", []))
    nitrite_hits = [i["display"] for i in known_additives if i["entry"]["name"] in nitrite_names]

    # ---- 评分（确定性规则，详见 references/scoring_rules.md）
    score = 0.0
    breakdown = []
    n_add = len(additives)
    pts = min(20.0, n_add * 2.5)
    if pts:
        score += pts
        breakdown.append(f"添加剂数量 {n_add} 项 → +{pts:.1f}")
    pts = risk1 * 4 + risk2 * 12
    if pts:
        score += pts
        breakdown.append(f"留意级 {risk1} 项、特别关注级 {risk2} 项 → +{pts}")
    if top_sugar_pos is not None:
        if top_sugar_pos <= 3:
            score += 10
            breakdown.append(f"糖类配料排在第 {top_sugar_pos} 位（含量不低）→ +10")
        elif top_sugar_pos <= 5:
            score += 5
            breakdown.append(f"糖类配料排在第 {top_sugar_pos} 位 → +5")
    if trans_hits:
        score += 20
        breakdown.append(f"含反式脂肪风险配料（{'、'.join(sorted(set(trans_hits)))[:40]}）→ +20")
    if trans_hits and top_sugar_pos == 1:
        score += 5
        breakdown.append("糖排首位且含反式脂肪风险配料（组合红旗）→ +5")
    if nitrite_hits:
        score += 8
        breakdown.append("含亚硝酸盐类护色剂（腌腊工艺常见）→ +8")
    if unknowns:
        pts = min(6, len(unknowns) * 2)
        score += pts
        breakdown.append(f"未收录的疑似添加剂 {len(unknowns)} 项 → +{pts}")
    score = int(round(min(100, score)))

    if score <= 20:
        tier, verdict = "绿灯", "配料简单干净：以天然原料为主，添加剂少且常规，可以放心作为日常选择。"
    elif score <= 45:
        tier, verdict = "黄灯", "配料平常：属于常规工业化食品水平，没有明显红旗项，日常适量即可。"
    elif score <= 70:
        tier, verdict = "橙灯", "配料偏复杂：添加剂较多或含值得留意的成分，建议对照营养成分表再权衡，控制频次。"
    else:
        tier, verdict = "红灯", "配料很复杂：添加剂密集，且/或含反式脂肪风险、高糖等红旗项，建议作为偶尔解馋的选项。"

    return {
        "items": items,
        "counts": {
            "additive": n_add,
            "sugar": len(sugars),
            "fat": len(fats),
            "ingredient": len(mains),
            "unknown": len(unknowns),
        },
        "risk1": risk1,
        "risk2": risk2,
        "risk2_names": risk2_names,
        "trans_hits": sorted(set(trans_hits)),
        "top_sugar_pos": top_sugar_pos,
        "allergens": allergens,
        "nitrite_hits": nitrite_hits,
        "unknowns": unknowns,
        "known_additives": known_additives,
        "sugars": sugars,
        "fats": fats,
        "mains": mains,
        "score": score,
        "tier": tier,
        "verdict": verdict,
        "breakdown": breakdown,
        "db_version": DB.get("version", ""),
    }


# ---------------------------------------------------------------- 渲染

TIER_ICON = {"绿灯": "🟢", "黄灯": "🟡", "橙灯": "🟠", "红灯": "🔴"}
TIER_HEX = {"绿灯": "#22c55e", "黄灯": "#e0a800", "橙灯": "#f97316", "红灯": "#ef4444"}


def _short_notes(res):
    """卡片用的短提示（纯文本，最多 4 条）。"""
    notes = []
    if res["trans_hits"]:
        notes.append("🚩 反式脂肪风险：" + "、".join(res["trans_hits"]))
    if res["top_sugar_pos"] is not None and res["top_sugar_pos"] <= 3:
        notes.append(f"🍬 糖类配料排在第 {res['top_sugar_pos']} 位，含量不低")
    if res["nitrite_hits"]:
        notes.append("🥓 含亚硝酸盐护色剂，加工肉制品建议适量")
    if res["risk2_names"]:
        notes.append("🔴 特别关注：" + "、".join(res["risk2_names"]))
    if res["allergens"]:
        notes.append("⚠️ 可能涉及过敏原：" + "、".join(res["allergens"]) + "（以包装为准）")
    if res["unknowns"]:
        notes.append(f"❓ {len(res['unknowns'])} 项未收录，以包装标注为准")
    return notes[:4]


def render_card(res, product=None):
    """渲染 HTML 分享卡片（小红书 3:4 风格，可截图传播）。"""
    tpl_path = os.path.normpath(os.path.join(BASE, os.pardir, "templates", "report_card.html"))
    with open(tpl_path, encoding="utf-8") as fh:
        tpl = fh.read()

    esc = _html.escape
    rows = []
    notable = res["known_additives"] + res["sugars"] + res["fats"]
    for i in notable[:8]:
        e = i["entry"]
        cat = i.get("declared_class") or e["cat"]
        rows.append(
            f"<tr><td class='name'>{esc(RISK_ICON[e['risk']] + ' ' + i['display'])}</td>"
            f"<td class='cat'>{esc(cat)}</td><td class='what'>{esc(e['what'])}</td></tr>"
        )
    for i in res["unknowns"][:2]:
        rows.append(
            f"<tr><td class='name'>❓ {esc(i['display'])}</td>"
            f"<td class='cat'>未收录</td><td class='what'>数据库暂未收录，以包装标注为准</td></tr>"
        )
    if len(notable) + len(res["unknowns"]) > 10:
        rows.append(f"<tr><td colspan='3' class='more'>……其余 {len(notable) + len(res['unknowns']) - 10} 项见完整报告</td></tr>")

    notes_html = "".join(f"<div class='note'>{esc(n)}</div>" for n in _short_notes(res)) or \
        "<div class='note'>没有触发红旗项，常规水平。</div>"

    out = (tpl
           .replace("{{PRODUCT}}", esc(product or "未命名产品"))
           .replace("{{SCORE}}", str(res["score"]))
           .replace("{{TIER}}", res["tier"])
           .replace("{{TIER_COLOR}}", TIER_HEX[res["tier"]])
           .replace("{{VERDICT}}", esc(res["verdict"]))
           .replace("{{TABLE_ROWS}}", "".join(rows))
           .replace("{{NOTABLES}}", notes_html)
           .replace("{{FOOTER}}",
                    esc(f"狠活指数衡量配料复杂程度，不等于食品安全结论 · 数据依据 GB 2760 等公开资料（版本 {res['db_version']}）"
                        " · 仅供参考，不构成健康建议，以包装标注和现行国标为准")))
    return out


def render(res, product=None):
    c = res["counts"]
    lines = []
    title = f"配料表报告 · {product}" if product else "配料表报告"
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"**狠活指数：{res['score']} / 100 —— {TIER_ICON[res['tier']]} {res['tier']}**")
    lines.append("")
    lines.append(f"> {res['verdict']}")
    lines.append("")

    lines.append("## 配料总览")
    main_names = [i["name"] for i in res["mains"]][:12]
    overview = []
    if main_names:
        overview.append("主要原料：" + "、".join(main_names) + ("…" if len(res["mains"]) > 12 else ""))
    overview.append(
        f"共 {len(res['items'])} 项：食品添加剂 {c['additive']} 项"
        f"（留意级 {res['risk1']}、特别关注级 {res['risk2']}）、"
        f"糖类配料 {c['sugar']} 项、油脂配料 {c['fat']} 项、其他配料 {c['ingredient']} 项"
    )
    lines.extend(overview)
    lines.append("")

    lines.append("## 逐项解读（按包装顺序）")
    lines.append("| # | 配料 | 类别 | 它是什么 / 干什么 | 提示 |")
    lines.append("|---|------|------|------------------|------|")
    table_rows = []
    for i in res["known_additives"]:
        e = i["entry"]
        cat = i.get("declared_class") or e["cat"]
        en = f"（{e['e']}）" if e.get("e") else ""
        table_rows.append((
            i["pos"],
            f"{RISK_ICON[e['risk']]} {i['display']}",
            f"{cat}",
            e["what"],
            e["care"] or RISK_LABEL[e["risk"]],
        ))
    for i in res["sugars"] + res["fats"]:
        e = i["entry"]
        table_rows.append((
            i["pos"],
            f"{RISK_ICON[e['risk']]} {i['display']}",
            e["cat"],
            e["what"],
            e["care"] or RISK_LABEL[e["risk"]],
        ))
    for i in res["unknowns"]:
        cat = i.get("declared_class") or "未收录添加剂"
        table_rows.append((i["pos"], f"❓ {i['display']}", cat, "数据库暂未收录，无法解读。", "以包装标注为准"))
    for pos, name, cat, what, care in table_rows:
        lines.append(f"| {pos} | {name} | {cat} | {what} | {care} |")
    lines.append("")

    notes = []
    if res["trans_hits"]:
        notes.append("🚩 **反式脂肪风险配料**：" + "、".join(res["trans_hits"]) +
                     " —— 结合营养成分表“反式脂肪”一栏确认（标 0 指每 100g ≤ 0.3g）。")
    if res["top_sugar_pos"] is not None and res["top_sugar_pos"] <= 3:
        notes.append(f"🍬 **糖位次靠前**：糖类配料排在第 {res['top_sugar_pos']} 位，按国标递减排序，含量不低。")
    if res["nitrite_hits"]:
        notes.append("🥓 **含亚硝酸盐护色剂**（" + "、".join(res["nitrite_hits"]) +
                     "）：腌腊工艺必需且合法限量，加工肉制品整体建议适量。")
    if res["risk2_names"]:
        notes.append("🔴 **特别关注级成分**：" + "、".join(res["risk2_names"]) + "。")
    if res["allergens"]:
        notes.append("⚠️ **可能涉及过敏原**：" + "、".join(res["allergens"]) +
                     "（按配料名推断，请以包装上的过敏原提示为准）。")
    if res["unknowns"]:
        notes.append("❓ **未收录 " + str(len(res["unknowns"])) + " 项**：" +
                     "、".join(i["display"] for i in res["unknowns"]) + " —— 数据库为常用子集，非全集。")
    if notes:
        lines.append("## 值得注意")
        lines.extend("- " + n for n in notes)
        lines.append("")

    if res["breakdown"]:
        lines.append("## 评分明细（为什么是这个分）")
        lines.extend("- " + b for b in res["breakdown"])
        lines.append(f"- **合计：{res['score']} / 100（{res['tier']}）**")
        lines.append("")

    lines.append("---")
    lines.append("*狠活指数衡量配料复杂程度，不等于食品安全结论。数据依据 GB 2760 等公开资料整理"
                 f"（版本 {res['db_version']}），仅供参考，不构成健康建议；一切以产品包装标注和现行国标为准。*")
    return "\n".join(lines)


def to_json(res, product=None):
    def clean_item(i):
        e = i["entry"]
        return {
            "name": i["name"],
            "display": i["display"],
            "pos": i["pos"],
            "parent": i["parent"],
            "category": i.get("declared_class") or (e["cat"] if e else None),
            "kind": i["kind"],
            "risk": e["risk"] if e else None,
            "what": e["what"] if e else None,
            "care": e["care"] if e else None,
            "known": e is not None,
        }

    return {
        "product": product,
        "score": res["score"],
        "tier": res["tier"],
        "verdict": res["verdict"],
        "counts": res["counts"],
        "allergens": res["allergens"],
        "trans_fat_hits": res["trans_hits"],
        "top_sugar_pos": res["top_sugar_pos"],
        "risk2_names": res["risk2_names"],
        "breakdown": res["breakdown"],
        "items": [clean_item(i) for i in res["items"]],
        "disclaimer": "狠活指数衡量配料复杂程度，不等于食品安全结论；仅供参考，不构成健康建议。",
        "db_version": res["db_version"],
    }


def main():
    ap = argparse.ArgumentParser(description="配料表翻译官：把配料表翻译成人话")
    ap.add_argument("input", nargs="?", help="配料表文本，或包含配料表的文件路径")
    ap.add_argument("--text", dest="text", help="配料表文本（与 input 二选一）")
    ap.add_argument("--name", dest="name", default=None, help="产品名（可选，用于报告标题）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")
    ap.add_argument("--html", dest="html_path", default=None, metavar="PATH",
                    help="生成 HTML 分享卡片并保存到该路径")
    args = ap.parse_args()

    text = args.text or ""
    if not text and args.input:
        if os.path.isfile(args.input):
            with open(args.input, encoding="utf-8") as fh:
                text = fh.read()
        else:
            text = args.input
    if not text.strip():
        print("错误：未提供配料表文本。用法：python scan.py --text \"水,白砂糖,...\"", file=sys.stderr)
        sys.exit(2)

    res = analyze(text)
    if not res or not res["items"]:
        print("错误：未能从输入中解析出任何配料条目，请检查文本是否为完整配料表。", file=sys.stderr)
        sys.exit(2)

    if args.html_path:
        with open(args.html_path, "w", encoding="utf-8") as fh:
            fh.write(render_card(res, args.name))
        print(f"HTML 卡片已生成：{args.html_path}")

    if args.json:
        print(json.dumps(to_json(res, args.name), ensure_ascii=False, indent=2))
    else:
        print(render(res, args.name))


if __name__ == "__main__":
    main()
