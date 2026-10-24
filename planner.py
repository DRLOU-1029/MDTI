# -*- coding: utf-8 -*-
"""按「性格 + 预算」生成最多 4 个套餐组合方案。

设计要点：
  - 每个类型有一个默认预算（团型按一桌算）
  - 性格决定方案的「落点」：控型扎堆在预算下方，纵型贴着或略微超
  - 算型把落点收紧到预算附近（精确），感型把落点铺开（凭感觉）
"""

from __future__ import annotations

import time

import nutrition
from mcp_client import config
from mcp_client.client import McpClient, McpError
from recommend import (
    TYPES,
    describe,
    find_store,
    is_main,
    load_coupons,
    load_menu,
    price_items,
    query_stores,
    rank,
    slim_rounds,
    split_location,
)

# 每个类型的默认预算（元）。团型的预算是一桌的总额。
DEFAULT_BUDGETS = {
    "独稳算控": 26,
    "独稳算纵": 24,
    "独稳感控": 24,
    "独稳感纵": 58,
    "独新算控": 30,
    "独新算纵": 46,
    "独新感控": 20,
    "独新感纵": 66,
    "群稳算控": 120,
    "群稳算纵": 160,
    "群稳感控": 60,
    "群稳感纵": 180,
    "群新算控": 110,
    "群新算纵": 100,
    "群新感控": 36,
    "群新感纵": 150,
}

BUDGET_RANGE = {"min": 10, "max": 200, "step": 5}

# 相对预算的落点。控型整体在下方，纵型贴着或往上。
UNDER = (-0.38, -0.24, -0.10, 0.02)
OVER = (-0.04, 0.08, 0.20, 0.34)

CACHE_TTL = 600.0
_POOL: dict[tuple, dict] = {}
_DETAIL_CACHE: dict[tuple, dict] = {}
STORE_CACHE: dict[str, dict] = {}


def list_stores(city: str, keyword: str = "麦当劳") -> dict:
    """查附近门店，按距离升序返回。结果进缓存，供后续按 storeCode 出方案。"""
    client = McpClient(config.token() or "", config.url())
    client.initialize()
    stores, city_name, kw = query_stores(client, city, keyword)

    for store in stores:
        code = store.get("storeCode")
        if code:
            STORE_CACHE[code] = store

    return {
        "city": city_name,
        "keyword": kw,
        "stores": [
            {
                "storeCode": store.get("storeCode"),
                "storeName": store.get("storeName"),
                "address": store.get("address"),
                "distance": store.get("distance"),
                "businessStatus": store.get("businessStatus"),
                "businessStartTime": store.get("businessStartTime"),
                "businessEndTime": store.get("businessEndTime"),
            }
            for store in stores
        ],
        "calls": 2,
    }

# 周边、礼品不是饭，不能进推荐池
NOT_FOOD = ("丝巾", "玩具", "卡片", "徽章", "冰箱贴", "杯垫", "周边", "礼盒",
            "盲盒", "手提袋", "海报", "贴纸", "兑换券", "券码")
# 多人份关键词
SHARE_WORDS = ("双人", "多人", "分享", "全家", "三人", "四人", "拼盘", "聚")


def default_budget(type_code: str) -> int:
    return DEFAULT_BUDGETS.get(type_code, 30)


def _ratios(type_code: str) -> list[float]:
    restraint, basis = type_code[3], type_code[2]
    base = UNDER if restraint == "控" else OVER
    # 算型收紧（精确贴预算），感型铺开（凭感觉）
    k = 0.6 if basis == "算" else 1.25
    return [r * k for r in base]


def _tag(delta: float, budget: float) -> str:
    ratio = delta / budget if budget else 0
    if ratio <= -0.35:
        return "省一大截"
    if ratio <= -0.10:
        return "留有余地"
    if ratio <= 0.04:
        return "刚好卡线"
    if ratio <= 0.18:
        return "略超一点"
    return "超出不少"


def _reason(type_code: str, delta: float) -> str:
    restraint = type_code[3]
    if restraint == "控":
        if delta > 0:
            return f"超了 ¥{delta:.1f}，下次收着点"
        if delta < 0:
            return f"还留着 ¥{-delta:.1f} 的余地"
        return "刚好用满，一分不多"
    if delta > 0:
        return f"超 ¥{delta:.1f}，但今天值得"
    if delta < 0:
        return f"还剩 ¥{-delta:.1f}，可以再加"
    return "刚好花完预算"


