# 社区游戏接入手册

本仓库是游戏大厅的社区游戏源码仓库，可以作为可选 Git Submodule 挂载到主项目的 `game-hall-community-games/`。每款游戏独立维护自己的规则、状态、界面、资源和测试；主项目只提供稳定的房间平台、游戏注册表和插件 SDK。主项目未初始化本仓库时仍能以仅含官方游戏的形态构建和运行。

接入一款普通游戏时，不需要修改主项目的大厅、路由、房间、账号、Socket 或战绩代码。完成插件目录后，由本仓库根部的 `registry.json` 决定是否发布。

> 插件会与主服务在同一个 Python 进程运行，前端也会进入主站构建，因此这里只接收经过信任和代码审核的源码插件。陌生或不可信代码必须使用独立服务和 sandbox iframe 隔离。

## 1. 先理解两份清单

插件 API v1 将“游戏是什么”和“生产是否发布”分开管理：

- `plugin-your-game/manifest.json`：游戏自己的身份、版本、人数、平台能力、战绩类型和默认规则。
- 根目录 `registry.json`：维护者控制的发布注册表。只有这里登记为 `enabled` 或 `deprecated` 的插件会进入构建和后端加载。

插件不能通过修改自己的 manifest 给自己批准上线，也不需要修改主项目注册表。

当前仓库内容：

| 类型 | 目录 | 是否发布 |
| --- | --- | --- |
| 正式游戏 | `plugin-cheat-poker/` | 是 |
| 正式游戏 | `plugin-crazy-futures/` | 是 |
| 正式游戏 | `plugin-pyramid-solitaire/` | 是 |
| 示例游戏 | `plugin-number-vault/` | 是 |
| 示例游戏 | `plugin-star-stones/` | 是 |
| 最小模板 | `plugin-counter-demo/` | 否 |

两个示例游戏会按正式插件进入生产大厅，也可以用来参考单人和双人游戏的完整实现；最小模板只用于复制开发，不会被生产构建或后端导入。

## 2. 标准目录

目录名通常与插件 ID 一致，并以 `plugin-` 开头：

```text
game-hall-community-games/
├── README.md
├── registry.json                 # 生产发布注册表
├── registry.schema.json
├── plugin.schema.json            # manifest API v1 约束
├── plugin-counter-demo/          # 可复制的最小模板
└── plugin-your-game/
    ├── manifest.json             # 必需：游戏声明
    ├── README.md                 # 必需：玩法、动作、状态和维护说明
    ├── backend/
    │   ├── plugin.py             # 必需：固定后端入口
    │   ├── engine.py             # 推荐：规则引擎
    │   ├── state.py              # 可选：状态模型
    │   └── ...
    ├── frontend/
    │   ├── GameView.vue          # 必需：固定前端入口
    │   ├── components/           # 可选：本游戏组件
    │   ├── composables/          # 可选：本游戏逻辑
    │   └── assets/
    │       ├── catalog-dark.webp # 发布必需：深色大厅图标
    │       └── catalog-light.webp # 发布必需：浅色大厅图标
    └── tests/                    # 推荐：规则与界面测试
```

`plugin.py` 和 `GameView.vue` 是固定入口，其他文件可以自由拆分，但只能使用插件目录内的相对导入或公开 SDK。

## 3. 最快新增一款游戏

在社区仓库根目录执行：

```bash
cp -R plugin-counter-demo plugin-your-game
```

然后按顺序完成：

1. 将目录和 `manifest.id` 改成唯一的 `plugin-*` ID。
2. 填写版本、作者、许可证、人数、能力和战绩类型。
3. 在 `backend/` 实现服务端规则，在 `frontend/` 实现界面。
4. 在 `frontend/assets/` 制作成对的深浅大厅图标。
5. 在本插件的 `README.md` 写清动作名称、payload、隐藏信息和结算方式。
6. 为关键规则、权限边界和主要界面补测试。
7. 在尚未加入根 `registry.json` 的状态下完成开发。
8. 运行插件测试、全量测试和生产构建。
9. 由维护者审核后把插件加入 `registry.json`，再次完整验证并发布。

一款普通新游戏的开发提交应主要修改自己的目录。只有平台能力确实不足时，才单独提交主项目 SDK 或插件 API 升级。

### 大厅图标

