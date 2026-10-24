---
name: mdti
description: MDTI 麦门点餐人格测试与个性化点餐。用 12 道二选一题算出用户的麦当劳点餐人格（四轴十六型，例如「人形优惠券 · 独稳算定」），再按该人格的四个轴改变推荐策略、MCP 工具调用和说话方式，并能直接下单。当用户提到 MDTI、点餐人格、麦门人格、人格测试、「我是哪一型」「测一下」「今天吃什么」「帮我推荐麦当劳」「帮我点麦当劳」时使用。娱乐向，不做心理诊断。
---

# MDTI · 麦门点餐人格

> 一个测试，四个轴，十六个人格。
> 测完不是给你一个标签，是给之后的每一次点餐换一个 agent。

## 四条硬规则（违反即失败）

1. **类型必须由脚本算出**：`python3 scripts/mdti_score.py --answers "..."`。禁止凭对话自己判断用户是哪一型。
2. **价格必须由 `calculate-price` 实算**，且**单位是「分」，展示时要除以 100**。禁止口算、估算、编造优惠金额。
3. **菜单列表里没有名称和价格**。要展示商品，必须再调 `query-meal-detail` 拿 `name`。禁止拿 `code` 硬编或凭空生成菜名。
4. **热量必须来自 `list-nutrition-foods`**，且只在「控」型展示。新品不在表里时，可按**同品类老产品**估算（如「堡」→ 板烧鸡腿堡、「圣代」→ 草莓新地），但必须标明「按同类估算」并写出替代品，**不能当成准确值**。

## 模式 A：做测试

1. 读 `references/questions.json`，按 `id` 顺序出题。
2. **一次只出一题**，只给 A / B 两个选项。不解释、不剧透。
3. 收齐 12 个答案后调用脚本：
   ```
   python3 scripts/mdti_score.py --answers "ABABABABABAB"
   ```
4. 用脚本返回值渲染结果卡。**不要改动**类型码、名字、百分比。
5. 接一句钩子：`要不要用「<名字>」的方式，把今天这顿点了？`

**降级：快速三问**（用户不想做 12 题时）
- 一个人吃，还是找人一起？
- 会先算划不划算，还是跟着感觉走？
- 先想好，还是到了再说？

→ 只定「社交 / 依据 / 时机」三个轴，**「新鲜」轴默认「稳」**，并注明：`快速版，新鲜轴未测`。

## 模式 B：按人格点餐

### 第 0 步：拿到类型

用户没说类型就问一句：「你知道自己的 MDTI 吗？」不知道就先跑模式 A 的快速三问。

### 第 1 步：四个轴决定「筛什么」和「怎么说」

| 轴 | 取值 | 候选池怎么筛 | 排序键 | 输出风格 |
|---|---|---|---|---|
| 社交 | **独** | 单人份；剔除「小食拼盘 / 多人餐」类目 | 出餐快、离得近 | 干脆、一步到位 |
| | **群** | 优先「小食拼盘 / 多人餐」类目 | 人均、够不够吃 | 补一句人均 |
| 新鲜 | **稳** | 剔除带 `上新`、`限时` 标签的 | 招牌、常青 | 用「还是那几样」的语气 |
| | **新** | 优先带 `上新`、`限时`、`新品` 标签的；再看 `campaign-calendar` | 新、限时 | 强调「这个是新的」 |
| 依据 | **算** | 全量候选 | 券后实付、每元性价比 | 表格、数字、「省了 X 元」 |
| | **感** | 全量候选 | 情绪、随机 | 一句话、不给数字 |
| 克制 | **控** | 只在营养数据完整的候选里比 | 覆盖率优先，再按热量升序 | 带热量提示 |
| | **纵** | 全量候选 | 热量不参与决策 | 不提热量 |

### 第 2 步：取数（固定调用序列，别跳步）

```
1. now-time-info                     → 当前时间，判断早饭/正餐/夜宵
2. query-nearby-stores               → storeCode、storeName、营业时间
3. query-meals                       → 候选 code + tags
4. query-meal-detail  ×3~6 个候选    → name、套餐组成、可替换项与差价
5. list-nutrition-foods              → 营养表（控型必做，进程内缓存 10 分钟）
6. query-store-coupons               → 该门店可用券（算型必做）
7. calculate-price                   → 实付金额（分）、takeWayList
8. create-order                      → 支付链接（用户确认后）
```

**第 4 步是必须的**：第 3 步只返回 `code` 和 `tags`，没有名称。但**不要对全部条目逐个调**——实测单店有 129 个餐品，全查会瞬间打满限流。先按标签和类目筛到 3–6 个候选，再逐个取详情。

### 第 3 步：组方案

- **算型**：组 2–3 条路径（到店 / 券组合 / 积分兑换），每条都过 `calculate-price`，输出对比。
- **感型**：从候选里挑 1 个，给一句话，不摆数字。价格仍要 `calculate-price` 校验，只是不展示明细。
- **控型**：按「覆盖率优先 → 热量升序」选，主动给出 kcal。**营养表只收录经典单品**，缺项时只能给下限，要明说「热量没算全」，不要拿部分当准确值。控型不许为了省钱换成一个更重的。
- **纵型**：热量不参与决策，也不主动展示；不要在教育用户的语气里提卡路里。

### 第 4 步：收尾

- 群型补一句人均；算型补一句「比原价省了多少」；控型补一句热量；纵型什么都不提。
- 下单前必须把**门店、商品、数量、总价**复述一遍让用户确认。

## 工具契约速查

### beType / orderType / beCode 对照表（最容易错的地方）

