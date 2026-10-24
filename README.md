# 🎭 MDTI · 麦门点餐人格

> 12 道题，测出你的麦当劳点餐人格。
> 测完不是给你一个标签，是给之后每一次点餐换一个 agent。

MDTI（McDonald's Type Indicator）是一个跑在麦当劳 MCP 上的 Agent Skill：12 道二选一题 → **四轴十六型** → 四个策略开关直接改变 agent 点餐时的候选池、排序键和说话方式。

同一句「今天吃什么」，十六型会得到十六种答案。

---

## 为什么不是又一个 MBTI 测试

1. **类型是算出来的，不是编的。** 12 个答案进确定性脚本，同一组答案永远得到同一个类型，模型无权改判。
2. **类型是配置，不是装饰。** 四个轴各自对应一组 MCP tool 调用：算型必须走券 + 实算价，感型只给结论；控型必须核算热量，纵型不看热量。
3. **价格必须落地。** 任何金额都来自 `calculate-price`，不口算、不估算、不编优惠。

它解决的不是「吃什么」，是**「每次都要重新做二十个决定」**。测一次，以后这类决定交给一个有你性格的 agent。

---

## 目标用户

- **每天中午纠结五分钟的打工人**：想要一个替自己做决定的东西，而不是再刷一遍菜单。
- **在意热量但懒得算的人**：控型人格会在预算内挑热量更低的组合，并把每一层的热量摊开给你核对。
- **预算敏感的学生党 / 刚毕业的人**：滑块定预算，方案贴着预算走，还能自动用掉可用的券。
- **一群人点餐时的组织者**：群型人格按「人均」和「份量」出方案，避免一个人替全桌做二十个决定。

## 测试链接

 http://49.234.44.131:1145/
 
---

## 安装

无第三方依赖，只需要 Python 3.9+。

```bash
git clone https://github.com/DRLOU-1029/MDTI.git mdti
cd mdti

# 1. 配置 MCP Token（写到 .env，不要提交）
cp .env.example .env
#   编辑 .env，把 MCD_MCP_TOKEN 填成你自己的 Token

# 2. 验证接通
python3 scripts/mcp_cli.py check     # 打印出服务器信息即成功
python3 scripts/mcp_cli.py list      # 列出全部工具

# 3. 启动网页
python3 web/app.py --port 1145
```

**没有 Token 也能跑。** 不填 Token 会自动进入演示模式，用脱敏快照走完整个流程（不会产生真实订单）：

```bash
python3 web/app.py --port 1145                # 无 Token → 自动演示
MDTI_DEMO=1 python3 web/app.py --port 1145    # 有 Token 也强制演示
```

---

## 快速开始

```bash
python scripts/mdti_score.py --interactive      # 逐题作答
python scripts/mdti_score.py --answers AAAAAAAAAAAA
python scripts/mdti_score.py --answers "A,B,A,B,A,B,A,B,A,B,A,B" --json
python scripts/mdti_score.py --list             # 看题库
```

输出：

```
🎭 MDTI · 麦门点餐人格

  独稳算控 · SFCR
  「热量会计」

  > 点单之前先看热量，超了就换一个。

  招牌　站在菜单前，心里已经在算今天还能吃多少
  雷区　想吃的那款没有轻量版

  独 100% ｜ 稳 100% ｜ 算 100% ｜ 控 100%
```

---

## 接入麦当劳 MCP

「类型 → 推荐 → 下单」这条链路依赖官方 MCP Server（Streamable HTTP，`https://mcp.mcd.cn`）。

### 1. 填 Token

把 MCP Token 写进项目根目录的 `.env`：

```bash
MCD_MCP_TOKEN=你的_token
```

或用环境变量（优先级更高）：

```bash
export MCD_MCP_TOKEN=你的_token
# Windows PowerShell: $env:MCD_MCP_TOKEN='你的_token'
```

`.env` 已在 `.gitignore` 中，不会被提交。`MCD_MCP_URL` 默认就是官方地址，一般不用改。

### 2. 验证

```bash
python3 scripts/mcp_cli.py check     # 握手，打印服务器信息
python3 scripts/mcp_cli.py list      # 列出全部工具及其参数
python3 scripts/mcp_cli.py call query-meals --args '{"storeCode":"..."}'
```

`check` 能打印出服务器名和协议版本，就说明 Token 生效了。

---

## 演示模式（无需 Token）

没有 MCP Token 也能完整体验：选品、预算、排序、换餐、加购物车全部离线运行，
数据来自 `references/demo_fixture.json`（脱敏快照，**0 次 MCP 调用**）。

```bash
# 没有 Token 时自动进入演示模式
python3 web/app.py --port 1145

# 有 Token 但想强制演示
MDTI_DEMO=1 python3 web/app.py --port 1145
```

演示模式下：顶栏显示「演示模式」横幅，下单返回的是**演示订单**（不产生真实订单、没有支付链接），领券功能关闭。

重新抓快照（需要 Token）：

```bash
python3 scripts/make_fixture.py
```

## 发布前检查

- [x] `.env` 不进版本库（已在 `.gitignore`）
- [x] `references/tools.json` 已清除其中的示例订单号
- [x] `references/demo_fixture.json` 不含券码 / 订单号 / 手机号 / 优惠券字段
- [x] 公网实例用演示模式启动；真实实例只监听内网或本机
- [ ] 公网实例启动前确认 `.env` 里没有真实 Token（或显式 `MDTI_DEMO=1`）

---

## 网页调试台

```bash
python3 web/app.py --port 1145
```

