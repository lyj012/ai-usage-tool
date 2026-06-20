# AI Usage Tool

AI Usage Tool 是一个**本地个人研发工作分析工具**，不是普通 token 统计器。它把 Codex / Claude Code 会话、指定项目的 Git 提交、每日人工复盘放在一起，生成可复盘的个人研发日报、趋势、周报和月报。

它解决的问题：

- 今天实际推进了什么研发工作。
- 哪些 AI 会话和 Git 提交可能有关联。
- 哪里出现了返工、异常或反复修改信号。
- 哪些技术主题近期重复出现，值得沉淀。
- 如何在本地保留数据，不上传云端。

核心输出：

```text
data/reports/YYYY-MM-DD/
  daily-report.md      # 人可读研发日报
  daily-report.json    # 结构化日报
  ai-inputs.jsonl      # 本地 AI 输入明细，可能包含敏感内容
  git-commits.jsonl    # Git 提交明细
  associations.jsonl   # AI-Git 关联结果
```

数据关系：

```text
Codex / Claude Code 会话
  + Git 提交和文件变更
  + 人工复盘
  -> daily-report.json / daily-report.md
  -> 周报、月报、趋势
  -> 本地 stdio MCP / ChatGPT Streamable HTTP MCP 只读查询
     -> 日报缺失或过期时，在本地 data_dir/reports 下自动生成或刷新缓存
```

## 功能状态

| 能力 | 状态 | 说明 |
|---|---|---|
| v2 个人研发日报 | 稳定 | 本地生成 `daily-report.json` / `daily-report.md` |
| 历史趋势、周报、月报 | 可用 | CLI 基于已生成日报聚合；MCP 查询默认按需补齐缺失或过期日报 |
| Streamlit Dashboard | 可用 | 本地看板查看日报和趋势 |
| 本地 stdio MCP | 可用 | 给本机 MCP 客户端读取本地报告 |
| ChatGPT Streamable HTTP MCP | 已实现 / 待端到端验证 | `mcp_chatgpt_server.py`，用于 ChatGPT 自定义 MCP，通过 HTTPS tunnel 连接 |
| HTTP JSON-RPC 调试入口 | 实验 | `mcp_http_server.py`，项目自定义 transport，不作为 ChatGPT 直连接入口 |

## 隐私和安全提示

- 本工具默认读取和写入本机文件，不调用 OpenAI API，不上传云端。
- `data/` 可能包含完整 AI 输入、本地路径、commit 信息和工作内容，不要提交到 Git。
- `aiusage-config.json` 可能包含本地项目路径，不要提交到 Git。
- ChatGPT Streamable HTTP MCP 暴露的是私人研发数据。只在你主动启动本地服务和 tunnel 时使用，用完关闭。
- 当前 ChatGPT 第一版建议使用开发模式的无认证只读 tunnel；生产或长期暴露需要 OAuth 2.1，本项目尚未实现 OAuth。
- 不要把 tunnel 地址、token、`aiusage-config.json` 或 `data/` 提交到 Git。

## 快速开始

创建本地配置：

```powershell
python .\aiusage.py init-config `
  --out .\aiusage-config.json `
  --project "ai-usage-tool=C:\Users\lenovo\Desktop\ai-usage-tool|https://github.com/lyj012/ai-usage-tool"
```

生成某天日报：

```powershell
python .\aiusage.py export-workday `
  --person lenovo `
  --date 2026-06-19 `
  --config .\aiusage-config.json `
  --verbose
```

打开本地看板：

```text
Windows: run_dashboard.bat
macOS:   run_dashboard.command
```

## 依赖

Python 3.11+。

可选安装 `tiktoken`，用于缺失原生 token 字段时估算输入 token：

```bash
pip install tiktoken
```

不安装也能运行，会用字符长度近似估算。

普通导出脚本只需要系统已安装 Python，不会安装看板依赖。看板脚本会自动创建 `.venv` 并安装 `requirements.txt` 中的看板依赖。也可以用 `pyproject.toml` 的可选依赖安装：

```bash
pip install -e ".[dashboard,tokens]"
```