每款准备发布的新游戏必须在自己的 `frontend/assets/` 中提供：

- `catalog-dark.webp`：深色主题使用。
- `catalog-light.webp`：浅色主题使用。

两张图由主项目构建脚本自动发现并写入统一游戏注册表，不需要修改 manifest、不需要升级 `apiVersion`，也不需要在主仓库为具体插件登记图标路径。旧插件两张都不存在时会继续使用公共占位图；只提供一张、文件不是 WebP 或尺寸不正确会直接构建失败。

图标必须遵守与官方游戏相同的产品视觉规范：

- WebP、sRGB、`768 × 768`、质量 90，使用完整方形背景且不透明。
- 深浅版保持完全相同的物体数量、几何轮廓、镜头、构图和空间关系，只切换材质与背景。
- 使用略俯视的 3/4 产品镜头、圆角双层基座、4–6 层精密材质、柔和高光与真实接触阴影。
- 黑、白、灰和金属中性色占 80%–90%，只使用一种低饱和点缀色。
- 主体放在中央 82% 安全区内，并在 142、104、72 像素三档检查识别度。
- 不得出现人物、场景截图、大段文字、Logo、水印、电竞盾牌、奇幻徽章、强霓虹或廉价手游边框。
- 大厅图标只提交最终的 `catalog-dark.webp` 和 `catalog-light.webp`；对应 PNG、SVG、生成草稿和源文件放在运行时目录之外。游戏实际运行需要的其他资源仍可保留在 `frontend/assets/`。

图标必须准确表达当前玩法，不能为了好看增加不存在的棋子、卡牌数量或规则元素。发布前应将深浅版与至少三款官方图标并排检查，确认属于同一视觉家族，同时能在不看标题时辨认游戏。

## 4. manifest.json API v1

完整约束见 `plugin.schema.json`。可直接复制下面的清单：

```json
{
  "$schema": "../plugin.schema.json",
  "apiVersion": 1,
  "version": "1.0.0",
  "author": "Your Name",
  "license": "UNLICENSED",
  "id": "plugin-your-game",
  "name": "游戏名称",
  "description": "显示在游戏目录中的一句话简介",
  "category": "策略游戏",
  "tone": "your-game",
  "roomLayout": "standard",
  "players": {
    "min": 2,
    "max": 4,
    "label": "2–4 人"
  },
  "capabilities": {
    "guests": true,
    "spectators": true,
    "spectatorFrames": false,
    "firstPlayer": true,
    "undoActions": [],
    "drawRequests": false,
    "replay": false,
    "ai": false
  },
  "records": {
    "scoreKind": "outcome"
  },
  "defaultOptions": {
    "listed": true,
    "allowGuests": true,
    "allowSpectators": true,
    "firstPlayer": "random"
  },
  "ruleLabels": ["一局约 15 分钟", "公开房间"]
}
```

基础字段：

- `apiVersion`：宿主插件接口版本，当前必须为 `1`；它不是游戏版本。
- `version`：当前插件自己的语义化版本，例如 `1.3.0`。
- `author`、`license`：代码责任人和许可证；私有代码可以使用 `UNLICENSED`。
- `id`：以 `plugin-` 开头，最长 32 位，只能包含小写字母、数字和连字符。发布后不要修改，否则历史房间和战绩会失联。
- `name`：必须与后端引擎的 `name` 一致。
- `players.min/max`：必须与后端引擎人数一致，范围为 1–20。
- `tone`：稳定的视觉标识，不能用它覆盖主项目全局样式。
- `roomLayout`：可选，支持 `standard`、`wide`、`immersive`。
- `defaultOptions`：创建房间时使用的初始规则，必须可 JSON 序列化。
- `ruleLabels`：房间顶部展示的规则摘要，最多 6 条。

平台能力：

| 字段 | 作用 |
| --- | --- |
| `guests` | 多人房是否允许配置游客准入 |
| `spectators` | 是否支持固定玩家视角的只读观战 |
| `spectatorFrames` | 是否允许玩家客户端补充发布视觉帧；依赖 `spectators` |
| `firstPlayer` | 是否显示随机先手/房主先手设置 |
| `undoActions` | 可被公共撤销系统记录的 action 名称 |
| `drawRequests` | 是否启用公共和棋申请 |
| `replay` | 是否声明回放能力 |
| `ai` | 是否实现宿主要求的 AI 动作方法 |