打开 `http://<服务器>:1145`：答完 12 题拿到人格卡 → 拖预算滑块 → 出最多 4 个方案（真实门店、真实价格、真实可用券）。

**预算默认值由性格给**，也可以自己拖：

| 类型 | 默认预算 |
|---|---|
| 热量会计（控·算） | ¥26 |
| 麦星吞噬者（纵·感） | ¥58 |
| 团餐管家（群·算控） | ¥120 |

性格还决定四个方案**落在预算的哪一侧**：控型扎堆在下方，纵型贴着或略微超；算型把落点收紧到预算附近，感型铺开。

```bash
curl -X POST http://127.0.0.1:1145/api/plan \
  -H 'Content-Type: application/json' \
  -d '{"type":"独稳感纵","city":"上海","budget":58}'
```

实测（同一家店）：

| 类型 / 预算 | 四个方案 |
|---|---|
| 热量会计 ¥30 | ¥22.5 → ¥29.9，全部在预算下方 |
| 麦星吞噬者 ¥40 | ¥34.5 → ¥56.8，从「留有余地」到「超出不少」 |
| 团餐管家 ¥120 | ¥88.7 → ¥122.4，带人均和热量 |

第一次出方案要拉菜单逐项算价（约 20–30 次调用），之后拖滑块只重算组合，1 秒内返回。

「控」型会额外核算热量（`list-nutrition-foods` 按套餐每层默认选项累加），「纵」型不显示。

---

## 四个轴

| 轴 | 两极 | 本质问题 | 驱动什么 |
|---|---|---|---|
| 社交 | **独 / 群** | 你的能量来自一个人，还是一桌人 | 单人份 vs 分享装、团餐、人均 |
| 新鲜 | **稳 / 新** | 你信老几样，还是信「新品」两个字 | 是否过滤新品、是否查活动日历 |
| 依据 | **算 / 感** | 你靠算账，还是靠那一下 | 券 + 积分 + 实算价，还是随机 |
| 克制 | **控 / 纵** | 你收着，还是放开 | 热量核算、份量、轻量替换 |

每个轴 3 道题，二选一，不会平票：3:0 判为「强烈」，2:1 判为「偏向」。

**分布是均分的**：4096 种作答组合，十六型各占 256 种。不会有人天生更容易测成某一型，这也是它敢叫「测试」而不是「抽签」的原因。

---

## 十六型

| 类型 | 名字 | 一句话 |
|---|---|---|
| 独稳算控 `SFCR` | 热量会计 | 点单之前先看热量，超了就换一个。 |
| 独稳算纵 `SFCI` | 人形优惠券 | 券算得比谁都精，但从不点沙拉。 |
| 独稳感控 `SFVR` | 八分饱 | 每次都说「够了」，然后真的够了。 |
| 独稳感纵 `SFVI` | 麦星吞噬者 | 你点的单，店员会跟后厨确认两遍。 |
| 独新算控 `SNCR` | 配料表读者 | 新品上市，你先翻营养成分表。 |
| 独新算纵 `SNCI` | 新品测评员 | 上新当天就到，回来能说出三条缺点。 |
| 独新感控 `SNVR` | 试吃员 | 新的都要试，但只试一口。 |
| 独新感纵 `SNVI` | 菜单清道夫 | 上新第一周，你把整页点了一遍。 |
| 群稳算控 `GFCR` | 团餐管家 | 让一桌人都满意，且不超预算。 |
| 群稳算纵 `GFCI` | 预算杀手 | 你算预算，是为了把它花光。 |
| 群稳感控 `GFVR` | 半份党 | 一桌人都点大份，只有你要半份。 |
| 群稳感纵 `GFVI` | 加桌王 | 老几样摆满一桌，你问的是下一轮。 |
| 群新算控 `GNCR` | 满减工程师 | 组局之前先把满减算明白。 |
| 群新算纵 `GNCI` | 凑单狂 | 你永远在群里问：还差几块？ |
| 群新感控 `GNVR` | 可乐党 | 热闹是他们的，你只要一杯。 |
| 群新感纵 `GNVI` | 新品蝗虫 | 新出一样点一样，桌子很快就摆不下。 |

> MDTI 是娱乐向测试，不构成心理、医疗或营养建议。所有人设均为玩梗，**不鼓励暴饮暴食或食物浪费**；餐品信息、价格与供应状态以麦当劳官方渠道的实时结果为准。

---

## 目录

```
mdti/
├── SKILL.md                 # 技能入口：测试流程 + 类型 → 工具路由表
├── recommend.py             # 单次推荐（门店→菜单→详情→算价）
├── planner.py               # 预算规划 / 换餐重算 / 结算下单
├── demo.py                  # 无 Token 演示模式
├── nutrition.py             # 营养表解析 + 同类替代估算
├── mcp_client/              # Streamable HTTP 客户端（零依赖）
├── web/                     # 网页调试台（零依赖）
├── references/
│   ├── questions.json       # 12 题题库
│   ├── types.json           # 十六型档案
│   ├── tools.json           # MCP 工具 schema（已脱敏）
│   └── demo_fixture.json    # 演示快照（已脱敏）
├── scripts/
│   ├── mdti_score.py        # 确定性评分器
│   ├── selftest.py          # 自检
│   ├── mcp_cli.py           # MCP 命令行
│   └── make_fixture.py      # 抓演示快照
└── deploy/                  # 部署与运维脚本
```

> MDTI 是娱乐向测试，不构成心理诊断，也不代表任何官方人格分类。