如果要接入 ChatGPT 自定义 MCP，需要安装 MCP SDK 可选依赖：

```bash
pip install -e ".[chatgpt]"
```

安装后可使用 CLI：

```bash
aiusage --help
aiusage list-reports --help
```

如果 Windows 提示找不到 `aiusage`，通常是用户级 Python Scripts 目录没有加入 `PATH`，可先使用 `python .\aiusage.py ...`，或把 pip 输出提示的 Scripts 目录加入 `PATH`。

## 推荐分发方式

把整个 `aiusage-tool/` 文件夹压缩成 zip 发给同事：

```text
aiusage-tool/
  aiusage.py
  app.py
  mcp_server.py
  requirements.txt
  README.md
  run_workday_report.bat
  run_dashboard.bat
  run_dashboard.command
```

普通 Windows 用户生成 v2.0 个人研发日报，每天只需要双击：

```text
run_workday_report.bat
```

脚本会在项目目录下生成：

```text
data\reports\YYYY-MM-DD\daily-report.md
data\reports\YYYY-MM-DD\daily-report.json
```

这是 v2.0+ 个人研发日报。

双击导出时终端会显示进度，例如：

```text
扫描 Codex 会话文件...
Codex: 25/120 C:\Users\...
扫描 Claude Code 会话文件...
Claude: 50/260 C:\Users\...
总计命中 86 轮，准备写出
```

如果项目目录很多，第一次扫描会慢一些。只想快速扫描全局 `~/.codex` 和 `~/.claude` 时，可以命令行加：

```bash
--skip-project-root-scan
```

打开看板双击：

```text
Windows: run_dashboard.bat
macOS:   run_dashboard.command
```

打开网页后默认进入 `v2 个人日报`，可查看当天日报、历史趋势、技术主题和返工趋势。

## 我应该点哪个文件

- `run_workday_report.bat`：生成 v2.0 个人研发日报，输出 `data\reports\YYYY-MM-DD\daily-report.md` 和 `daily-report.json`。这是“个人研发工作分析版”的日常入口。
- `run_dashboard.bat`：打开网页看板，查看 `v2 个人日报`、历史趋势、技术主题和返工趋势。

如果 macOS 提示无法打开 `.command`，执行一次：

```bash
chmod +x run_dashboard.command
```

如果人员名不想用系统用户名，可以这样启动导出：

Windows PowerShell：

```powershell
$env:AIUSAGE_PERSON = "zac"
.\run_workday_report.bat
```

## v2.0 个人研发日报

第一步，创建本地项目配置：

```powershell
git clone https://github.com/lyj012/ai-usage-tool.git C:\Users\lenovo\Desktop\ai-usage-tool

python .\aiusage.py init-config `
  --out .\aiusage-config.json `
  --project "ai-usage-tool=C:\Users\lenovo\Desktop\ai-usage-tool|https://github.com/lyj012/ai-usage-tool"
```

配置文件示例：

```json
{
  "projects": [
    {
      "name": "ai-usage-tool",
      "path": "C:\\Users\\lenovo\\Desktop\\ai-usage-tool",
      "repo_url": "https://github.com/lyj012/ai-usage-tool"
    }
  ],
  "data_dir": "data"
}
```

`repo_url` 只用于记录来源和报告展示，Git 工作量采集读取的是 `path` 指向的本地 Git 仓库。只填写 GitHub URL、没有本地 clone 时，无法统计当天本机提交和文件变更。

第二步，生成某天日报：

```powershell
python .\aiusage.py export-workday `
  --person zac `
  --date 2026-06-14 `
  --config .\aiusage-config.json `
  --verbose
```

默认输出到：

```text
data/
  reflections/
    2026-06-14.json
  reports/
    2026-06-14/
      ai-inputs.jsonl
      git-commits.jsonl
      git-file-changes.jsonl
      associations.jsonl
      daily-report.json
      daily-report.md
```

也可以在看板中打开 `v2 个人日报` 视图，填写当天人工复盘后点击生成日报。

