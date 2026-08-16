# Codex core-v2 独立验收版

这是一个与游戏大厅解耦的网页游戏原型。当前版本用于验收两张北京现代办公室地图、八名团队成员的实时场景、每日签到、好人卡和桌边工作；不读取大厅账号、Cookie、MySQL、Redis 或 Socket，也没有大厅插件 `manifest.json`。

当前视觉基线是 `beijing-modern-isometric-v2`：`32×16` 等距地块、48 色世界调色板、原生方向墙体、半透明玻璃和屏幕空间北京城市远景。`core-v2` 只在新建验收局中生效，旧资产 release、地图快照和 Run 始终冻结，不会被后台升级。

## 本地前台运行

只使用本目录现有 `.venv`，不向全局 Python 环境安装依赖：

```bash
cd third_party_games/demo
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m uvicorn codex_v0.main:app --host 127.0.0.1 --port 10700
```

命令必须保持在前台，且不要添加 `--reload`。按 `Ctrl+C` 可完整停止；开发助手不会替你启动或停止服务。随后打开：

- `http://127.0.0.1:10700/assets?pack=core-v2`：资产验收台。
- `http://127.0.0.1:10700/review`：单玩家导演台，只载入 Gus 的一个真实客户端；世界里仍有 Ava–Hana 八名成员。

手机真机检查时可由用户显式改成 `--host 0.0.0.0`，再使用局域网地址；不要把该开发服务直接暴露到互联网。

## core-v2 资产包

`assets/core-v2-pack.spec.json` 是包级契约。它在创建时冻结当时的 active `core-v1` release 为不可变 `base_release_id`，共 29 个必需槽位：

- 13 个 `inherited` 槽位：直接引用冻结基包里已经人工接受的 PNG/SHA，只读且不重复审核。
- 16 个 `editable` 槽位：9 个 override 加 7 个新槽位；经 pack-scoped scan 后以 `draft` 导入，必须逐项人工接受。
- 16 个 editable 未全部通过自动检查和人工验收前，整包不能激活；接受单项也不会自动激活。

9 个 override 是北京远景、raw concrete、utility border、两向实墙、两向窗墙、NE 门墙和转角柱。7 个新增槽位是两向单人工位、媒体台、原型工作台、低书柜、入口长凳和移动式灵感板。

五个 `geometryVersion: 2` 墙体使用原生 NW/NE 像素几何，不做运行时镜像或仿射补丁。manifest 必须同时满足：方向、整数 `groundAxis`、anchor 中点、固定轴跨度、整数 `wallFaceHeight: 56`，以及从 ground axis 上移 56px 后的两端仍位于原生 frame 内。窗墙的玻璃 pane 使用离散半透明 alpha，城市远景能从窗后透出。

`sceneShell` 是 manifest 中的可选几何合约；`core-v2` 将它固定为 `cutaway-office-tower` v1。塔楼前缘只取地图的 `x=max` 与 `y=max` 两条完整地块外缘，使用 512px 立面深度、8px 楼板、2px 环境遮蔽与 12px 窗带节距。它的固定顺序是“屏幕空间背景 → 世界空间立面 → 地面 → 摆件/角色”；立面不参与全图自动 fit，但会向下延伸到画布底部。旧 manifest 不包含 `sceneShell` 时仍走原有渲染路径。

幕墙的楼层线在世界空间是水平的，所以在 2:1 投影里必须与屋檐平行，而不是与屏幕平行。窗带因此按“距屋檐的深度”参数化，而非绝对屏幕 y：同一个深度在两个面上描述同一层楼板，转角处自动对齐，不需要相位修正。

### 塔楼几何的双实现契约

这套几何有两份实现：浏览器渲染器 `web/scene.mjs` 和离线验收渲染器 `codex_v0/asset_qa.py`。两边各自把几何构建成同一个结构（`checks/tower-shell-contract.mjs` 与 `codex_v0/tower_shell_contract.py`），并各自与已提交的 `checks/fixtures/tower-shell-geometry.json` 比对。两个测试套件都不需要对方的运行时，所以单边改动会在**另一种语言**里报错，而不是悄悄通过。fixture 覆盖全部 core-v2 地图，新增地图必须重新生成。

