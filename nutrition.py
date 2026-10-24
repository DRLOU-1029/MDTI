# -*- coding: utf-8 -*-
"""营养数据：解析 list-nutrition-foods 的文本表，并按套餐组成累加。

麦当劳这个工具返回的不是 JSON，而是一段伪表格文本：

    [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
      猪柳麦满分,null,1288,308,16,16,24,781,213
      ...

两个必须诚实处理的地方：

1. 这张表只收录经典单品，新品（龙焰、枫糖等）不在里面。
2. 新品按「同品类老产品」估算热量——但必须标注是估算，并写明拿了谁做替代。
"""

from __future__ import annotations

import re
import time

_TABLE: dict[str, dict] | None = None
_LOADED_AT = 0.0
_TTL = 600.0          # 10 分钟内复用同一份
JUST_LOADED = False   # 供调用方统计 MCP 调用次数

FIELDS = ("kcal", "protein", "fat", "carb", "sodium")

# 酱料、餐具这类配件不参与热量累加，也不该被算成「缺项」
SKIP_WORDS = ("酱", "餐具", "纸巾", "吸管")

# 关键词 -> 用来估算的老产品。顺序有意义：越具体的规则放越前面。
PROXIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("无糖", "零度"), "无糖可口可乐中杯"),
    (("麦旋风",), "奥利奥麦旋风"),
    (("新地", "圣代"), "草莓新地"),
    (("甜筒", "圆筒", "冰淇淋"), "圆筒冰淇淋"),
    (("薯饼",), "脆薯饼"),
    (("薯条", "薯角"), "中薯条"),
    (("麦乐鸡", "鸡块"), "麦乐鸡5块"),
    (("鸡翅",), "麦辣鸡翅-2块"),
    (("鸡排",), "那么大鸡排"),
    (("派",), "香芋派"),
    (("玉米",), "大杯玉米杯"),
    (("油条",), "脆香油条"),
    (("麦满分",), "猪柳麦满分"),
    (("咖啡", "拿铁"), "大杯鲜萃咖啡"),
    (("红茶", "柠檬茶"), "锡兰红茶"),
    (("果汁", "橙汁", "苹果汁"), "100%苹果汁"),
    (("可乐", "雪碧", "汽水"), "可乐中杯"),
    (("卷",), "麦香鸡"),
    (("堡", "汉堡"), "板烧鸡腿堡"),
    (("鸡",), "麦香鸡"),
)


def parse_table(text: str) -> dict[str, dict]:
    table: dict[str, dict] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("["):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 9:
            continue
        try:
            table[parts[0]] = {
                "kcal": float(parts[3]),
                "protein": float(parts[4]),
                "fat": float(parts[5]),
                "carb": float(parts[6]),
                "sodium": float(parts[7]),
            }
        except (ValueError, IndexError):
            continue
    return table


def _from_list(rows: list) -> dict[str, dict]:
    table: dict[str, dict] = {}
    for row in rows:
        name = row.get("productName") or row.get("name")
        if not name:
            continue
        try:
            table[name] = {
                "kcal": float(row.get("energyKcal") or 0),
                "protein": float(row.get("protein") or 0),
                "fat": float(row.get("fat") or 0),
                "carb": float(row.get("carbohydrate") or 0),
                "sodium": float(row.get("sodium") or 0),
            }
        except (TypeError, ValueError):
            continue
    return table


def table(client) -> dict[str, dict]:
    """取营养表（带缓存）。"""
    global _TABLE, _LOADED_AT, JUST_LOADED
    JUST_LOADED = False

    if _TABLE is not None and time.time() - _LOADED_AT <= _TTL:
        return _TABLE

    data = client.tool_data("list-nutrition-foods", {})
    raw = data.get("data")
    if isinstance(raw, str):
        _TABLE = parse_table(raw)
    elif isinstance(raw, list):
        _TABLE = _from_list(raw)
    else:
        _TABLE = {}
    _LOADED_AT = time.time()
    JUST_LOADED = True
    return _TABLE


def _norm(text: str) -> str:
    """去掉数量、单位和括号注释，让 `5块麦乐鸡` 能对上 `麦乐鸡5块`。"""
    text = re.sub(r"[（(].*?[）)]", "", text)
    text = re.sub(r"\d+", "", text)
    for word in ("块", "个", "杯", "份", "只", "片", "装"):
        text = text.replace(word, "")
    return text.strip()


