# MCP 集成说明

本文说明 MDTI 实际接入的麦当劳 MCP Server、用到的 Tool、调用流程与业务价值。

---

## 1. 接入的 MCP Server

| 项 | 值 |
|---|---|
| 地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP（`POST` + `Accept: application/json, text/event-stream`） |
| 鉴权 | `Authorization: Bearer <MCP_TOKEN>` |
| 协议版本 | `2025-06-18`（握手返回 `mcd-mcp 1.0.0`） |
| 会话 | 无状态（服务端不下发 `Mcp-Session-Id`） |
| 限流 | 每个 Token 600 次/分钟，超出返回 429 |

实现见 [`mcp_client/client.py`](./mcp_client/client.py)：零依赖，仅用 `urllib` + `json`，同时解析 JSON 与 SSE 两种响应。

---

## 2. 用到的 Tool（9 个）

| # | Tool | 在本项目中的用途 | 调用时机 |
|---|---|---|---|
| 1 | `query-nearby-stores` | 按城市 + 地点关键词找可点餐门店 | 出方案 / 查门店 |
| 2 | `query-meals` | 拉门店可售餐品编码与标签 | 出方案（构建候选池） |
| 3 | `query-meal-detail` | 拿餐品名称、套餐每层组成、可替换项与差价 | 候选详情 / 换餐重算 |
| 4 | `calculate-price` | 实算价格（含优惠）、返回取餐方式 | 单价估算 / 组合复核 / 结算 |
| 5 | `query-store-coupons` | 查该门店可用券 | 「算」型人格比价 |
| 6 | `list-nutrition-foods` | 取营养表，按套餐分层累加热量 | 「控」型人格核算热量 |
| 7 | `create-order` | 创建订单并返回支付链接 | 用户确认下单 |
| 8 | `cancel-order` | 取消待支付订单 | 用户在购物车取消订单 |
| 9 | `auto-bind-coupons` | 麦麦省一键领券 | 用户主动点击领券 |

> 未使用的能力：抽奖（`draw-lottery` 等）、积分商城（`mall-*`）、主题活动预约（`party-*`）、企业团餐（`query-meal-assistance`）。这些与「点餐人格」主线无关，刻意不堆砌。

---

## 3. 调用流程

### 3.1 出方案（`POST /api/plan`）

```
query-nearby-stores  ── storeCode
        │
query-meals ── 140 条 { code, tags }
        │
  按人格四轴排序 + 过滤（排除周边礼品、非主食、不符社交轴的多/单人份）
        │
query-meal-detail × 6~10  ── name / rounds（每层组成与可选差价）
        │
   ┌────┴────┐
「算」型      「控」型
query-store-coupons   list-nutrition-foods
   └────┬────┘
calculate-price × N  ── 每条候选的真实单价
        │
  按「性格 + 预算」生成最多 4 个组合（控型压预算下方，纵型贴线或略超）
        │
calculate-price（组合复核）── 实付金额（分）、takeWayList
```

**要点**

- `beType=1`（到店自取）、`orderType=1`，此场景下 `beCode` **不能传**（传了会报错）。
- `storeCode` 必须纯数字。
- 价格单位是**分**，展示前除以 100。
- 菜单列表只有 `code` 和 `tags`，**没有名称和价格**，必须再调 `query-meal-detail`。
- 单店 140 条餐品，不可能全量取详情（会打满限流），所以先按人格排序取前 6~10 个。

### 3.2 换套餐子项（`POST /api/reprice`）

这是本项目最关键的一处集成。**换套餐里的某一项，走的是 `roundList`，不是 `modification`。**

```jsonc
{
  "productCode": "9900004064",
  "quantity": 1,
  "roundList": [
    { "round": "1", "comboItemList": [{ "code": "1100", "quantity": 1 }] },
    { "round": "2", "comboItemList": [{ "code": "4820", "quantity": 1 }] },
    { "round": "3", "comboItemList": [{ "code": "3071", "quantity": 1 }] }
  ]
}
```

