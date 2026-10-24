#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 MDTI 人格跑一条真实的点餐链路：门店 → 菜单 → 详情 → 算价。

  python3 recommend.py --type 独稳算定 --city 上海
  python3 recommend.py --type 群新感随 --city 上海 --json

只读：不会创建订单。下单是另一步，需要用户确认。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import nutrition
from mcp_client import config
from mcp_client.client import McpClient, McpError

ROOT = Path(__file__).resolve().parent
TYPES = json.loads((ROOT / "references" / "types.json").read_text(encoding="utf-8"))

SHARE_HINTS = ("拼盘", "多人", "分享", "全家", "双人", "盒")
NEW_HINTS = ("上新", "限时", "新品", "新客")
CLASSIC_HINTS = ("爆款", "经典", "招牌", "全球", "人气")

MAIN_HINTS = ("堡", "卷", "饭", "套餐", "餐", "盒", "盘", "组合")
SIDE_HINTS = ("冰淇淋", "甜筒", "麦旋风", "可乐", "咖啡", "奶茶", "薯条", "薯饼", "派", "鸡块", "鸡翅", "饮料", "果汁", "圣代")

BE_TYPE = 1      # 1=到店取餐
ORDER_TYPE = 1   # 1=到店

# 常见的城市名，用于把「上海南京西路」拆成城市 + 地点
CITY_NAMES = (
    "北京", "上海", "广州", "深圳", "杭州", "成都", "重庆", "武汉", "西安",
    "南京", "天津", "苏州", "长沙", "郑州", "青岛", "沈阳", "大连", "厦门",
    "福州", "济南", "合肥", "昆明", "哈尔滨", "长春", "石家庄", "太原",
    "南昌", "贵阳", "南宁", "兰州", "海口", "乌鲁木齐", "呼和浩特",
    "银川", "西宁", "拉萨", "宁波", "无锡", "佛山", "东莞", "珠海", "温州",
)


def yuan(fen) -> float:
    """接口金额单位是分。"""
    return round((fen or 0) / 100, 2)


def is_main(name: str) -> bool:
    """是不是一份正经主食/套餐，而不是单点小食或饮料。"""
    if any(h in name for h in SIDE_HINTS):
        return False
    return any(h in name for h in MAIN_HINTS)


def kcal_key(item: dict) -> tuple:
    """热量排序键。

    部分匹配出来的热量是「下限」：拿它直接比大小，会偏向数据不全的商品；
    但如果只按覆盖率排，又会选出数据最全的巨无霸套餐。
    折中：覆盖率 ≥ 50% 才算可信，可信的按热量升序，不可信的垫底。
    """
    info = item.get("nutrition") or {}
    rounds = max(info.get("rounds") or 1, 1)
    coverage = (info.get("covered") or 0) / rounds if info else 0.0
    return (0 if coverage >= 0.5 else 1, info.get("kcal", 10 ** 9))


def _text(entry: dict) -> str:
    return f"{entry.get('category', '')} {' '.join(entry.get('tags') or [])}"


def matches_type(name: str, tags: list[str], type_code: str) -> bool:
    """这件商品是否还在这个人格的筛法之内。

    优惠券只能在这个范围内做优化，不能把人格本身换掉。
    """
    text = f"{name} {' '.join(tags or [])}"
    is_share = any(h in text for h in SHARE_HINTS)
    is_new = any(h in text for h in NEW_HINTS)

    if type_code[0] == "群" and not is_share:
        return False
    if type_code[0] == "独" and is_share:
        return False
    if type_code[1] == "新" and not is_new:
        return False
    if type_code[1] == "稳" and is_new:
        return False
    return True


def split_location(text: str) -> tuple[str, str]:
    """把「上海市静安区南京西路」拆成 (城市, 地点关键词)。

    query-nearby-stores 的 city 只吃城市名，塞整条地址会报 600014。
    """
    text = (text or "").strip()
    if not text:
        return "", ""

    if "市" in text:
        cut = text.index("市") + 1
        return text[:cut], text[cut:].strip()

    for name in CITY_NAMES:
        if text.startswith(name):
            return name, text[len(name):].strip()

    return text, ""