def _build_pool(client: McpClient, store_code: str, type_code: str, calls: list[int]) -> list[dict]:
    social, restraint = type_code[0], type_code[3]
    # 一个人又想收着的，不会去点双人餐；一个人但放纵的，双人餐正是他
    allow_share = not (social == "独" and restraint == "控")
    # 需要凑量的：群型，或任何「纵」型
    allow_pairs = social == "群" or restraint == "纵"

    entries = load_menu(client, store_code)
    calls[0] += 1

    coupons: list[dict] = []
    if type_code[2] == "算":
        coupons = load_coupons(client, store_code)
        calls[0] += 1

    coupon_by_code: dict[str, dict] = {}
    for coupon in coupons:
        for product in coupon.get("products") or []:
            code = product.get("productCode")
            if code and code not in coupon_by_code:
                coupon_by_code[code] = coupon

    # 先按类目过滤，能省掉一批没必要的详情调用
    filtered: list[dict] = []
    for entry in entries:
        category = entry.get("category") or ""
        if any(word in category for word in NOT_FOOD):
            continue
        if not allow_share and any(word in category for word in SHARE_WORDS):
            continue
        filtered.append(entry)

    priced: list[dict] = []
    names: set[str] = set()
    for entry in rank(filtered, type_code):
        if len(priced) >= 8:
            break
        if len(names) > 24:  # 详情调用的兜底上限
            break
        try:
            item = describe(client, store_code, entry)
            calls[0] += 1
        except McpError:
            continue

        name = item["name"]
        if name in names:
            continue
        names.add(name)

        if any(word in name for word in NOT_FOOD):
            continue
        if not is_main(name):
            continue
        if not allow_share and any(word in name for word in SHARE_WORDS):
            continue

        coupon = coupon_by_code.get(item["code"])
        trial = None
        if coupon:
            trial = dict(coupon)
            trial["_matched"] = {item["code"]}
        try:
            price = price_items(client, store_code, [item], trial)
            calls[0] += 1
        except McpError:
            continue
        if not price["total"] or price["total"] <= 0:
            continue
        priced.append({"item": item, "price": price, "coupon": coupon})

    priced.sort(key=lambda row: row["price"]["total"])
    return priced, allow_pairs


def _combos(priced: list[dict], allow_pairs: bool, budget: float) -> list[dict]:
    combos = [{"rows": [row], "total": row["price"]["total"]} for row in priced]

    if allow_pairs:
        count = len(priced)
        for i in range(count):
            for j in range(i + 1, count):
                total = priced[i]["price"]["total"] + priced[j]["price"]["total"]
                if total <= budget * 1.8:
                    combos.append({"rows": [priced[i], priced[j]], "total": total})

    return combos


def _select(combos: list[dict], targets: list[float]) -> tuple[list[dict], set[int]]:
    """每个目标价位挑一个最接近的，且四个方案不重复。"""
    chosen: list[dict] = []
    used: set[int] = set()
    for target in targets:
        best_index = None
        best_gap = None
        for index, combo in enumerate(combos):
            if index in used:
                continue
            gap = abs(combo["total"] - target)
            if best_gap is None or gap < best_gap:
                best_index, best_gap = index, gap
        if best_index is None:
            break
        used.add(best_index)
        chosen.append(combos[best_index])
    return chosen, used


def _count_of(combo: dict) -> int:
    return _item_count([row["item"] for row in combo["rows"]])


def _ensure_variety(chosen: list[dict], combos: list[dict], used: set[int]) -> list[dict]:
    """四个方案样数全一样时，换掉一个，否则「按样数排序」看起来像坏了。"""
    if len(chosen) < 2:
        return chosen
    if len({_count_of(combo) for combo in chosen}) > 1:
        return chosen

    same = _count_of(chosen[0])
    for index, combo in enumerate(combos):
        if index in used or _count_of(combo) == same:
            continue
        used.add(index)
        return chosen[:-1] + [combo]
    return chosen


def _item_count(items: list[dict]) -> int:
    """样数：套餐按层拆开算。三件套 = 3 样，单品 = 1 样。"""
    total = 0
    for item in items:
        rounds = len(item.get("rounds") or [])
        total += max(rounds, 1)
    return total


