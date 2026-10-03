# Changelog

本项目的所有显著变更按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 记录，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。版本与 `metadata.yaml` 保持一致。

## [1.1.2] - 2026-10-04

### Changed

- 回滚同步列表操作区布局至 1:1:1 等宽（撤销 1:2:1 与删除按钮常显改动），保留删除按钮隐藏逻辑与 toast 原样

## [1.1.1] - 2026-10-03

### Removed

- 删除 `/kbridge` 指令组及相关代码（全部操作已由 WebUI 覆盖）
- 删除平台配置页引导文案（「配置各知识源平台凭据，同步即基于此连接」「点击进入配置」「点击管理网页分组」）

### Changed

- 同步中状态徽标不再显示 `x/y` 进度，统一显示「同步中」；取消按钮保留 `x/y` 进度
- Open Notebook 平台配置不再预填默认地址，字段留空 + hint 提示（留空使用默认 `http://localhost:5055`）

## [1.1.0] - 2026-10-03

### Added

- **Open Notebook（beta）**：自托管 RAG 服务（默认 `http://localhost:5055`）接入，页面自动展示 notebooks 并创建同步源；同步 notebook 的 sources/notes 内容入库（目标知识库名 = notebook 名），sources 端点缺失时自动回退 notes（兼容版本差异）
- **Memos（beta）**：自建 [usememos.com](https://usememos.com/) 实例接入，单库同步源，分页拉取全部备忘录（Markdown 内容）入库，目标知识库名可配置（默认 `Memos`）
- 平台配置新增 `open_notebook_url` / `open_notebook_password` / `memos_url` / `memos_token` / `memos_target_kb` 字段；两个平台均支持启用/禁用、增量去重、定时同步

### Fixed

- 平台禁用列表补齐 Open Notebook / Memos（默认启用）

## [1.0.0] - 2026-10-03

### Added

- **四大知识源同步**：腾讯 ima（自建库直列、文件夹递归、笔记文本读取）、有道云笔记（MCP 单库，目标库名可配置）、GitHub 仓库（全仓库 / tree 子目录，Raw 加速镜像）、url2kb（网页转 Markdown，多分组 + 自动识别标题）
- **同步源管理**：添加/删除订阅、「删除本地」清空知识库数据（订阅保留）、增量去重与全量重建、启用/禁用平台
- **定时同步**：间隔可配置（天/时/分/秒）、同步源定时开关、控制台式自动同步日志（级别高亮、横向滚动、一键清除）
- **WebUI 管理**：总览统计与定时卡片、同步页（「同步全部」、进度实时刷新、取消同步）、平台配置全屏弹窗、url2kb 分组管理
- **状态体系**：已同步数按增量索引口径实时计算、目标知识库删除检测（未同步）、同步中「x/y」徽标、删除本地后自动归位

### Fixed

- 大型仓库（3300+ 文件）同步 OOM：**全局入库串行锁**（下载并发、入库串行）+ 分块处理
- GitHub 批量超时（约 400 条处失败）：统一捕获 `asyncio.TimeoutError`，1s/2s/4s 指数退避重试，镜像 404 自动回退官方源
- GitHub tree 子目录同步全失败：raw 下载 URL 补全子目录前缀
- 平台配置弹窗无法打开：`u2Block` 未定义 + async 错误静默（补定义、错误 toast 可见）
- 知识库被删除后空库/误报「已同步」：重建时清空增量索引全量重同步
- 有道云偶发超时：请求等待超时放宽并重试
- 状态标签被长标题顶出列表、列表布局/省略号、定时页间距等 UI 问题

### Changed

- 同步列表标题：GitHub 显示目标 AstrBot 知识库名；「同步全部」移至同步页上端
- 删除操作二次确认弹窗；「删除本地」在知识库不存在时隐藏
- 移除不支持平台（Obsidian / 印象笔记 / Open Notebook），保留扩展架构