def lookup(name: str, tbl: dict[str, dict]) -> dict | None:
    """精确 → 归一化 → 包含，三级匹配。"""
    if not name:
        return None
    if name in tbl:
        return tbl[name]

    normalized = _norm(name)
    if normalized:
        for key, value in tbl.items():
            if _norm(key) == normalized:
                return value

    best_key = ""
    for key in tbl:
        if key and key in name and len(key) > len(best_key):
            best_key = key
    return tbl[best_key] if best_key else None


def proxy_of(name: str, tbl: dict[str, dict]) -> tuple[dict, str] | None:
    """找不到这个新品，就按品类拿一个老产品来估。返回 (营养, 替代品名)。"""
    for keywords, proxy_name in PROXIES:
        if not any(word in name for word in keywords):
            continue
        item = tbl.get(proxy_name) or lookup(proxy_name, tbl)
        if item:
            return item, proxy_name
    return None


def _default_choice(round_item: dict) -> dict | None:
    choices = round_item.get("choices") or []
    for choice in choices:
        if choice.get("isDefault") == 1:
            return choice
    return choices[0] if choices else None


def for_item(name: str, rounds: list | None, tbl: dict[str, dict]) -> dict | None:
    """套餐按每层默认选项累加；单品（没有 rounds）按名字匹配。

    返回字典带 complete / covered / rounds / substituted / estimated，
    其中 substituted 是「被拿来代替的新品所对应的老产品名」。
    """
    if rounds:
        total = {field: 0.0 for field in FIELDS}
        covered = 0
        counted = 0
        substituted: list[str] = []
        parts: list[dict] = []

        for index, round_item in enumerate(rounds):
            choice = _default_choice(round_item)
            if not choice:
                continue
            choice_name = choice.get("name") or ""
            if any(word in choice_name for word in SKIP_WORDS):
                continue
            counted += 1

            proxy_name = None
            item = lookup(choice_name, tbl)
            if item is None:
                proxy = proxy_of(choice_name, tbl)
                if proxy is None:
                    continue
                item, proxy_name = proxy
                substituted.append(proxy_name)
            covered += 1
            for field in FIELDS:
                total[field] += item[field]
            parts.append({
                "round": index,
                "name": choice_name,
                "kcal": round(item["kcal"]),
                "proxy": proxy_name,
            })

        if not covered:
            return None
        total.update({
            "covered": covered,
            "rounds": max(counted, 1),
            "complete": covered == max(counted, 1),
            "substituted": substituted,
            "estimated": bool(substituted),
            "parts": parts,
        })
        return total

    item = lookup(name, tbl)
    if item is not None:
        merged = dict(item)
        merged.update({
            "covered": 1, "rounds": 1, "complete": True,
            "substituted": [], "estimated": False,
            "parts": [{"name": name, "kcal": round(item["kcal"]), "proxy": None}],
        })
        return merged

    proxy = proxy_of(name, tbl)
    if proxy is None:
        return None
    item, proxy_name = proxy
    merged = dict(item)
    merged.update({
        "covered": 1, "rounds": 1, "complete": True,
        "substituted": [proxy_name], "estimated": True,
        "parts": [{"name": name, "kcal": round(item["kcal"]), "proxy": proxy_name}],
    })
    return merged


def format_line(nutrition: dict | None, health_focus: bool = False) -> str:
    """人读的一行。缺项时给下限，用替代时标明是估算。"""
    if not nutrition:
        return ""
    kcal = round(nutrition.get("kcal") or 0)

    if not nutrition.get("complete", True):
        return f"热量 ≥ {kcal} kcal（{max(nutrition.get('missing', 1), 1)} 项未收录）"

    substituted = nutrition.get("substituted") or []

    if health_focus:
        protein = round(nutrition.get("protein") or 0, 1)
        sodium = round(nutrition.get("sodium") or 0)
        suffix = "（估算）" if substituted else ""
        return f"约 {kcal} kcal · 蛋白质 {protein} g · 钠 {sodium} mg{suffix}"

    if substituted:
        return f"约 {kcal} kcal（估算）"
    return f"约 {kcal} kcal"
