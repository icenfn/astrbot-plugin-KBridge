# Changelog

本项目的所有显著变更按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 记录，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。版本与 `metadata.yaml` 保持一致。

## [Unreleased]

### Fixed

- GitHub 同步 400 条左右批量超时（30-seconds-of-code）：`asyncio.TimeoutError`（3.11+ 不属于 `aiohttp.ClientError`）原先不在重试捕获范围，镜像 404 回退官方 raw 后一旦超时直接失败且无重试；已统一捕获超时/连接错误并按 1s/2s/4s 指数退避重试，官方回退同样重试 2 次
- 同步进行中徽标误显示上次状态（如「已取消」）：同步中的卡片徽标改为实时显示「同步中 x/y」，与取消按钮进度一致
- 同步大型仓库（如 30-seconds-of-code，3300+ 文件）导致服务器卡死/OOM：根因是 GitHub 同步的 `upload_document` 在并发信号量**外**执行，全部文件同时切片 + embedding 向量化入库；已改为**全局入库串行锁**（所有同步源共享 `Semaphore(1)`，下载仍可并发、仅入库严格串行），并将文件处理分块（每批 60 个 task）避免一次性挂起上千协程
- 有道云/ima 入库同样纳入全局串行锁，多知识库同时同步不再叠加内存压力
- 同步页点击同步后进度不实时更新（需手动刷新）：手动单源同步时 `is_syncing` 恒为 False 导致轮询首次即被终止，改为以 `stats.current` 判断同步是否结束，进度实时刷新
- 「已同步 x」计数逻辑修正：改为该同步源**当前已入库文档数**（增量索引长度），重复同步不再累加、知识库重建后自动归位、取消后为已入库部分
- GitHub tree 子目录同步（如 `/tree/master/packages/docs/src/pages/en`）全部失败：raw 下载 URL 缺少子目录前缀（`fetch_raw` 未拼接 `parsed.path`），已修复；失败条目不再静默，日志输出前 5 条明细
- 删除 AstrBot 知识库后再同步出现「跳过」导致空知识库：目标知识库重建（`resolve_target_kb` 返回 recreated）时清空该同步源增量索引，全量重同步
- 有道云笔记同步偶发 `read operation timed out`：POST/响应等待超时 30s→120s，失败重试间隔增大
- 同步列表状态标签被长标题顶出列表外：`.sub-list` 卡片默认 `min-width:auto` 被内容撑爆，已加 `min-width:0` 收缩，状态标签 `flex:none` 固定在元信息行首

### Added

- 新增 **url2kb** 平台：把网页转为 Markdown 存入 AstrBot 知识库。支持创建多个**分组**（分组名即 AstrBot 知识库名，可带备注）与多条 URL（添加时自动识别网页 `<title>`）；分组自动注册为同步源，支持手动/定时同步与增量去重
- 总览页新增**定时同步**卡片（启用状态 + 间隔文本）与「定时设置」入口按钮

### Added

- 同步列表新增「**删除本地**」按钮：删除该同步源已同步到 AstrBot 的本地知识库数据（订阅保留），知识库被删除、列表「已同步 N」归零、增量索引清空，下次同步全量重建；重复点击幂等

### Changed

- GitHub 同步列表标题改为显示**目标本地 AstrBot 知识库名**（repo 名，如 `30-seconds-of-code`），不再显示完整仓库路径（`owner/repo : path`）
- 删除操作增加**二次确认弹窗**：同步列表「删除本地」与 Github 配置弹窗「删除仓库」均需确认（自定义样式化弹窗，非原生 confirm）
- 同步列表状态兜底：删除本地后状态显示「未同步」（原为空文本）


- **重构 url2kb 配置弹窗**：顶部独立「添加分组」卡片（分组名 + 备注 + 添加按钮一行式）；分组改为**卡片网格**（宽屏两列、窄屏单列），每张分组卡片头部为「名称 + 备注 + 保存/删除」紧凑一行，URL 行 title/url/删除单行排布，信息不再挤成一团；弹窗加宽 560px → 720px
- 添加 URL 不再卡页面：标题识别改为**只读响应前 128KB**（`<title>` 位于 `<head>`）、超时 20s → 10s，慢/被墙站点快速回退不阻塞；按钮添加时显示「识别中…」并禁用，防重复点击


- 修复平台配置弹窗无法打开的问题：`u2Block` 模板此前只被引用从未定义（`ReferenceError`），且 `openPlatformConfig` 为 async 函数，同步 `try/catch` 捕获不到其抛错导致完全静默；已补全定义并将错误捕获改为 Promise `.catch`（出错会 toast 提示）
- 同步列表按钮比例：switch 定时开关 + 同步按钮 + 删除本地（次要 danger 小按钮）布局协调，窄屏时按钮换行不挤压

### Fixed


- 同步列表「定时」改为「定时同步」，复选框升级为 switch 开关（固定 34×18 尺寸，不撑乱卡片布局）
- Raw 加速镜像默认值改为 `https://gh.dpik.top/`（未配置时自动使用，可在 Github 平台配置中修改或留空回退官方源）
- 同步列表标题单行显示，超长省略号（`...`）截断
- 平台名「GitHub Repository」→「Github」
- GitHub Repository 平台配置 Token 改为**必填**：未配置时添加仓库返回 400 提示；前端保存前校验必填
- 定时同步页面重排：间隔输入分组卡片化；同步日志改为**控制台形态**——终端深底、等宽字体、`[OK]/[WARN]/[FAIL]` 级别标签 + 消息字符串着色高亮；超长日志横向滚动查看；新增「清除日志」按钮
- 删除无用日志（同步页列表数量、拉取同步源等 info 噪音）
- 项目整理：删除未引用的 `logo-mark.png`，补全 `.gitignore`（保留 `logo.png` 作为插件图标）

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
