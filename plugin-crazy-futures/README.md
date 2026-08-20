# 疯狂期货

《疯狂期货》是一个 4–8 人金融期货交易桌游插件。每局固定进行 8 个“月份”，玩家通过公开事件、个人信息、蛇形竞价、保证金管理和终局现货回归争夺最高净资产。

## 运行资料

- 可编辑规则源稿：[`docs/RULEBOOK.md`](docs/RULEBOOK.md)
- 可编辑卡牌源稿：[`docs/CARD_SET.md`](docs/CARD_SET.md)
- 当前规则：`crazy-futures-rulebook-v0.1-balanced-ladder.pdf`
- 当前卡牌：`crazy-futures-card-set-v0.2-balanced-ladder.xlsx`
- 历史规则：`crazy-futures-rulebook-v0.1.pdf`
- 历史卡牌：`crazy-futures-card-set-v0.1(1).xlsx`
- 美术资产与生成说明：`image/README.md`
- 后端运行时卡牌目录：`data/cards.json`

游戏运行时不会读取 PDF 或 XLSX。`data/cards.json` 已固化 v0.2 的 67 种个人牌设计（共 160 张）、20 张公共事件牌和 51 格偶数价格阶梯。

## 回合流程

1. 未破产玩家从当轮起始玩家开始依次决定是否借贷。
2. 每人自动抽取 2 张个人牌，随后自动翻开并结算 2 张公共事件。
3. 按 `1…N、N…1` 的蛇形顺序发起竞价，每名玩家每轮发起两次。
4. 从起始玩家开始循环出牌；每次可打一张牌、对封板商品只减仓，或放弃。
5. 一整圈无人行动后结算现货持续效果、期货损益、保证金、强平和手牌上限。
6. 第 8 轮结算后，全部期货向现货价格回归并计算最终净资产。

## 联机动作

前端只通过 `@game-hall/plugin-sdk` 提交以下动作：

- `borrow`：本轮借贷，金额必须为 10 万的整数倍，也可提交 0。
- `start_auction`：选择商品、方向和合法价格格发起竞价。
- `bid` / `pass_bid`：改善当前报价或永久退出本次竞价。
- `play_card` / `reduce_only` / `pass_card`：执行一次出牌阶段行动。
- `discard_cards`：完成信息置换或轮末手牌上限弃牌。
- `choose_liquidation`：逐份选择交易所强制平仓的商品。
- `resign`：关闭当前头寸并退出本局。

所有现金、头寸、报价、卡牌目标和胜负均由后端重新校验。`view()` 只向本人返回手牌和预见结果，录像状态不包含任何隐藏牌面。

## 本地验证

在已经初始化 `game-hall-community-games` 子模块的游戏大厅根目录执行：

```bash
.venv/bin/python -m pytest game-hall-community-games/plugin-crazy-futures/tests
.venv/bin/python -m pytest backend/tests/test_game_plugins.py
npm --prefix frontend run test:run -- ../game-hall-community-games/plugin-crazy-futures/frontend
npm --prefix frontend run plugins:sync
npm test
npm run build
```
