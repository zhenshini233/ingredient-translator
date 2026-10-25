# -*- coding: utf-8 -*-
"""配料表文本解析器：把一行配料表拆成结构化条目。

设计要点：
- 纯 Python 标准库，零依赖，任何 Agent 环境可运行。
- 兼容全角/半角标点、多种分隔符（、，,;；）。
- 括号感知：不会把括号内的子配料拆散到顶层。
- 处理国标标注习惯：
  * 功能类别引导：增稠剂（卡拉胶，刺槐豆胶） -> 两个条目，标注类别"增稠剂"
  * 复合配料展开：植脂末（葡萄糖浆，氢化植物油） -> 植脂末 + 两个子条目（带 parent）
  * 含量标注：全脂乳粉（80%） -> 条目 + percent 提示
  * 备注括号：阿斯巴甜（含苯丙氨酸） -> 条目 + remark
"""

import re

SEPARATORS = "、，,;；"

FULLWIDTH = {"（": "(", "）": ")", "：": ":", "　": " "}

# 功能类别词：括号前缀命中时，括号内为该类别的具体添加剂
FUNCTIONAL_CLASSES = {
    "防腐剂", "抗氧化剂", "甜味剂", "着色剂", "护色剂", "漂白剂", "增稠剂",
    "乳化剂", "稳定剂", "稳定和凝固剂", "酸度调节剂", "抗结剂", "膨松剂",
    "疏松剂", "水分保持剂", "被膜剂", "酶制剂", "增味剂", "面粉处理剂",
    "香精", "食用香精", "食用香料", "香料", "色素", "食用色素",
    "营养强化剂", "复配营养强化剂", "复合营养强化剂", "食品营养强化剂",
    "消泡剂", "助滤剂", "发酵用营养物质", "面团改良剂", "品质改良剂",
}

# 容器词：括号内是多个独立添加剂，不继承类别
CONTAINERS = {"食品添加剂", "复配添加剂", "复合添加剂"}

# 疑似添加剂的后缀/词形（用于未收录条目的分类判断）
ADDITIVE_SUFFIXES = ("剂", "香精", "香料", "色素", "酯", "酸钠", "酸钾", "酸钙", "酐", "苷", "胶")
E_NUMBER_RE = re.compile(r"^[eE]\s?\d{3}[a-b]?$")

PERCENT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*%\s*$")


def split_tail_paren(tok):
    """若 token 以 ) 结尾，返回 (head, 括号内容)；否则 (tok, None)。

    用括号配平扫描而非正则，支持嵌套括号，如
    食品添加剂(柠檬酸、阿斯巴甜(含苯丙氨酸))。
    """
    tok = tok.rstrip()
    if not tok.endswith(")"):
        return tok, None
    depth = 0
    for i in range(len(tok) - 1, -1, -1):
        ch = tok[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
            if depth == 0:
                return tok[:i].strip(), tok[i + 1: -1].strip()
    return tok, None

LABEL_PREFIXES = ("配料表", "配料", "成分表", "成分", "ingredients", "Ingredients")


def normalize(text):
    """全角转半角、压缩空白、去掉'配料表:'之类的引导词。"""
    for k, v in FULLWIDTH.items():
        text = text.replace(k, v)
    text = re.sub(r"[\r\n\t ]+", " ", text).strip()
    # 国标合法写法"单，双甘油脂肪酸酯"名字本身含分隔符，先归一再切分
    text = re.sub(r"单[，,、]双甘油脂肪酸酯", "单双甘油脂肪酸酯", text)
    low = text.lower()
    for p in LABEL_PREFIXES:
        if low.startswith(p.lower()):
            text = text[len(p):].lstrip(":(：) ").strip()
            break
    return text


def split_top(s):
    """按分隔符切分，跳过括号内的分隔符。"""
    parts, buf, depth = [], [], 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch in SEPARATORS and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip().rstrip("，,、;；").strip() for p in parts if p.strip().strip("，,、;；")]


def _is_functional_head(head):
    if head in FUNCTIONAL_CLASSES or head in CONTAINERS:
        return True
    return any(fc in head for fc in FUNCTIONAL_CLASSES)


def _parse_token(tok, parent=None, declared_class=None, pos=0):
    """递归解析单个 token，返回条目 dict 列表。"""
    tok = tok.strip()
    if not tok:
        return []

    item = {
        "name": tok,
        "percent": None,
        "remark": None,
        "parent": parent,
        "declared_class": declared_class,
        "pos": pos,  # 顶层序位（1 开始），复合配料的子项继承父项序位
    }

    m_head, paren = split_tail_paren(tok)
    if paren is None:
        return [item]

    head = m_head

    # 括号里是百分数 -> 含量提示
    pm = PERCENT_RE.match(paren)
    if pm:
        item["name"] = head or tok
        item["percent"] = float(pm.group(1))
        return [item]

    subs = split_top(paren)
    if not subs:
        item["name"] = head or paren
        item["remark"] = paren
        return [item]

    if not head:
        # 整个 token 就是一组括号内容，递归下钻
        out = []
        for sub in subs:
            out.extend(_parse_token(sub, parent=parent, declared_class=declared_class, pos=pos))
        return out

    if head in CONTAINERS:
        out = []
        for sub in subs:
            out.extend(_parse_token(sub, parent=None, declared_class=None, pos=pos))
        return out

    if _is_functional_head(head):
        # 功能类别引导（即使括号内只有一个具体名称也要展开，
        # 如"抗氧化剂（特丁基对苯二酚）"，不能当成备注）
        out = []
        for sub in subs:
            out.extend(_parse_token(sub, parent=parent, declared_class=head, pos=pos))
        return out

    # 无分隔符且较短 -> 备注说明（如"阿斯巴甜（含苯丙氨酸）"）
    if len(subs) == 1 and len(paren) <= 24:
        item["name"] = head
        item["remark"] = paren
        return [item]

    # 复合配料：父项 + 子配料
    out = [{
        "name": head,
        "percent": None,
        "remark": None,
        "parent": parent,
        "declared_class": declared_class,
        "pos": pos,
    }]
    for sub in subs:
        out.extend(_parse_token(sub, parent=head, declared_class=None, pos=pos))
    return out


def parse_label(text):
    """解析整段配料表文本，返回条目列表（保持包装上的顺序）。"""
    text = normalize(text)
    items = []
    for i, tok in enumerate(split_top(text), start=1):
        items.extend(_parse_token(tok, pos=i))
    # 去掉空名
    return [it for it in items if it.get("name")]


def looks_like_additive(name, declared_class=None):
    """未收录条目的启发式分类：像不像食品添加剂。"""
    # 裸功能类别名（如漏网的"增稠剂"）不算添加剂
    if name in FUNCTIONAL_CLASSES or name in CONTAINERS:
        return False
    if declared_class and declared_class in ("营养强化剂", "复配营养强化剂"):
        return False  # 强化剂按配料对待，不算"狠活"
    if declared_class:
        return True
    if E_NUMBER_RE.match(name.strip()):
        return True
    for suf in ADDITIVE_SUFFIXES:
        if name.endswith(suf) and len(name) >= 3:
            return True
    return False