`records.scoreKind` 决定公共战绩和排行榜如何解释 `player_score()`：

- `outcome`：按胜、负、和统计；普通多人游戏使用它。
- `time_trial`：数值越小越好，单位由当前平台约定为毫秒；例如金字塔纸牌。
- `high_score`：数值越大越好；例如积分挑战。

声明 `time_trial` 或 `high_score` 时，引擎必须实现：

```python
def player_score(self, room, player) -> int | None:
    return room.state.elapsed_ms
```

## 5. 后端入口与公开 SDK

插件只能从稳定模块 `backend.app.games.plugin_api` 导入宿主类型：

```python
from backend.app.games.plugin_api import (
    ArcadePlayer,
    ArcadeRoom,
    GameRuleError,
)
```

不要直接依赖 `backend.app.arcade.*`、账号存储、实时服务或主项目某款游戏的内部模块。

`backend/plugin.py` 只负责导出无参数工厂：

```python
from .engine import YourGameEngine


def create_engine() -> YourGameEngine:
    return YourGameEngine()
```

引擎必须提供：

| 方法/字段 | 责任 |
| --- | --- |
| `key/name/min_players/max_players` | 与 manifest 保持一致 |
| `initial_state()` | 返回一局尚未开始的初始状态 |
| `start(room)` | 根据玩家和规则开局，并设置房间阶段 |
| `act(room, player, action, payload)` | 校验并执行玩家动作 |
| `view(room, viewer)` | 只返回当前观看者有权看到的数据 |
| `player_result(room, player)` | 返回 `(角色, 阵营, 是否获胜)` |
| `player_score(...)` | 计时/高分游戏提供公共成绩，可选 |
| `record_state(room)` | 自定义写入战绩的可序列化状态，可选 |
| `request_voter_ids(...)` | 自定义公共申请的投票人，可选 |

非法动作必须抛 `GameRuleError`。胜负、分数、牌 ID 和坐标都必须在服务端验证，不能信任客户端传入结果。

结束一局时使用公共房间结算：

```python
room.finish(
    "red",
    [winner.id],
    "红方率先完成目标",
)
```

`view()` 是隐藏信息的安全边界。真实状态可以保存全部手牌和身份，但返回给玩家或观众的字典只能包含该视角当时应该看到的内容。

## 6. 前端入口与公开 SDK

`frontend/GameView.vue` 必须接收 `snapshot`：

```vue
<script setup lang="ts">
import {
  usePluginGameActions,
  type ArcadeSnapshot,
} from '@game-hall/plugin-sdk'

defineProps<{ snapshot: ArcadeSnapshot }>()
const actions = usePluginGameActions()

async function move(from: number, to: number) {
  await actions.action('move', { from, to })
}
</script>
```

公开动作能力：

```ts
await actions.action('play_card', { cardId: 'heart-7' })
actions.rapidAction('aim', { x: 0.42, y: 0.68 })
await actions.restart()
actions.publishSpectatorFrame(sequence, { board, effects })
```

- `action`：普通可靠动作，并等待服务端确认。
- `rapidAction`：高频交互，可丢弃中间响应。
- `restart`：使用公共再来一局流程。
- `publishSpectatorFrame`：只有 manifest 声明 `spectatorFrames: true` 才会被服务端接收；帧只用于补充观战视觉，不能作为权威游戏状态。

公开界面组件：

| 分类 | 导出 | 用途 |
| --- | --- | --- |
| 操作 | `PluginButton`、`PluginIconButton` | 主题自适应的普通按钮和带无障碍标签的图标按钮 |
| 游戏展示 | `PluginPlayingCard`、`PluginRevealCard` | 标准扑克牌与按住显示的私密信息卡 |
| 数据与结算 | `PluginMetricGrid`、`PluginResultCard` | 指标网格与单人游戏通用结算 |
| 规则 | `PluginRuleGuide` | 快速开始、流程、完整规则和背景说明 |
| 弹窗 | `PluginModal`、`PluginConfirmDialog` | 通用弹窗与确认/危险确认流程 |
| 表单 | `PluginTextField`、`PluginNumberField`、`PluginSelect` | 带标签、说明、错误和无障碍关联的输入控件 |
| 状态 | `PluginStatePanel`、`PluginLoadingState`、`PluginEmptyState`、`PluginErrorState` | 自定义或预设的加载、空内容和失败反馈 |

