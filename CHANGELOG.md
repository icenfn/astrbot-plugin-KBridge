# Changelog

本项目的所有显著变更按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 记录，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。版本与 `metadata.yaml` 保持一致。

## [Unreleased]

### Fixed

- 同步页点击同步后进度不实时更新（需手动刷新）：手动单源同步时 `is_syncing` 恒为 False 导致轮询首次即被终止，改为以 `stats.current` 判断同步是否结束，进度实时刷新
- 「已同步 x」计数逻辑修正：改为该同步源**当前已入库文档数**（增量索引长度），重复同步不再累加、知识库重建后自动归位、取消后为已入库部分
- GitHub tree 子目录同步（如 `/tree/master/packages/docs/src/pages/en`）全部失败：raw 下载 URL 缺少子目录前缀（`fetch_raw` 未拼接 `parsed.path`），已修复；失败条目不再静默，日志输出前 5 条明细
- 删除 AstrBot 知识库后再同步出现「跳过」导致空知识库：目标知识库重建（`resolve_target_kb` 返回 recreated）时清空该同步源增量索引，全量重同步
- 有道云笔记同步偶发 `read operation timed out`：POST/响应等待超时 30s→120s，失败重试间隔增大

### Changed

- Raw 加速镜像默认值改为 `https://gh.dpik.top/`（未配置时自动使用，可在 Github 平台配置中修改或留空回退官方源）
- 同步列表标题单行显示，超长省略号（`...`）截断
- 平台名「GitHub Repository」→「Github」
- GitHub Repository 平台配置 Token 改为**必填**：未配置时添加仓库返回 400 提示；前端保存前校验必填
- 定时同步页面重排：间隔输入分组卡片化；同步日志改为**控制台形态**——终端深底、等宽字体、`[OK]/[WARN]/[FAIL]` 级别标签 + 消息字符串着色高亮；超长日志横向滚动查看；新增「清除日志」按钮
- 删除无用日志（同步页列表数量、拉取同步源等 info 噪音）
- 项目整理：删除未引用的 `logo-mark.png`，补全 `.gitignore`（保留 `logo.png` 作为插件图标）

### Added

- 同步进度与取消：同步中对应卡片按钮显示「取消 x/y」进度，点击取消后已入库部分保留（增量索引已保存，不会重复入库）；其他同步源同步中按钮置灰
- 定时同步开关移至同步按钮同一行（卡片操作区）

### Changed

- 同步管理/定时同步页面信息密度优化：卡片紧凑化、元信息合并一行、日志单行化（时间+色点+消息）并新增成功/警告/失败统计头
- 移除卡片内「目标库已删除」提示行（保留「未同步」徽章）

### Fixed

- GitHub 同步：tree 子目录 URL（`/tree/branch/path`）同步报「不支持的 GitHub 路径」——同步源键直接还原解析，不再经 URL 重建；`default` 占位分支正确查询仓库默认分支
- GitHub 同步：仓库/分支不存在或已删除（404）时不再向上抛异常崩溃，改为捕获并标记同步源为异常状态
- GitHub 平台配置页添加/删除仓库后页面卡住——改为局部刷新仓库列表，不再关闭并重开整个弹窗
- 非法 GitHub URL 在添加时返回 400 友好提示（不再 500）
- GitHub 订阅记录 `target_kb` 缺失 → 添加时写入真实仓库名，知识库删除检测（未同步标记）准确

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
