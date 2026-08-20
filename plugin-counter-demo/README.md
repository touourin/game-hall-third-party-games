# 计数竞速插件示例

这是一个完整但未发布的示例插件，用于复制并开始社区游戏开发。

开发完成并通过审核后，由维护者把插件加入仓库根部的 `registry.json`，它才会进入大厅。两名玩家轮流点击，率先达到 10 分者获胜。

前端入口同时演示了 `@game-hall/plugin-sdk` 的动作接口、`PluginButton` 操作按钮和 `PluginMetricGrid` 指标网格。新增游戏应优先复用 SDK 已开放的组件、组合式函数和工具；公共能力不足时应先评估扩展 SDK，不能通过相对路径导入主项目内部文件。

复制本目录开发新游戏时，请同时修改目录名、manifest 的 `id`、后端引擎的 `key`，并在 `frontend/assets/` 新建符合根 README 规范的 `catalog-dark.webp` 与 `catalog-light.webp`。两张图标必须是同一构图的深浅材质版本，发布前会由主项目自动发现和校验，不需要修改 manifest 或主仓库注册表。