v2.0 日报生成不调用 OpenAI API，不上传云端。AI 会话与 Git 提交的关联、返工识别、工作质量指标均为本地规则估算，报告中会保留依据和置信度。MCP 是后续查询入口，默认只在本地 `data_dir/reports` 下补齐或刷新日报缓存。

## v2.1 历史趋势、周报和月报

v2.1 的 CLI 周报、月报和趋势命令基于已经生成的 `data/reports/YYYY-MM-DD/daily-report.json` 做历史读取和趋势分析。

MCP 查询默认使用 freshness-aware 的 `auto` 模式：

```text
日报存在且有效
  -> 直接读取缓存

日报不存在或过期
  -> 自动扫描 Codex / Claude Code / Git
  -> 自动生成或刷新本地日报
  -> 返回最新结果
```

列出已有日报：

```powershell
python .\aiusage.py list-reports --config .\aiusage-config.json
```

查看某天日报 JSON：

```powershell
python .\aiusage.py show-report --date 2026-06-15 --config .\aiusage-config.json
```

导出技术主题趋势：

```powershell
python .\aiusage.py topic-trends `
  --from 2026-06-15 `
  --to 2026-06-17 `
  --config .\aiusage-config.json
```

导出周报：

```powershell
python .\aiusage.py export-week `
  --person lenovo `
  --week 2026-W25 `
  --config .\aiusage-config.json
```

导出月报：

```powershell
python .\aiusage.py export-month `
  --person lenovo `
  --month 2026-06 `
  --config .\aiusage-config.json
```

默认输出：

```text
data\reports\YYYY-MM-DD_YYYY-MM-DD\topic-trends.json
data\reports\YYYY-Www\weekly-report.json
data\reports\YYYY-Www\weekly-report.md
data\reports\YYYY-MM\monthly-report.json
data\reports\YYYY-MM\monthly-report.md
```

看板的 `v2 个人日报` 页面底部也提供 `历史趋势` 区域，可按日期范围查看日报列表、项目趋势、技术主题和返工趋势。

## v3.0 本地 MCP Server

v3.0 增加只读 MCP Server，让支持 MCP 的客户端读取本地日报和趋势数据。

启动命令：

```powershell
python .\mcp_server.py
```

当前 MCP Server 通过 stdio JSON-RPC 工作，不需要 OpenAI API Key，不上传云端，不自动提交 Git，也不会删除报告文件。MCP 工具对调用方仍是只读查询；服务端为了回答查询，允许在配置指定的 `data_dir/reports` 下生成或刷新本地缓存日报。

已提供只读工具：

- `get_daily_work_report`：准备并读取指定日期 `daily-report.json`。
- `get_work_trend`：按日期范围准备日报并返回趋势聚合，范围最多 31 天。
- `search_work_records`：搜索成果、主题、返工、commit 和 AI-Git 关联；指定日期范围时会先准备范围内日报。
- `get_git_activity`：准备并读取指定日期的 Git 工作量明细。
- `get_ai_session_details`：准备并读取指定日期的 AI 会话摘要、关联和未关联原因。

工具参数里的 `config` 可选，默认读取当前目录下的 `aiusage-config.json`。本地 stdio 模式还支持 `refresh_mode`：

- `auto`：默认模式。日报不存在、当天日报超过短 TTL、或源数据指纹变化时自动刷新。
- `cache`：只读已有日报，不自动生成。
- `force`：强制重新扫描并生成日报。

数据准备链路：

```text
MCP 查询
  -> 检查 data/reports/YYYY-MM-DD/daily-report.json
  -> 检查 report-meta.json、源文件 mtime、Git HEAD 和当天短 TTL
  -> 缓存有效：直接读取 daily-report.json
  -> 缓存缺失或过期：扫描 Codex / Claude Code / Git
  -> 写入 data/reports/YYYY-MM-DD/daily-report.json 和 report-meta.json
  -> 返回日报、data_freshness 和 warnings