def _nutrition_of(client: McpClient, items: list[dict], health: bool, calls: list[int]):
    """返回 (每一项的明细列表, 合并后的总量)。明细要贴回套餐构成的每一层。"""
    tbl = nutrition.table(client)
    if nutrition.JUST_LOADED:
        calls[0] += 1

    per_item: list[dict | None] = []
    totals = {field: 0.0 for field in nutrition.FIELDS}
    substituted: list[str] = []
    substitutions: list[dict] = []
    complete = True
    missing = 0
    got = False

    for item in items:
        info = nutrition.for_item(item["name"], item.get("rounds"), tbl)
        per_item.append(info)
        if not info:
            complete = False
            missing += 1
            continue
        got = True
        for field in nutrition.FIELDS:
            totals[field] += info[field]
        substituted.extend(info.get("substituted") or [])
        for part in info.get("parts") or []:
            if part.get("proxy"):
                substitutions.append({"from": part["name"], "to": part["proxy"]})
        if not info.get("complete", True):
            complete = False
            missing += max(info.get("rounds", 1) - info.get("covered", 0), 1)

    if not got:
        return per_item, None

    totals.update({
        "complete": complete,
        "missing": missing,
        "substituted": list(dict.fromkeys(substituted)),
    })
    return per_item, {
        "kcal": round(totals["kcal"]),
        "protein": round(totals["protein"], 1),
        "sodium": round(totals["sodium"]),
        "complete": complete,
        "missing": missing,
        "substituted": list(dict.fromkeys(substituted)),
        "substitutions": substitutions[:6],
        "estimated": bool(substituted),
        "line": nutrition.format_line(totals, health),
    }


def _item_payload(item: dict, info: dict | None) -> dict:
    """把一层层的热量和替代来源贴到套餐构成上，外面只留总量。"""
    rounds = item.get("rounds") or []
    parts = (info or {}).get("parts")
    payload = {
        "code": item["code"],
        "name": item["name"],
        "quantity": 1,
        "rounds": slim_rounds(rounds, parts),
    }
    if not rounds:
        first = (parts or [{}])[0]
        payload["kcal"] = first.get("kcal")
        payload["proxy"] = first.get("proxy")
    return payload


def _detail_payload(client: McpClient, store_code: str, code: str) -> dict:
    key = (store_code, code)
    cached = _DETAIL_CACHE.get(key)
    if cached and time.time() - cached["ts"] < CACHE_TTL:
        return cached["data"]
    data = client.tool_data("query-meal-detail", {
        "beType": 1, "orderType": 1, "storeCode": store_code, "code": code,
    })
    body = data.get("data") or {}
    _DETAIL_CACHE[key] = {"data": body, "ts": time.time()}
    return body


def _apply_selection(rounds: list, selections: dict) -> tuple[list[dict], list[dict]]:
    """按用户选的 code 生成 roundList，并合成一份「已选中」的 rounds 供算热量。"""
    round_list: list[dict] = []
    synthetic: list[dict] = []

    for rnd in rounds or []:
        rid = str(rnd.get("id"))
        choices = rnd.get("choices") or []
        chosen = None
        wanted = str(selections.get(rid)) if selections else ""
        if wanted:
            chosen = next((c for c in choices if str(c.get("code")) == wanted), None)
        if chosen is None:
            chosen = next((c for c in choices if c.get("isDefault") == 1), None)
        if chosen is None:
            chosen = choices[0] if choices else None
        if chosen is None:
            continue

        round_list.append({
            "round": rid,
            "comboItemList": [{"code": chosen.get("code"), "quantity": 1}],
        })
        synthetic.append({
            "id": rnd.get("id"),
            "name": rnd.get("name"),
            "choices": [
                dict(choice,
                     quantity=1 if choice is chosen else 0,
                     isDefault=1 if choice is chosen else 0)
                for choice in choices
            ],
        })

    return round_list, synthetic


