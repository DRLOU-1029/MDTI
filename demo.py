# -*- coding: utf-8 -*-
"""演示模式：不连 MCP、不碰账号，用脱敏快照跑完整流程。

数据来自 references/demo_fixture.json（scripts/make_fixture.py 生成）。
选品、排序、热量、换餐都在本地完成；价格按快照里的单价求和，
换餐按接口给的 diffPrice 做加减——会在界面上明确标注「演示数据」。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import nutrition
import planner
from recommend import TYPES, is_main, kcal_key, rank

FIXTURE = Path(__file__).resolve().parent / "references" / "demo_fixture.json"
SHARE_WORDS = ("双人", "多人", "分享", "全家", "三人", "四人", "拼盘", "聚")

_DATA: dict | None = None
_TABLE: dict | None = None


def available() -> bool:
    return FIXTURE.exists()


def load() -> dict:
    global _DATA
    if _DATA is None:
        _DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return _DATA


def table() -> dict:
    global _TABLE
    if _TABLE is None:
        _TABLE = load().get("nutrition_table") or {}
    return _TABLE


def stores(city: str = "上海", keyword: str = "麦当劳") -> dict:
    data = load()
    return {
        "city": data.get("city") or city,
        "keyword": data.get("keyword") or keyword,
        "stores": data.get("stores") or [],
        "calls": 0,
        "demo": True,
    }


def _diff(text: str) -> float:
    if not text:
        return 0.0
    sign = -1.0 if "-" in text else 1.0
    found = re.search(r"([\d.]+)", text)
    return sign * float(found.group(1)) if found else 0.0


def _options_payload(items, rows, budget, type_code):
    total = round(sum(row["price"]["total"] for row in rows), 2)
    original = round(sum(row["price"]["original"] for row in rows), 2)
    delta = round(total - budget, 2)
    prepared = [{"name": it["name"], "rounds": it.get("rounds") or []} for it in items]
    return {
        "items": [planner._item_payload(it, row.get("nutrition")) for it, row in zip(items, rows)],
        "price": {"total": total, "original": original, "discount": round(original - total, 2)},
        "delta": delta,
        "delta_pct": round(delta / budget * 100) if budget else 0,
        "tag": planner._tag(delta, budget),
        "reason": planner._reason(type_code, delta),
        "count": planner._item_count([{"rounds": it.get("rounds") or []} for it in items]),
        "coupon": None,
        "nutrition": planner._aggregate_nutrition(prepared, table()),
    }


def plan(type_code: str, budget: float | None = None, keyword: str = "麦当劳",
         limit: int = 4, sort: str = "budget", store_code: str | None = None) -> dict:
    if type_code not in TYPES:
        raise ValueError(f"未知类型：{type_code}")

    data = load()
    tbl = table()
    budget = float(budget or planner.default_budget(type_code))

    social, restraint = type_code[0], type_code[3]
    allow_share = not (social == "独" and restraint == "控")
    allow_pairs = social == "群" or restraint == "纵"
    health = restraint == "控"

    by_code = {it["code"]: it for it in data["items"]}
    entries = [
        {"code": it["code"], "category": it.get("category", ""), "tags": it.get("tags") or []}
        for it in data["items"]
    ]

    priced: list[dict] = []
    seen_names: set[str] = set()
    for entry in rank(entries, type_code):
        item = by_code.get(entry["code"])
        if not item:
            continue
        name = item["name"]
        if name in seen_names or not is_main(name):
            continue
        if not allow_share and any(word in name for word in SHARE_WORDS):
            continue
        seen_names.add(name)
        rounds = item.get("rounds") or []
        priced.append({
            "item": {
                "code": item["code"],
                "name": name,
                "category": item.get("category", ""),
                "tags": item.get("tags") or [],
                "rounds": rounds,
            },
            "price": {
                "total": item["price"]["total"],
                "original": item["price"].get("original") or item["price"]["total"],
                "discount": 0,
            },
            "coupon": None,
            "nutrition": nutrition.for_item(name, rounds, tbl),
        })
        if len(priced) >= 9:
            break

    if not priced:
        raise ValueError("演示数据里没有可用商品")

    if health:
        priced.sort(key=lambda row: (
            0 if is_main(row["item"]["name"]) else 1,
            *kcal_key({"nutrition": row.get("nutrition")}),
        ))
    else:
        priced.sort(key=lambda row: 0 if is_main(row["item"]["name"]) else 1)

    combos = planner._combos(priced, allow_pairs, budget)
    targets = [budget * (1 + ratio) for ratio in planner._ratios(type_code)][:limit]
    chosen, used = planner._select(combos, targets)
    chosen = planner._ensure_variety(chosen, combos, used)

    options = [
        _options_payload([row["item"] for row in combo["rows"]], combo["rows"], budget, type_code)
        for combo in chosen
    ]

    if sort == "price":
        options.sort(key=lambda option: option["price"]["total"])
    elif sort == "kcal":
        options.sort(key=lambda option: (
            option["nutrition"] is None,
            (option["nutrition"] or {}).get("kcal", 10 ** 9),
        ))
    elif sort == "count":
        options.sort(key=lambda option: -option["count"])
    else:
        options.sort(key=lambda option: abs(option["delta"]))

    store = data.get("store") or {}
    if store_code:
        found = next(
            (s for s in data.get("stores") or [] if str(s.get("storeCode")) == str(store_code)),
            None,
        )
        if found:
            store = found
    return {
        "type": {
            "code": type_code,
            "name": TYPES[type_code]["name"],
            "tagline": TYPES[type_code]["tagline"],
            "ascii": TYPES[type_code]["ascii"],
        },
        "store": {
            "storeCode": store.get("storeCode"),
            "storeName": store.get("storeName"),
            "address": store.get("address"),
            "distance": store.get("distance"),
        },
        "city": data.get("city") or "上海",
        "keyword": data.get("keyword") or "麦当劳",
        "budget": budget,
        "default_budget": planner.default_budget(type_code),
        "range": planner.BUDGET_RANGE,
        "health_focus": health,
        "sort": sort,
        "options": options[:limit],
        "calls": 0,
        "demo": True,
    }


def _selection_rounds(rounds: list, selections: dict) -> tuple[list[dict], float]:
    """按用户选择重排每层，并累加差价。"""
    out: list[dict] = []
    extra = 0.0
    for rnd in rounds or []:
        choices = rnd.get("choices") or []
        default = next((c for c in choices if c.get("isDefault")), choices[0] if choices else None)
        wanted = str(selections.get(str(rnd.get("id")))) if selections else ""
        chosen = next((c for c in choices if str(c.get("code")) == wanted), None) if wanted else None
        if chosen is None:
            chosen = default
        if chosen is None:
            continue
        extra += _diff(chosen.get("diffPrice"))
        out.append({
            "id": rnd.get("id"),
            "name": rnd.get("name"),
            "choices": [
                dict(choice,
                     quantity=1 if choice is chosen else 0,
                     isDefault=1 if choice is chosen else 0)
                for choice in choices
            ],
        })
    return out, extra


def reprice(type_code: str, items: list[dict], budget: float) -> dict:
    if type_code not in TYPES:
        raise ValueError(f"未知类型：{type_code}")
    data = load()
    tbl = table()
    by_code = {it["code"]: it for it in data["items"]}
    budget = float(budget or planner.default_budget(type_code))

    total = 0.0
    prepared: list[dict] = []
    out_items: list[dict] = []

    for item in items:
        code = item.get("productCode") or item.get("code")
        src = by_code.get(code)
        if not src:
            raise ValueError("演示数据里没有这个商品")
        rounds, extra = _selection_rounds(src.get("rounds") or [], item.get("selections") or {})
        price = round(src["price"]["total"] + extra, 2)
        total += price
        info = nutrition.for_item(src["name"], rounds, tbl)
        prepared.append({"name": src["name"], "rounds": rounds})
        out_items.append(planner._item_payload(
            {"code": src["code"], "name": src["name"], "rounds": rounds}, info
        ))

    total = round(total, 2)
    delta = round(total - budget, 2)
    return {
        "price": {"total": total, "original": total, "discount": 0.0},
        "delta": delta,
        "delta_pct": round(delta / budget * 100) if budget else 0,
        "tag": planner._tag(delta, budget),
        "reason": planner._reason(type_code, delta),
        "count": planner._item_count([{"rounds": p["rounds"]} for p in prepared]),
        "nutrition": planner._aggregate_nutrition(prepared, tbl),
        "items": out_items,
        "calls": 0,
        "demo": True,
    }


def _sum_items(items: list[dict]) -> tuple[float, list[dict]]:
    data = load()
    by_code = {it["code"]: it for it in data["items"]}
    total = 0.0
    lines = []
    for item in items:
        code = item.get("productCode") or item.get("code")
        src = by_code.get(code)
        if not src:
            raise ValueError("演示数据里没有这个商品，请回购物车删除后重试")
        _, extra = _selection_rounds(src.get("rounds") or [], item.get("selections") or {})
        quantity = int(item.get("quantity") or 1)
        subtotal = round((src["price"]["total"] + extra) * quantity, 2)
        total += subtotal
        lines.append({"name": src["name"], "quantity": quantity, "subtotal": subtotal})
    return round(total, 2), lines


def checkout(items: list[dict]) -> dict:
    total, lines = _sum_items(items)
    return {
        "total": total,
        "original": total,
        "discount": 0.0,
        "takeWayList": [
            {"code": "eat-in", "title": "堂食"},
            {"code": "take-in-store", "title": "外带"},
        ],
        "items": lines,
        "calls": 0,
        "demo": True,
    }


def order(items: list[dict], take_way: str | None = None) -> dict:
    total, lines = _sum_items(items)
    return {
        "orderId": "DEMO-ORDER",
        "payUrl": None,
        "status": "演示订单（未真实下单）",
        "storeName": (load().get("store") or {}).get("storeName"),
        "total": f"{total:.2f}",
        "original": f"{total:.2f}",
        "discount": "0",
        "expirePayTime": "",
        "takeWay": "外带" if take_way == "take-in-store" else "堂食",
        "products": [{"name": line["name"], "quantity": line["quantity"]} for line in lines],
        "calls": 0,
        "demo": True,
    }