```

如果某天没有 AI 会话和 Git 提交，也会生成合法空日报，并标记 `data_status: no_activity`。范围趋势会区分 `available`、`no_activity` 和 `failed`，不会把无活动日期误判为文件缺失。

## ChatGPT 自定义 MCP 第一版

ChatGPT 不能直接连接本地 `stdio` MCP，也不能可靠使用本项目之前手写的 HTTP JSON-RPC 调试入口。ChatGPT 自定义 MCP 第一版使用 `mcp_chatgpt_server.py`，通过 MCP Python SDK 启动标准 Streamable HTTP endpoint。

本地启动服务前先准备依赖：

```powershell
cd C:\Users\lenovo\Desktop\ai-usage-tool
python -m pip install -e ".[chatgpt]"
```

默认 MCP 地址：

```text
http://127.0.0.1:8765/mcp
```

先启动 HTTPS tunnel，例如：

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

或：

```powershell
ngrok http 8765
```

复制 tunnel hostname 后，再启动 MCP server。Python MCP SDK 有 DNS rebinding 保护，tunnel 场景需要允许该 host：

```powershell
$env:MCP_ALLOWED_HOSTS = "<tunnel-host>"
$env:MCP_ALLOWED_ORIGINS = "https://<tunnel-host>"
python .\mcp_chatgpt_server.py --host 127.0.0.1 --port 8765 --config .\aiusage-config.json
```

ChatGPT 创建自定义 MCP / App 时填写 tunnel 的 HTTPS `/mcp` 地址：

```text
https://<tunnel-host>/mcp
```

认证方式第一版选择无认证 / None，仅用于你本人主动启动的本地开发 tunnel。OpenAI 的 ChatGPT MCP 认证流程面向 OAuth 2.1；本项目的静态 `AIUSAGE_MCP_TOKEN` 只属于下方手写 HTTP 调试入口，不是 ChatGPT 自定义 MCP 的最终认证方案。

`mcp_chatgpt_server.py` 的工具调用会强制走 Remote-safe 模式：

- 不暴露 `config` 参数，ChatGPT 不能传入任意本地配置路径。
- 不暴露 `refresh_mode` 参数，ChatGPT 默认使用服务端控制的 `auto` 模式。
- 使用启动参数 `--config` 指定的服务端配置。
- 返回脱敏视图，不返回原始 `session_id`，跨工具查询使用 `session_ref`。
- 不上传云端、不调用外部 AI API、不提交 Git、不删除数据。
- 只会在服务端配置指定的 `data_dir/reports` 下生成或刷新本地日报缓存。

第一次查询某个日期或范围时，可能需要扫描本地 Codex / Claude Code / Git，速度会比缓存查询慢。后续查询会优先使用缓存。历史范围最多 31 天。

本仓库代码已经准备好本地启动和 tunnel 验证，但 ChatGPT 页面端 `Scan Tools` 和真实工具调用仍需要你在自己的 ChatGPT 账号中执行确认。

## HTTP JSON-RPC 调试入口

`mcp_http_server.py` 是项目自定义的 HTTP JSON-RPC 调试入口，不是标准 Streamable HTTP MCP，不作为 ChatGPT 自定义 MCP 的推荐接入方式。它保留用于本地 HTTP smoke test、token 鉴权和 Remote-safe 数据边界回归测试。

本地启动：

```powershell
$env:AIUSAGE_MCP_TOKEN = "<local-random-token>"
python .\mcp_http_server.py --host 127.0.0.1 --port 8765
```

接口：

- `GET /health`：健康检查，不返回日报数据。
- `POST /mcp`：MCP JSON-RPC endpoint，复用本地 stdio MCP 的只读工具。
- `GET /mcp`：当前不支持 SSE / Streamable HTTP，返回 Method Not Allowed。

Remote MCP 访问需要 bearer token：

```text
Authorization: Bearer <local-random-token>
```

未配置 `AIUSAGE_MCP_TOKEN` 时，只允许本机无 token 访问，方便本地 smoke test。只要配置了 token，所有 `/mcp` 请求都必须携带正确 bearer token，包括 tunnel 转发后看起来来自 `127.0.0.1` 的请求。不要把 token 写入代码、README、测试数据或提交记录。

当前 HTTP JSON-RPC 调试入口仍然是只读工具面：不上传云端、不自动提交 Git、不删除报告、不新增写入型工具、不自动扫描 ChatGPT 聊天记录。为了回答查询，服务端可能在配置指定的 `data_dir/reports` 下生成或刷新本地日报缓存。HTTP 响应默认会移除本地路径、完整 AI 输入、邮箱、完整 session ID 和完整 hash 等远程不必要字段。

HTTP JSON-RPC 调试入口与本地 stdio MCP 的差异：

- stdio MCP 可使用 `config` 参数读取指定本地配置。
- HTTP MCP 的工具列表不会暴露 `config` 参数，远程调用方不能选择服务端配置文件。
- HTTP MCP 会把原始 `session_id` 转换成 `session_ref`；后续查询会话详情时使用 `session_ref`。
- HTTP MCP 会对文本内容做有限脱敏，包括本地路径、邮箱、URL 和常见 token/key/password 形态。它不能保证识别所有公司名称、客户名称或业务敏感语义。

## 版本口径

- 产品/CLI 包版本：`0.3.0`，见 `pyproject.toml` 和 `aiusage --version`。
- MCP Server 版本：`3.0.0`，表示 MCP 能力阶段，不等于 Python 包版本。
- 日报 schema：当前日报为 `schema_version: 2.0`，趋势/周报/月报为 `2.1`。
- MCP 协议版本：`2025-06-18`，用于当前手写 JSON-RPC MCP surface。

## ai-inputs.jsonl 字段

关键字段：

- `person`：导出人。
- `tool`：`codex` 或 `claude`。
- `date`：按导出时区归属的日期。
- `project` / `project_cwd`：项目名和项目路径。
- `session_id` / `turn_id`：会话和轮次标识。
- `input_at`：用户输入时间。
- `task_started_at` / `task_finished_at`：任务开始/结束时间。
- `next_input_at`：同会话下一次用户输入时间。
- `input_interval_seconds`：本次输入与上一次输入的间隔。
- `ai_active_seconds`：AI 任务执行时间。
- `after_done_gap_seconds`：AI 完成后到下一次输入的间隔。
- `input_tokens`：模型输入 token；原生字段优先，缺失时估算。
- `response_tokens`：模型输出 token。
- `cache_tokens`：缓存输入 token。
- `reasoning_tokens`：推理 token。
- `total_tokens`：总 token。
- `token_source`：`native` 或 `estimated`。
- `duration_source`：`native` 或 `estimated`。
- `input_text` / `input_preview`：原始输入与摘要。

其中 `input_preview` 是每轮输入的规则摘要。v2 日报会基于这些明细生成 AI 使用、AI-Git 关联、返工信号和技术主题分析。

## 关于 AI 摘要

当前版本默认不调用外部 AI，原因是本地日报要足够简单、稳定，不要求配置 API Key。

如果后续要引入 AI 梳理，建议基于 `data/reports/YYYY-MM-DD/ai-inputs.jsonl` 再做一个独立步骤：

1. 按 `person + date + project + session_id` 分组。
2. 每组只发送 `input_preview` 和必要统计字段，不发送完整 `input_text`。
3. 让 AI 输出 `session_title`、`work_summary`、`risk_or_blocker`、`deliverable`。
4. 回写一个 `ai_session_summary.md` 或 `ai_session_summary.jsonl`。

这样不会影响本地日报生成，也能控制隐私和 token 成本。

## 口径说明

Codex：

- 优先读取 `event_msg.token_count.info.last_token_usage`。
- 优先读取 `task_started` / `task_complete` / `turn_aborted` 的原生耗时。
- 过滤环境上下文、系统注入和工具结果。

Claude Code：

- 优先读取 assistant message 的 `usage`。
- 用户输入到下一条用户输入前的最后 assistant 时间作为任务结束时间。
- `ai_active_seconds` 默认最多按 30 分钟估算，可用 `--max-estimated-turn-seconds` 调整。
- 默认不包含 `subagents/` 和 `isSidechain=true`。

## 注意

`input_tokens` 是模型侧输入上下文 token，不等于用户输入文本本身的 token。对 Codex / Claude 的成本分析，应主要看 `total_tokens`、`cache_tokens`、`response_tokens`。