def reprice(type_code: str, store_code: str, items: list[dict], budget: float) -> dict:
    """按新的套餐选择重新算价与热量。items: [{productCode, selections:{roundId: code}}]"""
    if type_code not in TYPES:
        raise ValueError(f"未知类型：{type_code}")

    budget = float(budget or default_budget(type_code))
    client = McpClient(config.token() or "", config.url())
    client.initialize()
    calls = [1]

    payload_items: list[dict] = []
    prepared: list[dict] = []

    for item in items:
        code = item.get("productCode") or item.get("code")
        if not code:
            continue
        body = _detail_payload(client, store_code, code)
        calls[0] += 1
        rounds = body.get("rounds") or []
        round_list, synthetic = _apply_selection(rounds, item.get("selections") or {})

        payload = {"productCode": code, "quantity": 1}
        if round_list:
            payload["roundList"] = round_list
        payload_items.append(payload)

        prepared.append({
            "code": code,
            "name": body.get("name") or code,
            "rounds": synthetic,
            "has_rounds": bool(rounds),
        })

    if not payload_items:
        raise ValueError("没有可重算的商品")

    data = client.tool_data("calculate-price", {
        "beType": 1, "orderType": 1, "storeCode": store_code, "items": payload_items,
    })
    calls[0] += 1
    price_body = data.get("data") or {}
    total = round((price_body.get("price") or 0) / 100, 2)
    original = round((price_body.get("originalPrice") or 0) / 100, 2)
    discount = round((price_body.get("discount") or 0) / 100, 2)

    tbl = nutrition.table(client)
    if nutrition.JUST_LOADED:
        calls[0] += 1

    out_items = []
    for info in prepared:
        n = nutrition.for_item(info["name"], info["rounds"], tbl)
        out_items.append({
            "code": info["code"],
            "name": info["name"],
            "quantity": 1,
            "rounds": slim_rounds(info["rounds"], (n or {}).get("parts")) if info["has_rounds"] else [],
        })
        if not info["has_rounds"] and n:
            out_items[-1]["kcal"] = n.get("kcal")
            out_items[-1]["proxy"] = ((n.get("parts") or [{}])[0]).get("proxy")

    per_item = None
    aggregate = _aggregate_nutrition(prepared, tbl)

    delta = round(total - budget, 2)
    return {
        "price": {"total": total, "original": original, "discount": discount},
        "delta": delta,
        "delta_pct": round(delta / budget * 100) if budget else 0,
        "tag": _tag(delta, budget),
        "reason": _reason(type_code, delta),
        "count": _item_count([{"rounds": info["rounds"]} for info in prepared]),
        "nutrition": aggregate,
        "items": out_items,
        "calls": calls[0],
    }


def _aggregate_nutrition(prepared, tbl):
    """把多项商品的营养合成一份，返回 (逐项, 合计)。"""
    totals = {field: 0.0 for field in nutrition.FIELDS}
    substituted: list[str] = []
    substitutions: list[dict] = []
    complete = True
    missing = 0
    got = False

    for info in prepared:
        n = nutrition.for_item(info["name"], info["rounds"], tbl)
        if not n:
            complete = False
            missing += 1
            continue
        got = True
        for field in nutrition.FIELDS:
            totals[field] += n[field]
        substituted.extend(n.get("substituted") or [])
        for part in n.get("parts") or []:
            if part.get("proxy"):
                substitutions.append({"from": part["name"], "to": part["proxy"]})
        if not n.get("complete", True):
            complete = False
            missing += max(n.get("rounds", 1) - n.get("covered", 0), 1)

    if not got:
        return None

    totals.update({
        "complete": complete,
        "missing": missing,
        "substituted": list(dict.fromkeys(substituted)),
    })
    return {
        "kcal": round(totals["kcal"]),
        "protein": round(totals["protein"], 1),
        "sodium": round(totals["sodium"]),
        "complete": complete,
        "missing": missing,
        "substituted": list(dict.fromkeys(substituted)),
        "substitutions": substitutions[:6],
        "estimated": bool(substituted),
        "line": nutrition.format_line(totals, True),
    }


def _order_items(client: McpClient, store_code: str, items: list[dict]) -> list[dict]:
    """把购物车条目转成 calculate-price / create-order 需要的 items（含 roundList）。"""
    payload: list[dict] = []
    for item in items:
        code = item.get("productCode") or item.get("code")
        if not code:
            continue
        body = _detail_payload(client, store_code, code)
        rounds = body.get("rounds") or []
        round_list, _ = _apply_selection(rounds, item.get("selections") or {})
        row = {"productCode": code, "quantity": int(item.get("quantity") or 1)}
        if round_list:
            row["roundList"] = round_list
        if item.get("couponId"):
            row["couponId"] = item["couponId"]
        if item.get("couponCode"):
            row["couponCode"] = item["couponCode"]
        payload.append(row)
    return payload