def query_stores(client: McpClient, city: str, keyword: str = "麦当劳") -> tuple[list[dict], str, str]:
    """查门店，返回 (按距离升序的门店列表, 城市, 关键词)。"""
    city_name, extra = split_location(city)
    if extra and keyword in ("", "麦当劳"):
        keyword = extra

    params = {
        "beType": BE_TYPE,
        "searchType": 2,
        "city": city_name,
        "keyword": keyword,
    }
    try:
        data = client.tool_data("query-nearby-stores", params)
    except McpError as exc:
        # 地点关键词搜不到时，退回按城市搜麦当劳门店
        if "600014" in str(exc) and keyword != "麦当劳":
            params["keyword"] = "麦当劳"
            data = client.tool_data("query-nearby-stores", params)
        else:
            raise

    stores = data.get("data") or []
    stores.sort(
        key=lambda s: s.get("distance") if isinstance(s.get("distance"), (int, float)) else 10 ** 9
    )
    return stores, city_name, keyword


def find_store(client: McpClient, city: str, keyword: str = "麦当劳") -> dict:
    stores, city_name, keyword = query_stores(client, city, keyword)
    if not stores:
        raise McpError(f"{city_name}「{keyword}」附近没找到麦当劳门店")
    opened = [s for s in stores if s.get("businessStatus")]
    return (opened or stores)[0]


def load_menu(client: McpClient, store_code: str) -> list[dict]:
    data = client.tool_data(
        "query-meals",
        {"beType": BE_TYPE, "orderType": ORDER_TYPE, "storeCode": store_code},
    )
    entries: list[dict] = []
    for category in data.get("data", {}).get("categories") or []:
        name = category.get("name") or ""
        for meal in category.get("meals") or []:
            code = meal.get("code")
            if not code:
                continue
            entries.append({"code": code, "category": name, "tags": meal.get("tags") or []})
    return entries


def load_coupons(client: McpClient, store_code: str) -> list[dict]:
    try:
        data = client.tool_data(
            "query-store-coupons",
            {"beType": BE_TYPE, "orderType": ORDER_TYPE, "storeCode": store_code},
        )
        return data.get("data") or []
    except McpError:
        return []


def coupon_codes(coupons: list[dict]) -> set[str]:
    codes: set[str] = set()
    for coupon in coupons:
        for product in coupon.get("products") or []:
            if product.get("productCode"):
                codes.add(product["productCode"])
    return codes


def rank(entries: list[dict], type_code: str) -> list[dict]:
    """按人格的社交/新鲜两个轴给候选打分。优惠券不参与打分。"""
    social, novelty = type_code[0], type_code[1]
    scored: list[tuple[float, dict]] = []
    for entry in entries:
        text = _text(entry)
        score = 0.0
        is_share = any(h in text for h in SHARE_HINTS)
        is_new = any(h in text for h in NEW_HINTS)
        is_classic = any(h in text for h in CLASSIC_HINTS)

        if social == "群":
            score += 3.0 if is_share else 0.0
        elif is_share:
            score -= 3.0

        if novelty == "新":
            score += 2.0 if is_new else 0.0
        else:
            score -= 2.0 if is_new else 0.0
            score += 1.0 if is_classic else 0.0

        scored.append((score, entry))

    scored.sort(key=lambda pair: -pair[0])
    return [entry for _, entry in scored]


def describe(client: McpClient, store_code: str, entry: dict) -> dict:
    detail = client.tool_data(
        "query-meal-detail",
        {
            "beType": BE_TYPE,
            "orderType": ORDER_TYPE,
            "storeCode": store_code,
            "code": entry["code"],
        },
    )
    payload = detail.get("data") or {}
    return {
        "code": entry["code"],
        "name": payload.get("name") or entry["code"],
        "category": entry.get("category", ""),
        "tags": entry.get("tags") or [],
        "rounds": payload.get("rounds") or [],
    }


