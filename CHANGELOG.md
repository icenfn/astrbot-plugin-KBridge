# Changelog

本项目的所有显著变更按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 记录，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。版本与 `metadata.yaml` 保持一致。

## [Unreleased]

### Fixed

- GitHub 同步 400 条左右批量超时（30-seconds-of-code）：`asyncio.TimeoutError`（3.11+ 不属于 `aiohttp.ClientError`）原先不在重试捕获范围，镜像 404 回退官方 raw 后一旦超时直接失败且无重试；已统一捕获超时/连接错误并按 1s/2s/4s 指数退避重试，官方回退同样重试 2 次
- 同步进行中徽标误显示上次状态（如「已取消」）：同步中的卡片徽标改为实时显示「同步中 x/y」，与取消按钮进度一致

### Changed

- 总览页左上角新增 KBridge Logo（base64 内联，不依赖插件静态资源路由）
### Fixed

- 同步大型仓库（如 30-seconds-of-code，3300+ 文件）导致服务器卡死/OOM：根因是 GitHub 同步的 `upload_document` 在并发信号量**外**执行，全部文件同时切片 + embedding 向量化入库；已改为**全局入库串行锁**（所有同步源共享 `Semaphore(1)`，下载仍可并发、仅入库严格串行），并将文件处理分块（每批 60 个 task）避免一次性挂起上千协程
- 有道云/ima 入库同样纳入全局串行锁，多知识库同时同步不再叠加内存压力
- 同步页点击同步后进度不实时更新（需手动刷新）：手动单源同步时 `is_syncing` 恒为 False 导致轮询首次即被终止，改为以 `stats.current` 判断同步是否结束，进度实时刷新
- 「已同步 x」计数逻辑修正：改为该同步源**当前已入库文档数**（增量索引长度），重复同步不再累加、知识库重建后自动归位、取消后为已入库部分
- GitHub tree 子目录同步（如 `/tree/master/packages/docs/src/pages/en`）全部失败：raw 下载 URL 缺少子目录前缀（`fetch_raw` 未拼接 `parsed.path`），已修复；失败条目不再静默，日志输出前 5 条明细
- 删除 AstrBot 知识库后再同步出现「跳过」导致空知识库：目标知识库重建（`resolve_target_kb` 返回 recreated）时清空该同步源增量索引，全量重同步
- 有道云笔记同步偶发 `read operation timed out`：POST/响应等待超时 30s→120s，失败重试间隔增大
- 同步列表状态标签被长标题顶出列表外：`.sub-list` 卡片默认 `min-width:auto` 被内容撑爆，已加 `min-width:0` 收缩，状态标签 `flex:none` 固定在元信息行首

### Changed

- 新增**平台启用/禁用**：平台配置页每个平台卡片增加开关，禁用后同步列表该平台分组标记「已禁用」、卡片按钮与定时开关禁用；总览「已配置 x/n」不计入禁用平台；手动同步/定时同步/同步全部自动跳过已禁用平台（接口返回 400 提示）
- Raw 加速镜像默认值改为 `https://gh.dpik.top/`（未配置时自动使用，可在 Github 平台配置中修改或留空回退官方源）
- 同步列表标题单行显示，超长省略号（`...`）截断
- 平台名「GitHub Repository」→「Github」
- GitHub Repository 平台配置 Token 改为**必填**：未配置时添加仓库返回 400 提示；前端保存前校验必填
- 定时同步页面重排：间隔输入分组卡片化；同步日志改为**控制台形态**——终端深底、等宽字体、`[OK]/[WARN]/[FAIL]` 级别标签 + 消息字符串着色高亮；超长日志横向滚动查看；新增「清除日志」按钮
- 删除无用日志（同步页列表数量、拉取同步源等 info 噪音）
- 项目整理：删除未引用的 `logo-mark.png`，补全 `.gitignore`（保留 `logo.png` 作为插件图标）

### Added

## [v0.1.0] - 2026-10-03

### Added

- 腾讯 ima 知识库同步：页面直接展示自建库、知识库内文件夹递归、笔记文本读取（`get_doc_content`）、增量去重
- 有道云笔记同步：官方 MCP（SSE）协议、单库全量同步、目标库名可配置（`youdao_target_kb`，默认 `YoudaoNote`）
- GitHub 仓库同步：支持整个仓库（默认分支）与 tree 子目录 URL、AstrBot 支持格式白名单、自动忽略 `node_modules/dist/build` 等目录、按文件相对路径增量去重
- 自动建库：目标 AstrBot 知识库不存在时自动创建，并自动绑定已配置的 Embedding 与 Rerank（重排序）模型
- WebUI 管理页面：总览 / 平台配置 / 同步管理 / 定时同步
- 定时同步：间隔按天/时/分/秒设置，仅同步已开启「定时」开关的同步源，环形日志（上限 100 条）高亮分级展示（成功/警告/失败）
- `/kbridge` 命令组：帮助 / 列出可同步知识库 / 同步源列表 / 删除同步源 / 手动同步

### Changed

- 平台化配置重构：移除 Obsidian、印象笔记、url2kb、Open Notebook 平台支持
- GitHub URL 管理迁入平台配置页（弹窗内添加/删除仓库）
- 同步卡片状态：目标 AstrBot 知识库被删除时显示「未同步」，同步时自动重建
- 有道云同步源名称取自 `youdao_target_kb` 配置，不再固定显示「全部笔记」与「目标库」
- 删除同步源改为按 `kb_id + platform`（避免列表顺序错位）

### Fixed

- 首次安装加载失败（`cannot import name 'filter'`、`No module named 'ima_client'`）
- 同步显示「已同步 1」但 AstrBot 知识库为空（跳过分支静默返回）
- `Context.get_kv_data` 不存在（改为 Star 基类 KV 读写）
- 订阅页 select 显示 `undefined`
- 平台配置弹窗缺少 ima Key 输入入口
- 重复拉取报错 → 提示已存在
- 同步源列表无法删除
- 自动建库时 Embedding Provider 取错（按 `provider_config.get("id")` 并经 ProviderManager 校验）
- IMA 订阅库读取 403 / 220030（无权限）→ 页面过滤并在同步时跳过
- IMA 高频频控（200001 / HTTP 403）→ 低频串行 + 自动退避重试
- 有道云 SSE 连接在代理环境下超时 → 标准库 `http.client` 线程实现
- GitHub 匿名限流（403）→ 提示填写 Token

### Removed

- 同步前缀、同步二次确认弹窗、同步日志面板、手动拉取（subs/add）等冗余交互
- 「自动创建目标知识库」开关（始终自动创建）