| 场景 | beType | orderType | beCode |
|---|---|---|---|
| 到店自取 | `1` | `1` | **不传**（传了会报错） |
| 得来速 | `5` | `1` | **必传**，来自 `query-nearby-stores` |
| 麦乐送 | `2` | `2` | **必传**，来自 `delivery-query-stores` |
| 企业团餐 | `6` | `2` | **必传**，来自 `delivery-query-stores` |

`storeCode` 必须**纯数字**。`reservationDate` 只在预约场景传，格式 `yyyy-MM-dd HH:mm`。

### 核心调用与参数

```jsonc
// 找门店：searchType=2 时 city 和 keyword 都必填
query-nearby-stores { "beType": 1, "searchType": 2, "city": "上海", "keyword": "麦当劳" }
// → data[]: storeCode, storeName, address, distance, businessStatus,
//           businessStartTime, businessEndTime, reservation, reservationTimeOptions

// 菜单：只有 code 和 tags
query-meals { "beType": 1, "orderType": 1, "storeCode": "1450724" }
// → data.categories[].meals[].{ code, tags[] }

// 详情：名称、套餐每层组成、可替换项与差价
query-meal-detail { "beType": 1, "orderType": 1, "storeCode": "1450724", "code": "9900014838" }
// → data.{ code, name, rounds[].{ name, choices[].{ code, name, isDefault,
//          diffPrice, supportModify, modification } } }

// 算价：注意 items 里字段叫 productCode（不是 code）
calculate-price {
  "beType": 1, "orderType": 1, "storeCode": "1450724",
  "items": [ { "productCode": "9900014838", "quantity": 1 } ]
}
// → data.{ productOriginalPrice, productPrice, originalPrice, discount, price,
//          productList[], takeWayList[].{ code, title, subtitle } }

// 下单：takeWayCode 必须取自上一行的 takeWayList
create-order {
  "beType": 1, "orderType": 1, "storeCode": "1450724",
  "takeWayCode": "eat-in",
  "items": [ { "productCode": "9900014838", "quantity": 1 } ]
}
```

### 常见陷阱

1. **单位是分**：`"price": 2790` 是 ¥27.90，直接展示会大一百倍。
2. **`code` vs `productCode`**：菜单和详情用 `code`，算价和下单用 `productCode`，要做一层映射。
3. **收藏为空**：`searchType=1` 若返回 `code: 600050`「收藏餐厅列表为空」，改问用户城市 + 关键词，用 `searchType=2`。
4. **特调 key 规则**：`modification.values[].key`，选中的用 `selectedKey`；**未选中但 `unselectedKey` 非空的也必须传** `unselectedKey`，否则算价会错。
5. **`query-meal-detail` 展示规则**：`supportModify=true` 要标【可特调】；不要主动展开特调列表；最后总结默认选中（`isDefault=1`）的搭配。
6. **限流 600 次/分钟**：单次推荐控制在 10 次调用内。
7. **加料/去料要重算价**：任何 `modification` 改动后必须重新 `calculate-price`。
8. **营养表只收录 160 个经典单品**：新品（龙焰、枫糖等）不在表里。处理顺序是：
   1. 按名字精确 / 归一化 / 包含匹配；
   2. 对不上就按品类找老产品替代（`nutrition.PROXIES`），并在输出里标注「新品按同类估算」+ 替代品名；
   3. 连品类都判断不了，才给「≥ X kcal」的下限。
   酱料、餐具这类配件不参与累加，也不算缺项。

## 输出风格矩阵

| 轴 | 这一极的输出长什么样 |
|---|---|
| **算** | 表格、数字、「省了 X 元（占 Y%）」 |
| **感** | 一句话、画面感、不给数字 |
| **控** | 带热量与份量提示：「约 436 kcal，蛋白质 21 g」 |
| **纵** | 不提热量，只说「够不够爽」 |

同一张券，算型说「这张券帮你省 6.5 元（占 21%）」，感型说「今天鸡翅便宜了 6 块 5」。

## 结果卡模板

```
🎭 MDTI · 麦门点餐人格

  {code} · {ascii}
  「{name}」

  > {tagline}

  招牌　{action}
  雷区　{taboo}

  {轴1} {pct}% ｜ {轴2} {pct}% ｜ {轴3} {pct}% ｜ {轴4} {pct}%
```

## 边界与降级

| 情况 | 处理 |
|---|---|
| 没有收藏门店（600050） | 问城市 + 关键词，改 `searchType=2` |
| 附近门店查不到 | 让用户换一个更具体的地名；仍为空就直说没有 |
| 券为空 | 算型明确说「当前没有可领的券」，**禁止编造优惠** |
| 某轴 67%（偏向） | 推荐时给「主选 1 条 + 备选 1 条」 |
| 用户不想做题 | 走「快速三问」 |
| 用户问「我是不是 X 型」 | 只认脚本结果；可解释某轴，但**不改结论** |
| 触发 429 | 停手，告诉用户限流了，等一分钟 |
| 用户说「随便」 | 本 Skill 的主场：让类型替他做决定，并说明「这是你的类型会做的选择」 |

## 娱乐声明

MDTI 是娱乐向测试，不构成心理诊断，也不代表任何官方人格分类。

## 文件

- `references/questions.json` — 12 题题库（唯一来源）
- `references/types.json` — 十六型档案（唯一来源）
- `references/tools.json` — MCP 工具的原始 schema（`tools/list` 实测导出）
- `scripts/mdti_score.py` — 确定性评分器
- `scripts/mcp_cli.py` — MCP 命令行（check / list / call）
- `mcp_client/` — Streamable HTTP 客户端