def slim_rounds(rounds: list, parts: list | None = None) -> list[dict]:
    """只留展示套餐构成需要的信息，并把每层的热量/替代来源贴上去。"""
    by_round = {part["round"]: part for part in (parts or []) if "round" in part}
    out: list[dict] = []
    for index, rnd in enumerate(rounds or []):
        choices = [
            {
                "code": choice.get("code"),
                "name": choice.get("name"),
                "diff": choice.get("diffPrice") or "",
                "selected": (choice.get("quantity") or 0) > 0,
            }
            for choice in (rnd.get("choices") or [])[:8]
        ]
        extra = by_round.get(index, {})
        out.append({
            "id": rnd.get("id"),
            "name": rnd.get("name") or "",
            "choices": choices,
            "kcal": extra.get("kcal"),
            "proxy": extra.get("proxy"),
        })
    return out


def build_items(items: list[dict], coupon: dict | None) -> list[dict]:
    rows = []
    for item in items:
        row = {"productCode": item["code"], "quantity": item.get("quantity", 1)}
        if coupon and item["code"] in coupon.get("_matched", set()):
            row["couponId"] = coupon["couponId"]
            row["couponCode"] = coupon["couponCode"]
        rows.append(row)
    return rows


def price_items(
    client: McpClient, store_code: str, items: list[dict], coupon: dict | None = None
) -> dict:
    data = client.tool_data(
        "calculate-price",
        {
            "beType": BE_TYPE,
            "orderType": ORDER_TYPE,
            "storeCode": store_code,
            "items": build_items(items, coupon),
        },
    )
    body = data.get("data") or {}
    take_ways = body.get("takeWayList") or []
    take_way = next(
        (t for t in take_ways if t.get("code") == "eat-in"),
        take_ways[0] if take_ways else {},
    )
    return {
        "total": yuan(body.get("price")),
        "original": yuan(body.get("originalPrice") or body.get("productOriginalPrice")),
        "discount": yuan(body.get("discount")),
        "takeWayCode": take_way.get("code"),
        "takeWayTitle": take_way.get("title"),
        "lines": [
            {
                "name": line.get("productName"),
                "quantity": line.get("quantity"),
                "subtotal": yuan(line.get("subtotal")),
            }
            for line in body.get("productList") or []
        ],
    }


def build_reason(
    type_code: str, item_count: int, price: dict, nutrition_info: dict | None
) -> str:
    social, novelty, basis, restraint = type_code[0], type_code[1], type_code[2], type_code[3]
    saving = round(price["original"] - price["total"], 2)
    parts: list[str] = []

    if basis == "算":
        parts.append(
            f"券后 ¥{price['total']}，省 ¥{saving}"
            if saving > 0
            else f"实付 ¥{price['total']}，这单已经是最省的"
        )
    if social == "群":
        parts.append(f"人均约 ¥{round(price['total'] / max(item_count, 2), 2)}")
    if novelty == "新":
        parts.append("有新品在里面")
    if restraint == "控":
        if nutrition_info and nutrition_info.get("complete"):
            parts.append(f"约 {nutrition_info['kcal']} kcal")
        elif nutrition_info:
            parts.append("热量没算全，先收着点")
        else:
            parts.append("热量查不到，先收着点")
    else:
        parts.append("今天不称重")

    return "，".join(parts)