这些组件是插件 API v1 的稳定公共包装，不暴露主项目内部文件路径。按钮支持原生 `disabled`、`type`、`aria-*` 和点击事件；组件的 Props 类型也从同一个 SDK 导出。

开发新游戏时，应先检查 `@game-hall/plugin-sdk` 已开放的组件、组合式函数、格式化工具和宿主能力，并优先复用它们。只有游戏特有的规则或表现确实无法由公共 SDK 表达时，才在插件目录内自行实现；如果一项能力会被多款游戏使用，应先将其抽象为稳定、通用的 SDK 能力，而不是复制实现或导入主项目内部路径。

```vue
<script setup lang="ts">
import { ref } from 'vue'
import {
  PluginButton,
  PluginNumberField,
  PluginResultCard,
  usePluginGameActions,
  type ArcadeSnapshot,
} from '@game-hall/plugin-sdk'

defineProps<{ snapshot: ArcadeSnapshot }>()
const actions = usePluginGameActions()
const guess = ref<number | null>(null)
</script>

<template>
  <PluginNumberField
    v-model="guess"
    label="你的猜测"
    description="只能填写 1 到 20"
    :min="1"
    :max="20"
  />
  <PluginButton block variant="primary" :disabled="!snapshot.actions.canAct">
    确认行动
  </PluginButton>
  <PluginResultCard
    v-if="snapshot.phase === 'finished'"
    eyebrow="挑战完成"
    title="本局已结算"
    :can-restart="snapshot.actions.canRestart"
    @restart="actions.restart()"
  />
</template>
```

公共宿主能力：

| 导出 | 用途 |
| --- | --- |
| `formatPluginDuration` | 统一显示计时器或可读时长，默认保留一位小数且不会把进行中的时间向前舍入 |
| `formatPluginScore` | 统一数字精度、千分位和分数单位 |
| `usePluginFullscreen` | 让插件自己的根元素进入/退出全屏，并自动同步浏览器状态和清理监听器 |
| `usePluginTheme` | 只读获取当前主题和该主题的材质值 |
| `pluginThemeMaterials` | 按主题名读取不可变的场景、舞台、金属、文字和语义色材质 |

```vue
<script setup lang="ts">
import { ref } from 'vue'
import {
  formatPluginDuration,
  usePluginFullscreen,
  usePluginTheme,
} from '@game-hall/plugin-sdk'

const gameRoot = ref<HTMLElement | null>(null)
const { isFullscreen, isSupported, toggle } = usePluginFullscreen(gameRoot)
const { theme, materials } = usePluginTheme()
</script>

<template>
  <section ref="gameRoot">
    <span>{{ formatPluginDuration(65_200) }}</span>
    <button v-if="isSupported" @click="toggle">
      {{ isFullscreen ? '退出全屏' : '进入全屏' }}
    </button>
    <small>{{ theme }} · {{ materials.stage.edge }}</small>
  </section>
</template>
```

`usePluginTheme()` 不允许插件修改用户主题，`usePluginFullscreen()` 也只操作传入的插件元素。需要大厅导航、账号、聊天、房间管理或其他宿主业务时，不要导入内部模块，应先判断是否能抽象成更小的公共能力。

常用快照数据：

- `snapshot.self`：当前玩家；观战时是固定的目标玩家视角。
- `snapshot.players`：房间玩家。
- `snapshot.phase`：当前生命周期阶段。
- `snapshot.actions.canAct`：公共平台判定当前视角是否能行动。
- `snapshot.game`：后端 `view()` 返回的视角数据。
- `snapshot.viewer?.mode === 'spectator'`：当前是否为只读观众。

插件不能自行连接 Socket、读取令牌/Cookie/localStorage，或修改宿主 DOM。样式使用 `<style scoped>`，根元素应设置 `min-width: 0; max-width: 100%`，并检查 320、375、390、768、1024、1440 像素宽度。`dev`、`test` 和 `build` 会扫描插件前端导入：生产代码只能使用相对插件内部路径、Vue、`@lucide/vue` 和 `@game-hall/plugin-sdk`；测试可额外使用 Vitest、Vue Test Utils 和 Pinia。越过插件目录导入主项目内部文件会直接失败。