def checkout(store_code: str, items: list[dict]) -> dict:
    """结算：只算价，不创建订单。"""
    client = McpClient(config.token() or "", config.url())
    client.initialize()
    calls = [1]

    payload = _order_items(client, store_code, items)
    if not payload:
        raise ValueError("购物车是空的")
    calls[0] += len(items)

    data = client.tool_data("calculate-price", {
        "beType": 1, "orderType": 1, "storeCode": store_code, "items": payload,
    })
    calls[0] += 1
    body = data.get("data") or {}
    takes = body.get("takeWayList") or []

    return {
        "total": round((body.get("price") or 0) / 100, 2),
        "original": round((body.get("originalPrice") or 0) / 100, 2),
        "discount": round((body.get("discount") or 0) / 100, 2),
        "takeWayList": [{"code": t.get("code"), "title": t.get("title")} for t in takes],
        "items": [
            {"name": line.get("productName"), "quantity": line.get("quantity"),
             "subtotal": round((line.get("subtotal") or 0) / 100, 2)}
            for line in body.get("productList") or []
        ],
        "calls": calls[0],
    }


def place_order(store_code: str, items: list[dict], take_way: str | None = None) -> dict:
    """创建订单（不支付）。返回订单号与支付链接。"""
    client = McpClient(config.token() or "", config.url())
    client.initialize()
    calls = [1]

    payload = _order_items(client, store_code, items)
    if not payload:
        raise ValueError("购物车是空的")
    calls[0] += len(items)

    price = client.tool_data("calculate-price", {
        "beType": 1, "orderType": 1, "storeCode": store_code, "items": payload,
    })
    calls[0] += 1
    takes = (price.get("data") or {}).get("takeWayList") or []
    chosen = None
    if take_way:
        chosen = next((t for t in takes if t.get("code") == take_way), None)
    if chosen is None:
        chosen = next((t for t in takes if t.get("code") == "eat-in"), None)
    if chosen is None and takes:
        chosen = takes[0]
    if chosen is None:
        raise McpError("这个门店没有可用的取餐方式")

    order = client.tool_data("create-order", {
        "beType": 1,
        "orderType": 1,
        "storeCode": store_code,
        "takeWayCode": chosen["code"],
        "items": payload,
    })
    calls[0] += 1

    data = order.get("data") or {}
    detail = data.get("orderDetail") or {}
    return {
        "orderId": data.get("orderId"),
        "payUrl": data.get("payH5Url"),
        "status": detail.get("orderStatus"),
        "storeName": detail.get("storeName"),
        "total": detail.get("realTotalAmount"),
        "original": detail.get("totalAmount"),
        "discount": detail.get("totalDiscountAmount"),
        "expirePayTime": detail.get("expirePayTime"),
        "takeWay": detail.get("takeWay"),
        "products": [
            {"name": p.get("productName"), "quantity": p.get("quantity")}
            for p in detail.get("orderProductList") or []
        ],
        "calls": calls[0],
    }


def cancel_order(order_id: str, reason_code: str = "-1") -> dict:
    """取消订单。reasonCode: 1 改主意 / 2 重复下单 / 3 点错 / 4 地址错 / 5 时间错 / -1 其它。"""
    client = McpClient(config.token() or "", config.url())
    client.initialize()
    data = client.tool_data("cancel-order", {
        "orderId": order_id,
        "cancelReasonCode": str(reason_code or "-1"),
    })
    body = data.get("data") or {}
    return {
        "ok": bool(body.get("cancelResult")),
        "orderId": body.get("orderId") or order_id,
        "message": "订单已取消",
    }


def check_items(store_code: str, items: list[dict]) -> list[dict]:
    """逐件复核是否还能下单，返回不可用的条目。"""
    client = McpClient(config.token() or "", config.url())
    client.initialize()

    bad: list[dict] = []
    for item in items:
        code = item.get("productCode") or item.get("code")
        if not code:
            continue
        name = code
        try:
            body = _detail_payload(client, store_code, code)
            name = body.get("name") or code
            payload = _order_items(client, store_code, [item])
            client.tool_data("calculate-price", {
                "beType": 1, "orderType": 1, "storeCode": store_code, "items": payload,
            })
        except McpError as exc:
            bad.append({"productCode": code, "name": name, "reason": str(exc)[:80]})
    return bad


