# Changelog

本项目的所有显著变更按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 记录，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。版本与 `metadata.yaml` 保持一致。

## [Unreleased]

### Fixed

- GitHub 同步：仓库/分支不存在或已删除（404）时不再向上抛异常崩溃，改为捕获并标记同步源为异常状态

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