## 7. 发布注册表

开发完成并通过审核后，由维护者在根 `registry.json` 添加：

```json
{
  "id": "plugin-your-game",
  "path": "plugin-your-game",
  "status": "enabled",
  "order": 130
}
```

根注册表与插件 manifest 当前都使用 `apiVersion: 1`，但它们是两套独立版本号：前者表示“发布清单格式第 1 版”，改变发布清单结构时才升级；后者表示“宿主能力契约第 1 版”，改变宿主提供给游戏的接口时才升级。

- `id` 必须与 manifest 一致。
- `path` 是相对社区仓库根目录的安全路径。
- `order` 在社区游戏中唯一，控制社区入口排序。
- `enabled`：正常发布并显示在目录中。
- `deprecated`：继续加载引擎和界面以兼容已有房间，但不出现在新游戏目录中。
- `disabled`：完全不进入构建和后端加载。

不要同时使用 manifest 开关和根注册表；API v1 只有 `registry.json` 是发布状态的唯一事实来源。

下线插件时先改成 `deprecated`，等待未完成房间结束并确认不再需要恢复，再改成 `disabled` 或删除注册项。不要直接删除仍可能被恢复的游戏代码。

## 8. 测试与发布

以下命令在游戏大厅主仓库根目录执行：

```bash
# 当前正式插件的发布校验
.venv/bin/python -m backend.app.games.validate_plugins

# 当前插件后端测试
.venv/bin/python -m pytest game-hall-community-games/plugin-your-game/tests

# 当前插件前端测试
npm --prefix frontend run test:run -- ../game-hall-community-games/plugin-your-game/frontend

# 全部后端、示例、插件和前端测试
npm test

# 类型检查、插件前端生成与生产构建
npm run build
```

发布前至少验证：

- manifest 和 registry 校验通过。
- 建房、加入、游客限制、开局、主要动作、重连、结束、再来一局正常。
- 观众可见、视角固定、完全只读，隐藏信息符合产品规则。
- 战绩 `scoreKind` 与 `player_score()` 一致。
- 深浅大厅图标成对存在、尺寸正确、几何一致，并在 72 像素下仍可识别。
- 桌面端和手机端没有页面级横向滚动。
- 插件目录外没有意外业务改动。

提交与部署顺序：

1. 在本社区仓库提交并推送插件与 `registry.json`。
2. 服务器在主仓库运行 `python3 scripts/restart.py`，更新社区仓库 `origin/main` 最新提交。
3. 脚本先构建镜像，再严格校验全部已发布插件，成功后才替换当前服务。
4. 生产验证后，主仓库可以更新 Submodule 指针作为新的开发、CI、复现和回滚基线。

使用 `python3 scripts/restart.py --no-pull` 只会重建服务器当前已经检出的代码。构建或插件校验失败不会替换正在运行的应用容器。

## 9. 常见问题

### 插件没有出现在入口

检查插件是否已加入根 `registry.json`、状态是否为 `enabled`、三个必需入口和两张大厅图标是否存在，以及 `npm run build` 是否报告清单或图标错误。只存在 manifest 不会发布。

### 后端校验失败

常见原因是 `create_engine()` 缺失、引擎 key/name/人数与 manifest 不一致、能力字段不完整、Python 导入失败，或已发布插件缺少 README/前端入口。

### 已有游戏文件很多，必须合成一个文件吗

不需要。规则、状态、牌库、组件和资源都可以继续拆分；只有 `backend/plugin.py` 与 `frontend/GameView.vue` 的位置固定。

### 能直接复用主项目内部组件吗

不能直接导入主项目内部路径，但可以复用 `@game-hall/plugin-sdk` 正式开放的组件、类型和组合式函数。现在已经开放按钮、卡牌、指标、结算、规则、弹窗、表单、状态、全屏、主题和格式化能力。公共包装层保证插件不依赖宿主目录结构；多款插件都需要的新能力，应先升级公共 SDK。后端同理，只使用 Python 标准库、已审核依赖和 `backend.app.games.plugin_api`，不能导入宿主内部模块。

### 能保证任意主项目版本兼容任意插件版本吗

不能。兼容边界由 `apiVersion` 定义。宿主只加载自己支持的 API 版本，部署校验会阻止不兼容插件替换当前服务；Submodule 指针用于记录可复现的兼容基线。