几何有意变更后重新生成 fixture：

```bash
.venv/bin/python -m codex_v0.tower_shell_contract regenerate
```

该命令同时构建两份实现，不一致时拒绝写入并打印第一处差异路径；`check` 子命令只比对不写入。

已知且既有的差异：浏览器在相机变换**之前**对多边形顶点 `snap()`，Pillow 在变换**之后** `round()`。只要局部场景空间保持整数点阵，两者最多相差半个设备像素，整数 zoom 下完全一致；这对整个 shell 成立（立面、楼板、环境遮蔽与窗带一样）。契约构建器因此拒绝记录非整数局部坐标 —— 那会突破这个上界。

### 候选闭环

生成源图不直接成为游戏资产。每一项先按目标 frame 做原生规范化，并保存准备报告：

```bash
.venv/bin/python -m codex_v0.asset_normalize prepare \
  --pack core-v2 \
  --report data/assets/prepared/core-v2/<name>-prepare.json \
  <slot> <source.png> data/assets/prepared/core-v2/<name>-native.png
```

再用同一份准备报告生成最终 PNG 与 AssetLab sidecar：

```bash
.venv/bin/python -m codex_v0.asset_normalize slot \
  --pack core-v2 \
  --preparation-report data/assets/prepared/core-v2/<name>-prepare.json \
  --sidecar data/assets/inbox/core-v2-<name>.json \
  <slot> data/assets/prepared/core-v2/<name>-native.png \
  data/assets/inbox/core-v2-<name>.png
```

完整生命周期是：

1. 生成请求与源图进入被 Git 忽略的 `data/assets/sources/core-v2/`。
2. `prepare` 处理去背、原生 frame、方向与几何；`slot` 执行固定调色板、透明度、尺寸和 provenance 校验。
3. 在 `/assets?pack=core-v2` 点击“扫描本地收件箱”。页面只调用 `POST /api/assets/inbox/scan?packId=core-v2`，因此不会误扫其他包；扫描仅导入为 `draft`。
4. 人工查看原生尺寸、contact sheet、完整场景和遮挡效果，逐项接受或拒绝 16 个 editable 版本。
5. 16 项全部 accepted 且自动门禁通过后，由用户在确认框中手动激活 `core-v2`。

这里的“生成”“规范化”“draft 导入”“场景验收”“accepted”和“active release”是六个不同状态。任何命令、扫描或单项接受都不会跨过后续状态，也绝不自动激活资产包。

### 离线场景 QA

不用启动服务、导入数据库、审核或激活，就能以冻结的 13 个 inherited 版本加收件箱中的 16 个候选生成确定性验收图：

```bash
.venv/bin/python -m codex_v0.asset_qa render
```

输出写入被 Git 忽略的 `data/assets/derived/core-v2/`：

- `core-v2-candidates-contact.png`
- `world-opening-empty-v2-candidate.png`
- `world-mid-growth-v3-candidate.png`
- `desk-work-occlusion-candidate.png`
- `qa-manifest.json`（输入、输出与 SHA-256 清单，以及角色可见度、立面前缘/深度/窗带几何和画布覆盖指标）

QA renderer 只读 AssetLab 数据库，并优先从收件箱解析尚未导入的 core-v2 候选；它不会修改评审、包成员或 active release。离线立面使用和浏览器相同的中心相机变换，`qa-manifest.json` 会逐场记录 `x=max/y=max` 前缘、两面的屏幕 bounds、立面像素数、画布覆盖率和底行覆盖像素，用来阻止“概念图正确、实际几何反向或没有延伸到底部”的回归。

## 两张 core-v2 地图

建局必须明确选择地图；两张新地图均要求用户已经手动激活 `core-v2`：

| 地图 | 尺寸 | 定位 | 初始状态 |
|---|---:|---|---|
| `world.opening-empty-v2` | `14×9` | 光秃开局办公室 | 稀疏家具；Ava、Ben、Cleo 已在三个桌岛座位工作，第四座留给 Gus |
| `world.mid-growth-v3` | `20×12` | 丰富中期办公室 | 21 个功能簇；七名非 Gus 成员占用七个工作座位，Gus 位于场地中央，共享桌 NE 座位空闲 |

