#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓一份演示数据（脱敏），供无 Token 的演示模式使用。

  python3 scripts/make_fixture.py

产物：references/demo_fixture.json
不含：Token、优惠券、订单号、任何账号信息。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import nutrition  # noqa: E402
import recommend as r  # noqa: E402
from mcp_client import config  # noqa: E402
from mcp_client.client import McpClient, McpError  # noqa: E402

TARGET_ITEMS = 18
MAX_DESCRIBE = 90


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    client = McpClient(config.token() or "", config.url())
    client.initialize()

    city, keyword = "上海", "麦当劳"
    stores, city_name, kw = r.query_stores(client, city, keyword)
    if not stores:
        print("没查到门店", file=sys.stderr)
        return 1

    # 夜里很多店打烊，挑一家还能拉菜单的
    store = None
    entries: list[dict] = []
    for candidate in stores:
        try:
            entries = r.load_menu(client, candidate["storeCode"])
        except McpError as exc:
            print(f"跳过 {candidate.get('storeName')}：{str(exc)[:60]}")
            continue
        if entries:
            store = candidate
            break
    if store is None:
        print("没有营业中的门店，稍后再试", file=sys.stderr)
        return 1

    store_code = store["storeCode"]
    print(f"门店: {store.get('storeName')}")
    print(f"菜单条目: {len(entries)}")

    picked: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        if len(picked) >= TARGET_ITEMS or len(seen) > MAX_DESCRIBE:
            break
        try:
            item = r.describe(client, store_code, entry)
        except McpError:
            continue
        if not item.get("code") or item["code"] in seen:
            continue
        seen.add(item["code"])

        name = item.get("name") or ""
        if any(w in name for w in ("丝巾", "玩具", "徽章", "礼盒", "盲盒", "贴纸", "海报")):
            continue
        if not r.is_main(name):
            continue

        try:
            price = r.price_items(client, store_code, [item])
        except McpError:
            continue
        if not price["total"] or price["total"] <= 0:
            continue

        picked.append({
            "code": item["code"],
            "name": name,
            "category": item.get("category") or "",
            "tags": item.get("tags") or [],
            "rounds": item.get("rounds") or [],
            "price": {"total": price["total"], "original": price["original"]},
        })

    picked.sort(key=lambda it: it["price"]["total"])
    print(f"采集商品: {len(picked)}")

    table = nutrition.table(client)

    fixture = {
        "generated_at": datetime_now(),
        "note": "脱敏演示数据：不含 Token、优惠券、订单号与账号信息",
        "city": city_name,
        "keyword": kw,
        "store": {
            "storeCode": store.get("storeCode"),
            "storeName": store.get("storeName"),
            "address": store.get("address"),
            "distance": store.get("distance"),
            "businessStatus": store.get("businessStatus"),
        },
        "stores": [
            {
                "storeCode": s.get("storeCode"),
                "storeName": s.get("storeName"),
                "address": s.get("address"),
                "distance": s.get("distance"),
                "businessStatus": s.get("businessStatus"),
            }
            for s in stores
        ],
        "items": picked,
        "nutrition_table": table,
    }

    out = ROOT / "references" / "demo_fixture.json"
    out.write_text(json.dumps(fixture, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写入 {out}（{out.stat().st_size // 1024} KB）")
    return 0


def datetime_now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


if __name__ == "__main__":
    raise SystemExit(main())
