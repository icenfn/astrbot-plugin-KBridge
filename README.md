# KBridge

> **astrbot 知识库同步插件，装这一个就够了！** 支持从腾讯 ima、有道云笔记、GitHub、Url、Open Notebook、Memos 等同步，更多支持的平台正在路上…

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16-2563eb)](https://github.com/AstrBotDevs/AstrBot)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-4caf50)](./LICENSE)
[![Plugin](https://img.shields.io/badge/AstrBot%20Plugin-KBridge-0ea5e9)](https://github.com/icenfn/astrbot-plugin-KBridge)

将你分散在 **ima 知识库**、**有道云笔记**、**GitHub 仓库**、**Open Notebook**、**Memos** 里的内容，按需增量同步进 AstrBot 知识库，让 AstrBot 的 RAG 检索直接可用。自带 WebUI 管理页面，支持定时同步。

## 目录

- [功能特性](#功能特性)
- [安装](#安装)
- [快速开始](#快速开始)
- [平台配置](#平台配置)
- [WebUI 管理页面](#webui-管理页面)
- [命令](#命令)
- [定时同步](#定时同步)
- [同步逻辑](#同步逻辑)
- [限制](#限制)
- [开发](#开发)
- [贡献](#贡献)
- [License](#license)

## 功能特性

- **腾讯 ima 知识库**：页面直接展示账号下可同步的自建知识库，无需手动拉取；支持知识库内**文件夹递归**同步与**笔记文本读取**
- **有道云笔记**：官方 MCP（SSE）协议单库同步，目标 AstrBot 知识库名称可配置（默认 `YoudaoNote`）
- **GitHub 仓库**：多仓库管理，支持**整个仓库**（默认分支）或 **tree 子目录**（如 `…/tree/master/packages/docs/src/pages/en`）
- **格式白名单**：仅导入 AstrBot 可解析格式（`md/txt/markdown/rst/adoc/docx/xlsx/xls/pdf/epub`），自动忽略 `node_modules/dist/build` 等目录
- **增量同步**：ima 按 `media_id`、有道云按笔记 id、GitHub 按文件相对路径、Open Notebook 按 source/note id、Memos 按 memo uid 去重，重复同步不产生重复文档
- **自动建库 + 重排序**：目标知识库不存在时自动创建，并自动绑定已配置的 Embedding 与 Rerank（重排序）模型；知识库图标（emoji）可在「平台配置」页面配置（`kb_emoji`，默认 📥），简介优先取来源描述（GitHub 仓库简介），否则回退平台名
- **定时同步**：按间隔（天/时/分/秒）自动同步已开启「定时」的同步源，日志高亮分级展示（成功/警告/失败）
- **平台开关**：每个平台可独立启用/禁用，禁用后同步列表自动标记、手动/定时/全部同步均跳过该平台
- **url2kb**：网页一键转 Markdown 入库——多个分组（分组名即 AstrBot 知识库名，带备注）、多 URL（自动识别网页标题），同步/定时同步均支持
- **Open Notebook（beta）**：自托管 RAG 服务（`lfnovo/open-notebook`）接入，页面自动展示 notebooks；同步 notebook 的 sources/notes 内容入库，目标知识库名 = notebook 名
- **Memos（beta）**：自建 Memos 实例（`usememos/memos`）接入，单库同步源，分页拉取全部备忘录（Markdown）入库，目标知识库名可配置（默认 `Memos`）
- **WebUI 管理页面**：插件自带 GUI，平台配置、同步管理、定时同步一目了然

## 安装

1. 在 AstrBot 插件市场搜索 `KBridge` 安装，或将本仓库克隆到 AstrBot `plugins/` 目录
2. 重启 AstrBot，在插件 WebUI 详情页打开 **KBridge 管理**（`pages/dashboard`）
3. 在「平台配置」中填写需要使用的平台凭据（见下）

## 快速开始

1. 进入「平台配置」，填写 ima / 有道云 / GitHub 的凭据并保存
2. 进入「同步管理」：
   - **ima**：页面自动展示自建知识库，点击卡片「同步」即可
   - **有道云**：显示单库同步源，点击「同步」即可
   - **GitHub**：在「平台配置 → Github」中输入仓库 URL（如 `https://github.com/Chalarangelo/30-seconds-of-code`），回到同步页点击「同步」
   - **url2kb**：在「平台配置 → url2kb」中添加分组（名称即知识库名）与网页 URL（自动识别标题），回到同步页点击「同步」即抓取转 Markdown 入库
   - **Open Notebook（beta）**：在「平台配置 → Open Notebook（beta）」填写 API 地址与密码（未启用 `OPEN_NOTEBOOK_PASSWORD` 可留空），回到同步页即可看到 notebooks 列表，点击「同步」即把 sources/notes 内容入库
   - **Memos（beta）**：在「平台配置 → Memos（beta）」填写实例地址与 API Token（Memos 设置 → 我的账户 → 访问令牌），回到同步页点击「同步」即把全部备忘录入库
3. 在「定时同步」页设置间隔并启用，即可自动同步已开启「定时」的同步源

## 平台配置

| 平台 | 配置项 | 获取方式 |
| --- | --- | --- |
| 腾讯 ima | `ima_client_id` / `ima_api_key` | [https://ima.qq.com/agent-interface](https://ima.qq.com/agent-interface) 登录后生成 |
| 有道云笔记 | `youdao_api_key` | [https://mopen.163.com](https://mopen.163.com) 获取（需账号绑定手机号） |
| 有道云笔记 | `youdao_target_kb` | 同步至 AstrBot 知识库名称（默认 `YoudaoNote`） |
| GitHub | `github_token`（必填） | [https://github.com/settings/tokens](https://github.com/settings/tokens) 生成（勾选 `repo` 权限）；未配置时无法添加仓库 |
| GitHub | `github_raw_mirror`（可选） | Raw 加速镜像前缀（默认 `https://gh.dpik.top/`），raw 文件下载走镜像；留空使用官方源。AstrBot「设置→网络→GitHub 加速地址」仅作用于插件市场下载，插件内需在此单独配置 |
| url2kb | 无凭据 | 网页转 Markdown：分组（名 = AstrBot 知识库名）+ 备注 + URL 列表（自动识别标题），抓取正文转纯文本 Markdown 入库 |
| Open Notebook | `open_notebook_url` | 自托管 Open Notebook API 地址（默认 `http://localhost:5055`） |
| Open Notebook | `open_notebook_password` | API 密码（未启用 `OPEN_NOTEBOOK_PASSWORD` 可留空） |
| Memos | `memos_url` | 自建 Memos 实例地址（如 `https://memos.example.com`） |
| Memos | `memos_token`（必填） | Memos 设置 → 我的账户 → 访问令牌 |
| Memos | `memos_target_kb` | 同步至 AstrBot 知识库名称（默认 `Memos`） |
| 通用 | `kb_emoji` | 同步时自动创建 AstrBot 知识库所用的图标（emoji，默认 `📥`；在「平台配置」页面配置） |

## WebUI 管理页面

插件自带管理页面（AstrBot Plugin Pages）：在 WebUI 插件详情页打开 **KBridge 管理**（`pages/dashboard`）。

- **总览**：平台配置数（x/n）、同步源数、已同步文档数
- **平台配置**：全屏弹窗填写各平台凭据；GitHub 平台配置内嵌仓库 URL 管理（添加/删除）；每个平台卡片带启用/禁用开关
- **同步管理**：ima 自建库自动展示、有道云单库、GitHub 仓库卡片、Open Notebook notebooks 自动展示、Memos 单库；每张卡片可开关「定时」同步（与同步按钮同一行）；同步中显示进度并可一键取消（已入库部分保留）；目标 AstrBot 知识库被删除时显示「未同步」
- **定时同步**：设置间隔（天/时/分/秒）并启用，自动同步已开启「定时」的同步源；页面展示高亮日志（成功/警告/失败）
- **同步进度**：后台执行 + 自动轮询刷新，同步按钮实时显示 x/y 进度

## 定时同步

在「定时同步」页面：

1. 输入间隔（天 / 时 / 分 / 秒，支持任意组合，启用时至少 1 秒）
2. 打开「启用定时同步」开关并保存

到点后，插件后台自动同步**已开启「定时」开关**的同步源（在「同步管理」卡片上控制）。每一轮的开始、完成、各同步源明细（共 X，新增 Y，跳过 Z，失败 W）都会写入环形日志（保留最近 100 条），在页面按成功（绿）/ 警告（橙）/ 失败（红）高亮展示。插件卸载或关闭定时时任务自动清理。

## 同步逻辑

1. **ima**：递归遍历知识库（含子文件夹，深度 ≤ 8，单次上限 2000 条），对每个条目 `get_media_info` 获取原文访问链接 → 下载内容 → 按类型落地（网页/公众号转 Markdown、文件原样）→ `upload_document` 写入 AstrBot 知识库
2. **跳过**：订阅库/共享笔记无权限（220030/210005/210011/210034）、无访问链接、AstrBot 不支持解析的格式（如 PPTX）
3. **笔记读取**：ima 笔记（media_type=11）经官方 notes 接口 `get_doc_content` 读取纯文本入库（需笔记作者身份）
4. **有道云**：官方 MCP（SSE + `X-API-Key` 认证）递归列出目录下笔记，`getNoteTextContent` 读取内容统一存为 Markdown，写入配置指定的目标知识库（默认 `YoudaoNote`）
5. **GitHub**：`Git Trees API`（recursive）获取文件树 → 过滤 AstrBot 支持格式与忽略目录 → `raw.githubusercontent.com` 下载 → 按文件相对路径增量入库；目标知识库以仓库名自动创建，描述取仓库简介（无则用平台名）

## 限制

- 依赖 AstrBot 已配置 Embedding 模型方可自动建库；Rerank 模型若已配置会自动绑定，未配置则跳过重排序
- **ima 权限限制**：通过 OpenAPI 读取**订阅的知识库**文件受 ima 授权限制（code=220030），需在 ima 客户端授权，或使用自己创建的知识库
- **ima 频控极严**：`get_media_info` 高频调用会触发频率超限（code=200001 / HTTP 403），同步按条低频串行调用并自动退避重试
- GitHub 公开仓库匿名同步受 API 限流（60 次/小时/仓库树获取），大仓库单次同步上限 2000 个文件

## 开发

```
astrbot-plugin-KBridge/
├── main.py            # Star 入口、Web API
├── ima_client.py      # IMA OpenAPI 客户端
├── youdao_client.py   # 有道云笔记 MCP SSE 客户端（标准库实现）
├── github_client.py   # GitHub Git Trees / raw 客户端
├── sync_manager.py    # 同步源管理 + 同步核心
├── pages/dashboard/   # WebUI 管理页面（index.html / app.js / style.css）
├── metadata.yaml      # 插件元数据
├── _conf_schema.json  # 插件配置
└── requirements.txt
```

```bash
# 语法检查
python3 -m py_compile main.py sync_manager.py ima_client.py youdao_client.py github_client.py
# 前端检查
node -c pages/dashboard/app.js
```

## 贡献

欢迎提交 Issue 与 PR。请遵循 AstrBot 插件开发规范，并在提交前通过上述语法检查。

## License

[MIT](./LICENSE)