实测：默认三件套 3450 分，主食换巨无霸（+¥2）→ 3650 分，换麦辣（−¥1）→ 3350 分，完全按 `diffPrice` 变化。

换完之后再用同一份选择重算热量：把选中的那一项标记为默认，按 `query-meal-detail` 的分层顺序累加营养表。

### 3.3 结算与下单

```
POST /api/checkout   → calculate-price（汇总所有条目）→ 实付 + takeWayList（堂食/外带）
POST /api/order      → calculate-price（复核）
                     → create-order { storeCode, beType, orderType, takeWayCode, items }
                     → 返回 orderId + payH5Url + expirePayTime
```

- `takeWayCode` 只能取自 `calculate-price` 返回的 `data.takeWayList[].code`（实测 `eat-in` / `take-in-store`）。
- 下单只创建**待支付**订单，返回支付链接，不代付；订单 15 分钟未支付自动失效。

### 3.4 领券与取消

- `auto-bind-coupons`：无参数，一次领取当前所有可领券；已领完时返回 `499 暂无可领取的优惠券`，这是正常状态而非错误。
- `cancel-order`：`{ orderId, cancelReasonCode }`，原因码 `1` 改主意 / `2` 重复下单 / `3` 点错 / `4` 地址错 / `5` 时间错 / `-1` 其它。

### 3.5 演示模式

公开演示实例不连 MCP：`references/demo_fixture.json` 保存了一份脱敏快照（18 个餐品、5 家门店、158 条营养数据），选品、排序、换餐、结算全部在本地完成，**0 次 MCP 调用**，也不会产生真实订单。

---

## 4. 集成中踩到的坑（都已处理）

| 现象 | 原因 | 处理 |
|---|---|---|
| 热量算出来偏低 | 营养表只有 160 个经典单品，新品不在表里 | 按品类找老产品替代（堡→板烧鸡腿堡），并标注「按同类估算」+ 替代品名 |
| 换餐价格不变 | 误用 `modification` | 改用 `roundList` |
| 套餐被当成一个堡 | 该商品没有返回 `rounds` | 仅在无分层时才按单品估；有分层必须逐层累加 |
| 结算时报售罄 | `calculate-price` 返回 `60006` | 结算/下单/换餐三处统一识别，逐件复核并点名是哪件 |
| 夜里拉不到菜单 | 门店打烊返回 `600057` | 自动换一家营业中的门店 |
| 购物车结算总价对不上 | 页面上是单价之和 | 结算必须重新走 `calculate-price` |
| 城市填了整条地址 | `query-nearby-stores` 只吃城市名 | 自动拆分「城市 + 地点关键词」，关键词搜不到再退回「麦当劳」 |

---

## 5. 业务价值

1. **把「今天吃什么」变成一个 30 秒的决定。** 12 道题产出一个可执行的偏好档案，之后每次点餐不用再重新做二十个决定。
2. **用 `roundList` 把「改套餐」留在页面上。** 用户想换主食时不需要重新搜索，改完立刻看到新价格和热量——这是最容易产生放弃的一步。
3. **预算是一等公民。** 滑块 + 性格化的落点策略，让「推荐」直接对齐「我愿意花多少钱」，而不是先推荐再让用户自己算。
4. **不幻觉。** 价格全部来自 `calculate-price`，热量全部来自 `list-nutrition-foods`，拿不到就明说「数据未收录」，不用估算值糊弄。
5. **可回退的闭环。** 下单只创建待支付订单，用户可以随时取消；售罄、门店打烊、限流都有明确提示。

---

## 6. 配置

复制 `mcp-config.example.json`，把环境变量替换成自己的实际值：

```jsonc
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "${MCD_MCP_URL}",
      "headers": { "Authorization": "Bearer ${MCD_MCP_TOKEN}" }
    }
  }
}
```

本项目的 `.env` 写法见 [`.env.example`](./.env.example)，`MCD_MCP_TOKEN` 也可以直接用同名环境变量覆盖。