地图 JSON 是仓库内不可变内容。每个 Run 会冻结实际尺寸、origin、地面分区、placement、出生点、初始 activity、碰撞、layout SHA、资产 release 及 manifest/atlas SHA。服务重建时只有仍位于冻结座位的角色才恢复工作状态，不会把已经移动的角色强拉回座位。

旧 `core-v1` release、`world.opening-empty-v1`、`world.mid-growth-v2` 与既有 Run 继续保留原画面和兼容渲染；激活 core-v2 后，新建局使用 v2/v3 地图，既有局不升级、不重绑、不失效。

## 场景、镜头与交互

- 北京远景固定在屏幕空间，不随相机平移和缩放漂移；完整地图自动 fit 时排除 backdrop，并计算地面、结构、家具、人物与姓名的视觉边界。
- 缩放档位是 `1× / 1.25× / 1.5× / 2×`；桌面使用 `640×360` 逻辑画布，手机 contain 完整地图。
- 八名成员都以不透明角色绘制；姓名使用状态点、白字和深色描边，并执行边缘 clamp 与标签避让。
- 玩家页使用近黑硬边 HUD、左侧美元余额、右侧签到与好人卡按钮；整数美元隐藏 `.00`，有分币时显示两位小数。
- 点击或触摸空地由服务端 A* 权威寻路；拖动平移，滚轮、双指或按钮缩放。
- 点击共享桌或 focus desk 的座位会先预留座位，再自动寻路并进入相应方向的循环 `work`。再次点击、普通移动、断线或 reset 会释放座位；工作不增加美元。
- 每日签到由服务端先决定一次等概率转盘结果；好人卡每天一张，只能发送给其他成员，不增加美元。

导演台只创建一个 Gus iframe/WS，保留桌面、手机与窄屏视口、暂停、倍速、重放、模拟延迟、相机预设和调试 overlay。Gus 客户端会把 pack、release、manifest、atlas、layout SHA 与 Run 冻结绑定逐项核对；失败时阻断显示，不静默退回旧画面。

## 数据、安全与接口

- SQLite、图片 blob、评审记录、图集和 QA 输出只写入被 Git 忽略的 `data/`。
- Run、资产包和资产实验室数据彼此隔离；run reset 或推进业务日期不会改动资产数据。
- 玩家 token 只经 URL fragment 首次交给页面，读取后从地址栏移除；数据库仅保存哈希。
- 资产写接口只接受 loopback 同源请求，并要求页面启动时获得、仅保存在内存里的 CSRF token。
- 美元是不可充值、提现或兑换现实权益的纯游戏币；不保存位置历史。

主要玩家接口：

- `GET /api/bootstrap`
- `POST /api/checkin/spin`
- `POST /api/good-cards`
- `WS /ws/{run_id}`

签到和好人卡写请求要求 `Idempotency-Key`。WebSocket 首条消息必须携带玩家 token；客户端只能提交移动目标、`work.start` 或 `work.stop`，不能提交最终坐标或玩家身份。

主要验收接口：

- `GET /api/review/layouts`
- `POST /api/review/runs`（`layoutId` 必填）
- `GET /api/assets/bootstrap`
- `GET /api/assets/catalog?packId=core-v2`
- `POST /api/assets/inbox/scan?packId=core-v2`
- `POST /api/assets/{asset}/versions/{version}/review`
- `POST /api/assets/reviews/batch`
- `POST /api/assets/packs/{pack}/activate`
- `GET /api/assets/active/manifest`
- `GET /api/assets/manifests/{manifest_sha256}`
- `GET /api/assets/blobs/{sha256}`

## 检查

所有检查都使用现有 `.venv`，不需要启动服务：

```bash
.venv/bin/python -m pytest -q
node --test checks/*.test.mjs web/*.test.mjs
node --check web/asset-manifest.mjs
```

自动检查不能代替视觉验收。接受前仍需按原生尺寸确认每个 frame、anchor、角色身份、玻璃 alpha、墙体方向、完整 opening/mid 场景和桌边遮挡。

当前不包含大厅登录、正式经济数据联动、商店、升级、排行榜、聊天、随机事件、战斗、像素编辑器、地图编辑器或云端资产协作。
