# KBridge — AstrBot 外部知识源同步插件

同步外部知识源（**腾讯 ima 知识库**、**有道云笔记**、**GitHub 仓库**）到 AstrBot 知识库，支持增量同步与 WebUI 管理。

## 功能

- **ima 知识库同步**：页面直接展示 ima 账号下可同步的知识库（自建），无需手动拉取；支持知识库内**文件夹递归**同步
- **有道云笔记同步**：单库「全部笔记」同步为 Markdown 入库，目标 AstrBot 知识库名称可配置（默认 `YoudaoNote`）
- **GitHub 仓库同步**：添加多个仓库，支持**整个仓库**（默认分支）或 **tree 子目录**（如 `…/tree/master/packages/docs/src/pages/en`）；Git Trees API 遍历 + raw 下载
- **格式白名单**：仅导入 AstrBot 可解析的格式（`md/txt/markdown/rst/adoc/docx/xlsx/xls/pdf/epub`），自动忽略 `node_modules/dist/build` 等目录
- **增量同步**：仅同步新增内容（ima 按 `media_id`、有道云按笔记 id、GitHub 按文件相对路径去重），重复同步不产生重复文档
- **定时同步**：按间隔（天/时/分/秒）自动同步已开启「定时」的同步源，日志高亮分级展示（成功/警告/失败）
- **自动建库 + 重排序**：目标 AstrBot 知识库不存在时自动创建，并自动绑定已配置的 Embedding 与 **Rerank（重排序）模型**
- **内容落地**：网页/公众号文章转为 Markdown；PDF/DOCX/XLSX/MD/TXT/EPUB 原样入库
- **命令管理**：`/kbridge` 全命令管理
- **WebUI 管理页面**：插件自带 GUI 页面，同步/状态可视化操作

## 安装

1. 在 AstrBot 插件市场/插件目录安装本插件
2. 在插件 WebUI 管理页「平台配置」填写：
   - `ima_client_id` / `ima_api_key`：在 [https://ima.qq.com/agent-interface](https://ima.qq.com/agent-interface) 登录后生成
   - `youdao_api_key`：有道云笔记 API Key（在 [https://mopen.163.com](https://mopen.163.com) 获取，需账号绑定手机号）
   - `youdao_target_kb`：有道云笔记同步至 AstrBot 知识库的名称（默认 `YoudaoNote`）
   - `github_token`（可选）：GitHub Token，公开仓库可匿名同步；私仓或高频同步建议填写（[https://github.com/settings/tokens](https://github.com/settings/tokens) 生成）

## WebUI 管理页面

插件自带管理页面（AstrBot Plugin Pages）：在 WebUI 插件详情页打开 **KBridge 管理**（`pages/dashboard`）。支持：

- 平台配置：页面内填写各平台凭据与有道云目标知识库名，全屏表单弹窗；GitHub 平台配置内嵌仓库 URL 管理（添加/删除）
- 状态总览：平台配置、同步源数、已同步文档数
- 同步管理：ima 可同步知识库直接展示（自动补全）；有道云单库展示；GitHub 仓库卡片同步；每张卡片可开关「定时」同步
- 定时同步：设置间隔（天/时/分/秒）并启用后，自动同步已开启「定时」的同步源；页面展示高亮日志（成功/警告/失败）
- 同步进度：后台执行 + 自动轮询刷新

## 命令

| 命令 | 说明 |
| --- | --- |
| `/kbridge` | 帮助 |
| `/kbridge kbs` | 列出 IMA 账号可同步的知识库（自建） |
| `/kbridge sub list` | 同步源列表 |
| `/kbridge sub del <序号>` | 删除同步源 |
| `/kbridge sync [序号\|all]` | 手动同步 |

## 同步逻辑

1. ima：递归遍历知识库（含子文件夹），对每个条目 `get_media_info` 获取原文访问链接 → 下载内容 → 按类型落地（网页转 Markdown / 文件原样）→ `upload_document` 写入 AstrBot 知识库
2. 跳过：订阅库/共享笔记无权限（220030/210005/210011/210034）、无访问链接、AstrBot 不支持解析的格式（如 PPTX）
3. 笔记读取：ima 笔记（media_type=11）经官方 notes 接口 `get_doc_content` 读取纯文本入库（需笔记作者身份）
4. 有道云：经官方 MCP（SSE + `X-API-Key` 认证）递归列出目录下笔记，`getNoteTextContent` 读取内容统一存为 Markdown，写入配置指定的目标知识库（默认 `YoudaoNote`）
5. GitHub：`Git Trees API`（recursive）获取仓库文件树 → 过滤 AstrBot 支持格式与忽略目录 → `raw.githubusercontent.com` 下载内容 → 按文件相对路径增量入库；目标知识库默认以仓库名自动创建

## 限制（v0.1）

- 依赖 AstrBot 已配置 Embedding 模型方可自动建库；Rerank 模型若已配置会自动绑定，未配置则跳过重排序
- **ima 权限限制**：通过 OpenAPI 读取**订阅的知识库**文件受 ima 授权限制（code=220030），需在 ima 客户端授权，或使用你自己创建的知识库
- **ima 频控极严**：`get_media_info` 高频调用会触发频率超限（code=200001 / HTTP 403），同步按条低频串行调用并自动退避重试
- GitHub 公开仓库匿名同步受 API 限流（60 次/小时/仓库树获取），大仓库单次同步上限 2000 个文件

## 开发

```
astrbot-plugin-KBridge/
├── main.py            # Star 入口、/kbridge 命令、Web API
├── ima_client.py      # IMA OpenAPI 客户端
├── youdao_client.py   # 有道云笔记 MCP SSE 客户端（标准库实现）
├── github_client.py   # GitHub Git Trees / raw 客户端
├── sync_manager.py    # 同步源管理 + 同步核心
├── pages/dashboard/   # WebUI 管理页面（index.html / app.js / style.css）
├── metadata.yaml      # 插件元数据
├── _conf_schema.json  # 插件配置
└── requirements.txt
```

## 致谢

- IMA OpenAPI 协议参考官方 ima-skills 包（https://app-dl.ima.qq.com/skills/ima-skills-*.zip）
- 有道云笔记协议参考官方 YoudaoNote Skills / youdaonote-cli
- GitHub REST API：Git Trees / raw（https://docs.github.com/rest）
- AstrBot 知识库 API：`context.kb_manager`（create_kb / upload_document）

## License

MIT