def plan(type_code: str, city: str, budget: float | None = None,
         keyword: str = "麦当劳", limit: int = 4, sort: str = "budget",
         store_code: str | None = None) -> dict:
    if type_code not in TYPES:
        raise ValueError(f"未知类型：{type_code}")

    budget = float(budget) if budget else float(default_budget(type_code))
    budget = max(BUDGET_RANGE["min"], min(BUDGET_RANGE["max"], budget))

    client = McpClient(config.token() or "", config.url())
    client.initialize()
    calls = [1]

    if store_code and store_code in STORE_CACHE:
        store = STORE_CACHE[store_code]
    else:
        store = find_store(client, city, keyword)
        calls[0] += 1
        if store.get("storeCode"):
            STORE_CACHE[store["storeCode"]] = store

    store_code = store["storeCode"]
    city_name, extra = split_location(city)
    if extra and keyword in ("", "麦当劳"):
        keyword = extra

    cache_key = (store_code, type_code)
    cached = _POOL.get(cache_key)
    if cached and time.time() - cached["ts"] < CACHE_TTL:
        priced, allow_pairs = cached["priced"], cached["allow_pairs"]
    else:
        priced, allow_pairs = _build_pool(client, store_code, type_code, calls)
        _POOL[cache_key] = {
            "priced": priced,
            "allow_pairs": allow_pairs,
            "ts": time.time(),
        }

    if not priced:
        raise McpError("这家店没算出任何可售餐品")

    combos = _combos(priced, allow_pairs, budget)
    targets = [budget * (1 + ratio) for ratio in _ratios(type_code)][:limit]
    chosen, used = _select(combos, targets)
    chosen = _ensure_variety(chosen, combos, used)

    health = type_code[3] == "控"
    options: list[dict] = []

    for combo in chosen:
        rows = combo["rows"]
        items = [row["item"] for row in rows]

        coupon = None
        if len(rows) == 1 and rows[0].get("coupon"):
            coupon = dict(rows[0]["coupon"])
            coupon["_matched"] = {items[0]["code"]}

        try:
            final = price_items(client, store_code, items, coupon)
            calls[0] += 1
        except McpError:
            continue

        total = final["total"]
        delta = round(total - budget, 2)

        per_item, aggregate = _nutrition_of(client, items, True, calls)

        options.append({
            "items": [_item_payload(item, info) for item, info in zip(items, per_item)],
            "price": {
                "total": total,
                "original": final["original"],
                "discount": final["discount"],
            },
            "delta": delta,
            "delta_pct": round(delta / budget * 100) if budget else 0,
            "tag": _tag(delta, budget),
            "reason": _reason(type_code, delta),
            "count": _item_count(items),
            "coupon": (
                {"title": coupon.get("title"), "couponCode": coupon.get("couponCode")}
                if coupon else None
            ),
            "nutrition": aggregate,
        })

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

    return {
        "type": {
            "code": type_code,
            "name": TYPES[type_code]["name"],
            "tagline": TYPES[type_code]["tagline"],
            "ascii": TYPES[type_code]["ascii"],
        },
        "store": {
            "storeCode": store_code,
            "storeName": store.get("storeName"),
            "address": store.get("address"),
            "distance": store.get("distance"),
        },
        "city": city_name,
        "keyword": keyword,
        "budget": budget,
        "default_budget": default_budget(type_code),
        "range": BUDGET_RANGE,
        "health_focus": health,
        "sort": sort,
        "options": options[:limit],
        "calls": calls[0],
    }


def main() -> int:
    import argparse
    import json
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="按性格 + 预算出方案")
    parser.add_argument("--type", required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument("--budget", type=float)
    parser.add_argument("--keyword", default="麦当劳")
    parser.add_argument("--sort", default="budget",
                        choices=["budget", "price", "kcal", "count"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = plan(args.type, args.city, args.budget, args.keyword, sort=args.sort)
    except McpError as exc:
        print(f"[MCP 错误] {exc}", file=sys.stderr)
        return 4
    except ValueError as exc:
        print(f"[参数错误] {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"{result['type']['name']} · 预算 ¥{result['budget']:.0f}（默认 ¥{result['default_budget']}）")
    for index, option in enumerate(result["options"], 1):
        names = "、".join(item["name"] for item in option["items"])
        sign = "+" if option["delta"] > 0 else ""
        kcal = (option.get("nutrition") or {}).get("kcal")
        kcal_text = f"{kcal} kcal" if kcal else "-"
        print(f"  {index}. ¥{option['price']['total']:<7} ({sign}{option['delta']}) "
              f"{option['count']}样 {kcal_text:<9} {option['tag']:<5} {names}")
    print(f"调用 {result['calls']} 次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