def recommend(type_code: str, city: str, keyword: str = "麦当劳") -> dict:
    if type_code not in TYPES:
        raise ValueError(f"未知类型：{type_code}")

    social, basis = type_code[0], type_code[2]
    want = 2 if social == "群" else 1

    client = McpClient(config.token() or "", config.url())
    client.initialize()
    calls = 1

    store = find_store(client, city, keyword)
    calls += 1
    store_code = store["storeCode"]

    entries = load_menu(client, store_code)
    calls += 1
    if not entries:
        raise McpError("该门店当前没有可售餐品")

    coupons = load_coupons(client, store_code) if basis == "算" else []
    if basis == "算":
        calls += 1

    # 控型要多看几个候选：新品往往没有营养数据，得往下翻到算得清的
    health_focus = type_code[3] == "控"
    pool_size = 10 if health_focus else 6
    detail_size = 8 if health_focus else 4

    # 候选：按人格排序
    candidates: list[dict] = []
    seen: set[str] = set()
    for entry in rank(entries, type_code):
        if entry["code"] in seen:
            continue
        seen.add(entry["code"])
        candidates.append(entry)
        if len(candidates) >= pool_size:
            break

    described: list[dict] = []
    for entry in candidates:
        try:
            described.append(describe(client, store_code, entry))
            calls += 1
        except McpError:
            continue
        if len(described) >= detail_size:
            break
    if not described:
        raise McpError("拿不到任何餐品详情")

    if health_focus:
        # 控型：先取热量，再按「主食优先 + 热量升序」排
        tbl = nutrition.table(client)
        if nutrition.JUST_LOADED:
            calls += 1
        for item in described:
            item["nutrition"] = nutrition.for_item(item["name"], item.get("rounds"), tbl)
        described.sort(key=lambda item: (0 if is_main(item["name"]) else 1, *kcal_key(item)))
    else:
        # 其余人格：主食优先，保持人格排序的先后
        described.sort(key=lambda item: 0 if is_main(item["name"]) else 1)

    coupon: dict | None = None
    picked: list[dict]

    if basis == "算":
        # 算型：逐个算价，挑最省的一份；再和「用券」比一次
        priced: list[tuple[float, dict]] = []
        for item in described[:4]:
            try:
                priced.append((price_items(client, store_code, [item])["total"], item))
                calls += 1
            except McpError:
                continue
        if not priced:
            raise McpError("所有候选都算不出价格")

        if social == "群":
            # 群型要两样：先满足份量，再在选中的商品上套券
            picked = [item for _, item in sorted(priced, key=lambda pair: pair[0])][:want]
        elif health_focus:
            # 控型：热量优先，价格其次；查不到热量的排最后
            meals = [(total, item) for total, item in priced if is_main(item["name"])] or priced
            meals.sort(key=lambda pair: (*kcal_key(pair[1]), pair[0]))
            picked = [meals[0][1]]
        else:
            meals = [(total, item) for total, item in priced if is_main(item["name"])] or priced
            picked = [min(meals, key=lambda pair: pair[0])[1]]

        # 找能用在已选商品上的券
        picked_codes = {item["code"] for item in picked}
        better: tuple[float, list[dict], dict] | None = None
        for candidate_coupon in coupons:
            matched = {
                product.get("productCode")
                for product in candidate_coupon.get("products") or []
                if product.get("productCode") in picked_codes
            }
            if not matched:
                continue
            trial = dict(candidate_coupon)
            trial["_matched"] = matched
            try:
                total = price_items(client, store_code, picked, trial)["total"]
                calls += 1
            except McpError:
                continue
            if better is None or (0 < total < better[0]):
                better = (total, picked, trial)

        # 券商品本身更便宜时，直接换成券商品 —— 但必须仍符合这个人格的筛法
        # 控型不换：热量优先于省钱，换过去可能换来一个更重的
        for candidate_coupon in ([] if health_focus else coupons):
            for product in candidate_coupon.get("products") or []:
                code = product.get("productCode")
                if not code or code in picked_codes:
                    continue
                try:
                    item = describe(client, store_code, {"code": code, "category": "", "tags": []})
                    calls += 1
                    if not matches_type(item["name"], item.get("tags") or [], type_code):
                        break
                    trial = dict(candidate_coupon)
                    trial["_matched"] = {code}
                    total = price_items(client, store_code, [item], trial)["total"]
                    calls += 1
                except McpError:
                    continue
                if total > 0 and (better is None or total < better[0]):
                    better = (total, [item], trial)
                break

        if better:
            _, picked, coupon = better

    else:
        picked = described[:want]

    price = price_items(client, store_code, picked, coupon)
    calls += 1

    # 热量：只有「控」型才核算 —— 这正是控/纵轴存在的理由
    totals = {field: 0.0 for field in nutrition.FIELDS}
    got_nutrition = False
    complete = True
    missing = 0
    if health_focus:
        tbl = nutrition.table(client)
        if nutrition.JUST_LOADED:
            calls += 1
        substituted: list[str] = []
        for item in picked:
            item_nutrition = nutrition.for_item(item["name"], item.get("rounds"), tbl)
            item["nutrition"] = item_nutrition
            if not item_nutrition:
                complete = False
                missing += 1
                continue
            got_nutrition = True
            for field in nutrition.FIELDS:
                totals[field] += item_nutrition[field]
            substituted.extend(item_nutrition.get("substituted") or [])
            if not item_nutrition.get("complete", True):
                complete = False
                missing += max(
                    item_nutrition.get("rounds", 1) - item_nutrition.get("covered", 0), 1
                )
        totals["complete"] = complete
        totals["missing"] = missing
        totals["substituted"] = list(dict.fromkeys(substituted))
    else:
        substituted = []
        for item in picked:
            item["nutrition"] = None

    nutrition_payload = (
        {
            "kcal": round(totals["kcal"]),
            "protein": round(totals["protein"], 1),
            "fat": round(totals["fat"], 1),
            "carb": round(totals["carb"], 1),
            "sodium": round(totals["sodium"]),
            "complete": complete,
            "missing": missing,
            "substituted": substituted,
            "estimated": bool(substituted),
            "line": nutrition.format_line(totals, health_focus),
            "per_yuan_kcal": (
                round(totals["kcal"] / price["total"])
                if complete and price["total"]
                else None
            ),
        }
        if got_nutrition
        else None
    )

    reason = build_reason(type_code, len(picked), price, nutrition_payload)

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
            "businessStatus": store.get("businessStatus"),
        },
        "picked": [
            {
                "code": item["code"],
                "name": item["name"],
                "quantity": 1,
                "rounds": slim_rounds(item.get("rounds") or []),
                "nutrition": item.get("nutrition"),
            }
            for item in picked
        ],
        "price": price,
        "health_focus": health_focus,
        "nutrition": nutrition_payload,
        "coupon": (
            {
                "title": coupon.get("title"),
                "couponCode": coupon.get("couponCode"),
                "valid": coupon.get("tradeDateTime"),
            }
            if coupon
            else None
        ),
        "reason": reason,
        "calls": calls,
        "city": city,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="按 MDTI 人格推荐一单")
    parser.add_argument("--type", required=True, help="四字类型码，如 独稳算定")
    parser.add_argument("--city", required=True)
    parser.add_argument("--keyword", default="麦当劳")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = recommend(args.type, args.city, args.keyword)
    except McpError as exc:
        print(f"[MCP 错误] {exc}", file=sys.stderr)
        return 4
    except ValueError as exc:
        print(f"[参数错误] {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"人格：{result['type']['code']} 「{result['type']['name']}」")
    print(f"门店：{result['store']['storeName']}（{result['store']['distance']} 米）")
    print("商品：" + "、".join(f"{i['name']}×{i['quantity']}" for i in result["picked"]))
    price = result["price"]
    print(f"实付：¥{price['total']}（原价 ¥{price['original']}，省 ¥{price['discount']}）")
    if result["coupon"]:
        print(f"用券：{result['coupon']['title']}")
    print(f"理由：{result['reason']}")
    print(f"调用：{result['calls']} 次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
