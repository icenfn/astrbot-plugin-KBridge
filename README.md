# KBridge — AstrBot 外部知识源同步插件

同步外部知识源（当前支持 **腾讯 ima 知识库**、**有道云笔记**）到 AstrBot 知识库，支持增量同步与同步源管理。

## 功能

- **ima 知识库同步**：将 ima 账号下自建的知识库同步到 AstrBot
- **有道云笔记同步**：将有道云笔记目录（根目录/文件夹）下的笔记同步为 Markdown 入库
- **增量同步**：仅同步新增内容（按 ima `media_id` 去重），重复同步不产生重复文档
- **自动建库**：目标 AstrBot 知识库不存在时自动创建（需已配置嵌入模型）
- **内容落地**：网页/公众号文章转为 Markdown；PDF/DOCX/XLSX/MD/TXT/EPUB 原样入库
- **命令管理**：`/kbridge` 全命令管理，无需改代码
- **WebUI 管理页面**：插件自带 GUI 页面，同步源/同步可视化操作

## 安装

1. 在 AstrBot 插件市场/插件目录安装本插件
2. 在插件配置面板填写：
   - `ima_client_id` / `ima_api_key`：在 [https://ima.qq.com/agent-interface](https://ima.qq.com/agent-interface) 登录后生成
   - `youdao_api_key`：有道云笔记 API Key（在 [https://mopen.163.com](https://mopen.163.com) 获取，需账号绑定手机号）

## WebUI 管理页面

插件自带管理页面（AstrBot Plugin Pages）：在 WebUI 插件详情页打开 **KBridge 管理**（`pages/dashboard`）。支持：

- 平台配置：页面内填写各平台凭据（ima Client ID / API Key、有道云 API Key），多平台架构预留 GitHub Repository / Open Notebook / url2kb
- 状态总览：平台配置、同步源数、已同步文档数
- 拉取同步源：下拉选择 IMA 自建知识库，目标 AstrBot 知识库自动创建（名称与 IMA 知识库一致）
- 同步源管理：单个同步 / 删除，状态与错误信息展示
- 同步进度：后台执行 + 自动轮询刷新

## 命令

| 命令 | 说明 |
| --- | --- |
| `/kbridge` | 帮助 |
| `/kbridge kbs` | 列出 IMA 账号可同步的知识库（自建） |
| `/kbridge sub add <id\|名称> [--to 目标库名]` | 拉取同步源（`--to` 指定 AstrBot 侧知识库名） |
| `/kbridge sub list` | 同步源列表 |
| `/kbridge sub del <序号>` | 删除同步源 |
| `/kbridge sync [序号\|all]` | 手动同步 |

## 同步逻辑

1. 翻页拉取 ima 知识库根目录全部条目
2. 对每个新条目：`get_media_info` 获取原文访问链接 → 下载内容 → 按类型落地（网页转 Markdown / 文件原样）→ `upload_document` 写入 AstrBot 知识库
3. 跳过：订阅库/共享笔记无权限（220030/210005/210011/210034）、无访问链接、AstrBot 不支持解析的格式（如 PPTX）
4. 笔记读取：ima 笔记（media_type=11）经官方 notes 接口 `get_doc_content` 读取纯文本入库（需笔记作者身份）
5. 有道云：经官方 MCP（SSE + `X-API-Key` 认证）递归列出目录下笔记，`getNoteTextContent` 读取内容统一存为 Markdown

## 限制（v0.1）

- 仅同步知识库根目录，子文件夹暂未递归（后续版本）
- 依赖 AstrBot 已配置 Embedding 模型方可自动建库
- **ima 权限限制**：通过 OpenAPI 读取**订阅的知识库**文件受 ima 授权限制（code=220030），需在 ima 客户端授权，或使用你自己创建的知识库
- **ima 频控极严**：`get_media_info` 高频调用会触发频率超限（code=200001 / HTTP 403），同步按条低频串行调用并自动退避重试

## TODO：待支持的知识库平台

- [x] **ima** — 已支持（同步 + 管理页面）
- [x] **有道云笔记** — 已支持（目录/笔记同步，基于官方 MCP）
- [ ] **GitHub Repository** — 仓库文件（含 README/文档目录）同步
- [ ] **Open Notebook** — NotebookLM 开放笔记本导入
- [ ] **url2kb** — URL 批量转知识库（网页链接直接入库）

## 开发

```
astrbot-plugin-KBridge/
├── main.py            # Star 入口、/kbridge 命令、Web API
├── ima_client.py      # IMA OpenAPI 客户端
├── sync_manager.py    # 同步源管理 + 同步核心
├── pages/dashboard/   # WebUI 管理页面（index.html / app.js / style.css）
├── metadata.yaml      # 插件元数据
├── _conf_schema.json  # 插件配置
└── requirements.txt
```

## 致谢

- IMA OpenAPI 协议参考官方 ima-skills 包（https://app-dl.ima.qq.com/skills/ima-skills-*.zip）
- AstrBot 知识库 API：`context.kb_manager`（create_kb / upload_document）

## License

MIT
