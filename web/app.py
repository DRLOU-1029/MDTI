#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MDTI 网页调试台 —— 零依赖，只用 Python 标准库。

  python3 web/app.py --port 1145

路由：
  GET  /               页面
  GET  /api/questions  题库
  GET  /api/types      十六型档案
  POST /api/score      评分    {"answers": ["A","B",...]}
  POST /api/recommend  按人格推荐 {"type": "独稳算定", "city": "上海"}
  GET  /healthz        健康检查
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mdti_score as m  # noqa: E402
import demo  # noqa: E402
import planner as plan_mod  # noqa: E402
import recommend as reco  # noqa: E402
from mcp_client import config  # noqa: E402
from mcp_client.client import McpAuthError, McpClient, McpError  # noqa: E402


def in_demo() -> bool:
    """没有 Token、或显式打开 MDTI_DEMO 时进入演示模式。"""
    flag = os.environ.get("MDTI_DEMO", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    return not config.token()

INDEX = Path(__file__).resolve().parent / "index.html"
EXPECTED = 12


SOLD_OUT_HINTS = ("售罄", "售完", "库存", "卖完", "下架", "不足", "已抢光")


def is_sold_out(message: str) -> bool:
    # 60006 = 商品已售罄或不可售，优先用错误码判定，文案匹配作为兜底
    if "60006" in message:
        return True
    return any(hint in message for hint in SOLD_OUT_HINTS)


class Handler(BaseHTTPRequestHandler):
    server_version = "MDTI/1.1"

    # ---------- 基础 ----------

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(
            code,
            json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "请求体不是合法 JSON"})
            return None
        if not isinstance(payload, dict):
            self._json(400, {"error": "请求体必须是 JSON 对象"})
            return None
        return payload

    # ---------- 路由 ----------

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/questions":
            self._json(200, m._load(m.QUESTIONS_PATH))
        elif path == "/api/types":
            types = {}
            for code, row in m._load(m.TYPES_PATH).items():
                entry = dict(row)
                entry["budget"] = plan_mod.default_budget(code)
                types[code] = entry
            self._json(200, types)
        elif path == "/healthz":
            self._json(200, {"ok": True})
        elif path == "/api/mode":
            self._json(200, {"demo": in_demo(), "has_token": bool(config.token()),
                             "demo_ready": demo.available()})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        payload = self._read_json()
        if payload is None:
            return

        if path == "/api/score":
            self._handle_score(payload)
        elif path == "/api/recommend":
            self._handle_recommend(payload)
        elif path == "/api/plan":
            self._handle_plan(payload)
        elif path == "/api/stores":
            self._handle_stores(payload)
        elif path == "/api/reprice":
            self._handle_reprice(payload)
        elif path == "/api/checkout":
            self._handle_checkout(payload)
        elif path == "/api/order":
            self._handle_order(payload)
        elif path == "/api/cancel":
            self._handle_cancel(payload)
        elif path == "/api/coupons/bind":
            self._handle_bind_coupons()
        else:
            self._json(404, {"error": "not found"})

    # ---------- 业务 ----------

    def _handle_score(self, payload: dict) -> None:
        answers = payload.get("answers")
        if not isinstance(answers, list) or len(answers) != EXPECTED:
            self._json(400, {"error": f"answers 必须是 {EXPECTED} 个 A/B"})
            return
        if any(str(a).upper() not in ("A", "B") for a in answers):
            self._json(400, {"error": "只能选 A 或 B"})
            return
        self._json(200, m.score([str(a).upper() for a in answers]))

    def _handle_recommend(self, payload: dict) -> None:
        type_code = payload.get("type")
        city = (payload.get("city") or "").strip()
        keyword = (payload.get("keyword") or "麦当劳").strip() or "麦当劳"

        if not type_code or type_code not in m._load(m.TYPES_PATH):
            self._json(400, {"error": "type 必须是有效的四字类型码"})
            return
        if not city:
            self._json(400, {"error": "请填写城市"})
            return

        try:
            result = reco.recommend(type_code, city, keyword)
        except McpAuthError:
            self._json(503, {"error": "MCP Token 未配置或已失效，检查服务器 /opt/mdti/.env"})
            return
        except McpError as exc:
            self._json(502, {"error": f"麦当劳 MCP 返回错误：{exc}"})
            return
        except Exception as exc:  # noqa: BLE001 - 兜底，避免线程崩掉
            self._json(500, {"error": f"内部错误：{type(exc).__name__}: {exc}"})
            return

        self._json(200, result)

    def _handle_cancel(self, payload: dict) -> None:
        order_id = (payload.get("orderId") or "").strip()
        reason = str(payload.get("reasonCode") or "-1")
        if not order_id:
            self._json(400, {"error": "缺少订单号"})
            return
        if in_demo():
            self._json(200, {
                "ok": True, "orderId": order_id,
                "message": "演示订单已取消（未真实下单）",
            })
            return
        try:
            result = plan_mod.cancel_order(order_id, reason)
        except McpAuthError:
            self._json(503, {"error": "MCP Token 未配置或已失效"})
            return
        except McpError as exc:
            self._json(502, {"error": f"取消失败：{exc}"})
            return
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"内部错误：{type(exc).__name__}: {exc}"})
            return
        self._json(200, result)

    def _sold_out(self, store_code: str, items: list, message: str) -> None:
        """售罄统一回应：尽量点名是哪几件下不了单。"""
        bad = []
        try:
            bad = plan_mod.check_items(store_code, items)
        except Exception:  # noqa: BLE001
            pass
        self._json(409, {
            "error": "商品已售罄或不可售",
            "soldOut": True,
            "code": "60006" if "60006" in message else None,
            "raw": message[:200],
            "badItems": bad,
        })

    def _handle_checkout(self, payload: dict) -> None:
        store_code = (payload.get("storeCode") or "").strip()
        items = payload.get("items") or []
        if not store_code or not items:
            self._json(400, {"error": "缺少门店或商品"})
            return
        try:
            if in_demo():
                result = demo.checkout(items)
            else:
                result = plan_mod.checkout(store_code, items)
        except McpAuthError:
            self._json(503, {"error": "MCP Token 未配置或已失效"})
            return
        except McpError as exc:
            message = str(exc)
            if is_sold_out(message):
                self._sold_out(store_code, items, message)
                return
            self._json(502, {"error": f"麦当劳 MCP 返回错误：{exc}"})
            return
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"内部错误：{type(exc).__name__}: {exc}"})
            return
        self._json(200, result)

    def _handle_order(self, payload: dict) -> None:
        store_code = (payload.get("storeCode") or "").strip()
        items = payload.get("items") or []
        take_way = (payload.get("takeWay") or "").strip() or None
        if not store_code or not items:
            self._json(400, {"error": "缺少门店或商品"})
            return
        try:
            if in_demo():
                result = demo.order(items, take_way)
            else:
                result = plan_mod.place_order(store_code, items, take_way)
        except McpAuthError:
            self._json(503, {"error": "MCP Token 未配置或已失效"})
            return
        except McpError as exc:
            message = str(exc)
            if is_sold_out(message):
                self._sold_out(store_code, items, message)
                return
            self._json(502, {"error": f"麦当劳 MCP 返回错误：{exc}"})
            return
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"内部错误：{type(exc).__name__}: {exc}"})
            return
        self._json(200, result)

    def _handle_reprice(self, payload: dict) -> None:
        type_code = payload.get("type")
        store_code = (payload.get("storeCode") or "").strip()
        items = payload.get("items") or []

        if not type_code or type_code not in m._load(m.TYPES_PATH):
            self._json(400, {"error": "type 必须是有效的四字类型码"})
            return
        if not store_code:
            self._json(400, {"error": "缺少 storeCode"})
            return
        if not isinstance(items, list) or not items:
            self._json(400, {"error": "缺少 items"})
            return

        try:
            budget = float(payload.get("budget") or plan_mod.default_budget(type_code))
        except (TypeError, ValueError):
            self._json(400, {"error": "budget 必须是数字"})
            return

        try:
            if in_demo():
                result = demo.reprice(type_code, items, budget)
            else:
                result = plan_mod.reprice(type_code, store_code, items, budget)
        except McpAuthError:
            self._json(503, {"error": "MCP Token 未配置或已失效"})
            return
        except McpError as exc:
            message = str(exc)
            if is_sold_out(message):
                self._sold_out(store_code, items, message)
                return
            self._json(502, {"error": f"麦当劳 MCP 返回错误：{exc}"})
            return
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"内部错误：{type(exc).__name__}: {exc}"})
            return

        self._json(200, result)

    def _handle_stores(self, payload: dict) -> None:
        city = (payload.get("city") or "").strip()
        keyword = (payload.get("keyword") or "麦当劳").strip() or "麦当劳"
        if not city:
            self._json(400, {"error": "请填写城市或地址"})
            return

        if in_demo():
            self._json(200, demo.stores(city, keyword))
            return

        try:
            result = plan_mod.list_stores(city, keyword)
        except McpAuthError:
            self._json(503, {"error": "MCP Token 未配置或已失效"})
            return
        except McpError as exc:
            if "600014" in str(exc):
                self._json(400, {
                    "error": "没找到这个地点。请填「城市 + 地址」，例如「上海市静安区南京西路」"
                })
                return
            self._json(502, {"error": f"麦当劳 MCP 返回错误：{exc}"})
            return
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"内部错误：{type(exc).__name__}: {exc}"})
            return

        self._json(200, result)

    def _handle_plan(self, payload: dict) -> None:
        type_code = payload.get("type")
        city = (payload.get("city") or "").strip()
        keyword = (payload.get("keyword") or "麦当劳").strip() or "麦当劳"

        if not type_code or type_code not in m._load(m.TYPES_PATH):
            self._json(400, {"error": "type 必须是有效的四字类型码"})
            return
        if not city:
            self._json(400, {"error": "请填写城市"})
            return

        budget = payload.get("budget")
        if budget is not None:
            try:
                budget = float(budget)
            except (TypeError, ValueError):
                self._json(400, {"error": "budget 必须是数字"})
                return

        sort = payload.get("sort") or "budget"
        if sort not in ("budget", "price", "kcal", "count"):
            sort = "budget"

        store_code = (payload.get("storeCode") or "").strip() or None

        try:
            if in_demo():
                result = demo.plan(type_code, budget, keyword,
                                   sort=sort, store_code=store_code)
            else:
                result = plan_mod.plan(
                    type_code, city, budget, keyword, sort=sort, store_code=store_code
                )
        except McpAuthError:
            self._json(503, {"error": "MCP Token 未配置或已失效"})
            return
        except McpError as exc:
            if "600014" in str(exc):
                self._json(400, {
                    "error": "没找到这个地点。请填「城市 + 地址」，"
                             "例如「上海市静安区南京西路」，或直接填城市「上海」"
                })
                return
            self._json(502, {"error": f"麦当劳 MCP 返回错误：{exc}"})
            return
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"内部错误：{type(exc).__name__}: {exc}"})
            return

        self._json(200, result)

    def _handle_bind_coupons(self) -> None:
        """麦麦省一键领券。会真的往账户里加券，所以由用户显式触发。"""
        if in_demo():
            self._json(200, {
                "ok": True, "claimed": 0,
                "message": "演示模式不连账号，领券功能已关闭",
            })
            return
        try:
            client = McpClient(config.token() or "", config.url())
            client.initialize()
            data = client.tool_data("auto-bind-coupons", {})
        except McpAuthError:
            self._json(503, {"error": "MCP Token 未配置或已失效"})
            return
        except McpError as exc:
            message = str(exc)
            # 已经领完了不是错误，是正常状态
            if "499" in message or "暂无可领取" in message:
                self._json(200, {"ok": True, "claimed": 0, "message": "暂无可领取的优惠券"})
                return
            self._json(502, {"error": f"领券失败：{exc}"})
            return
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"内部错误：{type(exc).__name__}: {exc}"})
            return

        coupons = (data.get("data") or {}).get("successCoupons") or []
        self._json(
            200,
            {
                "ok": True,
                "claimed": len(coupons),
                "message": f"成功领取 {len(coupons)} 张" if coupons else "暂无可领取的优惠券",
                "coupons": [
                    {
                        "name": coupon.get("couponName"),
                        "image": coupon.get("couponImage"),
                    }
                    for coupon in coupons
                ],
            },
        )

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> int:
    parser = argparse.ArgumentParser(description="MDTI 网页调试台")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=1145)
    args = parser.parse_args()

    if not INDEX.exists():
        print(f"[mdti] 找不到 {INDEX}", file=sys.stderr)
        return 1

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"MDTI web listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
