# AI Usage Tool

一个本地小工具，用来导出和汇总 Codex / Claude Code 的每日使用记录。

v2.0 增加个人研发工作日报能力：在本地汇总 AI 使用记录、指定项目的 Git 工作量、每日人工复盘，并生成 `daily-report.json` 和 `daily-report.md`。

每个人每天只需要交一个 zip，里面只有：

- `inputs.jsonl`：机器统计源，一行代表一轮真实用户输入。
- `summary.md`：当天人工可读汇总。

## 依赖

Python 3.11+。

可选安装 `tiktoken`，用于缺失原生 token 字段时估算输入 token：

```bash
pip install tiktoken
```

不安装也能运行，会用字符长度近似估算。

普通导出脚本只需要系统已安装 Python，不会安装看板依赖。看板脚本会自动创建 `.venv` 并安装 `requirements.txt` 中的依赖。

## 推荐分发方式

把整个 `aiusage-tool/` 文件夹压缩成 zip 发给同事：

```text
aiusage-tool/
  aiusage.py
  app.py
  requirements.txt
  README.md
  run_workday_report.bat
  run_dashboard.bat
  run_export_today.command
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

这是 v2.0 个人研发日报，不是旧版 AI 使用 zip。

macOS 旧版 v1 zip 导出脚本仍可双击：

```text
run_export_today.command
```

它会在桌面生成：

```text
ai-usage-用户名-YYYY-MM-DD.zip
```

这个 zip 是旧版 AI 使用统计，不是 v2 研发日报。

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

管理者/汇总人双击：

```text
Windows: run_dashboard.bat
macOS:   run_dashboard.command
```

打开网页后上传所有人的 `ai-usage-*.zip`，即可查看总览、按天、按人、按项目、会话摘要、高 token 轮次、长任务轮次，并下载团队报表。

## 我应该点哪个文件

- `run_workday_report.bat`：生成 v2.0 个人研发日报，输出 `data\reports\YYYY-MM-DD\daily-report.md` 和 `daily-report.json`。这是“个人研发工作分析版”的日常入口。
- `run_dashboard.bat`：打开网页看板，默认进入 `v2 个人日报`，也可以切换到旧版团队 AI 使用统计。
- `run_export_today.command`：macOS 旧版 v1 AI 使用 zip 导出，只生成 `ai-usage-用户名-YYYY-MM-DD.zip`，不是 v2 研发日报。

如果 macOS 提示无法打开 `.command`，执行一次：

```bash
chmod +x run_export_today.command run_dashboard.command
```

如果人员名不想用系统用户名，可以这样启动导出：

macOS / Linux：

```bash
AIUSAGE_PERSON=zac ./run_export_today.command
```

Windows PowerShell：

```powershell
$env:AIUSAGE_PERSON = "zac"
.\run_workday_report.bat
```

## 个人每日导出

Windows PowerShell：

```powershell
python .\aiusage.py export-day `
  --person zac `
  --date 2026-06-09 `
  --out "$env:USERPROFILE\Desktop" `
  --verbose
```

macOS / Linux：

```bash
python3 aiusage.py export-day \
  --person zac \
  --date 2026-06-09 \
  --out ~/Desktop \
  --verbose
```

输出示例：

```text
C:\Users\用户名\Desktop\ai-usage-zac-2026-06-09.zip
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

v2.0 当前不调用 OpenAI API，不接入 MCP，不上传云端。AI 会话与 Git 提交的关联、返工识别、工作质量指标均为本地规则估算，报告中会保留依据和置信度。

## v2.1 历史趋势、周报和月报

v2.1 基于已经生成的 `data/reports/YYYY-MM-DD/daily-report.json` 做历史读取和趋势分析，不重新扫描原始 AI / Git 数据。

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

当前 MCP Server 通过 stdio JSON-RPC 工作，不需要 OpenAI API Key，不上传云端，不自动提交 Git，也不会删除报告文件。

已提供只读工具：

- `get_daily_work_report`：读取指定日期 `daily-report.json`。
- `get_work_trend`：按日期范围读取日报并返回趋势聚合。
- `search_work_records`：搜索成果、主题、返工、commit 和 AI-Git 关联。
- `get_git_activity`：读取指定日期的 Git 工作量明细。
- `get_ai_session_details`：读取指定日期的 AI 会话摘要、关联和未关联原因。

工具参数里的 `config` 可选，默认读取当前目录下的 `aiusage-config.json`。MCP Server 只读取配置中的 `data_dir` 和 `data/reports/` 下的本地报告；如果指定日期没有日报，会返回结构化错误和 warning。

默认扫描：

- `~/.codex`
- `~/.claude`
- `~/2027` 下项目内的 `.codex`、`.codex-ui-dev`、`.claude`

只导出 Codex：

```bash
python aiusage.py export-day --person zac --date 2026-06-09 --only codex --out ~/Desktop
```

只导出 Claude Code：

```bash
python aiusage.py export-day --person zac --date 2026-06-09 --only claude --out ~/Desktop
```

导出日期范围：

Windows PowerShell：

```powershell
python .\aiusage.py export-range `
  --person zac `
  --from 2026-06-01 `
  --to 2026-06-09 `
  --out "$env:USERPROFILE\Desktop"
```

macOS / Linux：

```bash
python3 aiusage.py export-range \
  --person zac \
  --from 2026-06-01 \
  --to 2026-06-09 \
  --out ~/Desktop
```

默认不统计 Claude `subagents/` 和 sidechain，避免重复放大数据。确实需要时加：

```bash
--include-subagents
```

只想快速扫描全局记录、不深扫 `~/2027` 项目目录时：

```bash
--skip-project-root-scan
```

## 团队汇总

把所有人交上来的 `ai-usage-*.zip` 放到同一个目录，例如：

```text
C:\Users\用户名\Downloads\aiusage-exports\
  ai-usage-zac-2026-06-09.zip
  ai-usage-hxl-2026-06-09.zip
  ai-usage-chenxiaohu-2026-06-09.zip
```

执行：

Windows PowerShell：

```powershell
python .\aiusage.py merge `
  --input "$env:USERPROFILE\Downloads\aiusage-exports" `
  --out "$env:USERPROFILE\Desktop\team-aiusage-report"
```

macOS / Linux：

```bash
python3 aiusage.py merge \
  --input ~/Downloads/aiusage-exports \
  --out ~/Desktop/team-aiusage-report
```

也可以用网页看板：

```bash
streamlit run app.py
```

或者双击：

```text
Windows: run_dashboard.bat
macOS:   run_dashboard.command
```

输出：

```text
team-aiusage-report/
  team_inputs.jsonl
  team_daily_summary.csv
  team_project_summary.csv
  team_person_summary.csv
  summary.md
```

`summary.md` 包含：

- 总览
- 输入节奏拆解
- 按天统计
- 按工具统计
- 按项目统计
- 会话摘要
- 高 token 轮次
- 长任务轮次

## inputs.jsonl 字段

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

其中 `input_preview` 是每轮输入的规则摘要。团队 `summary.md` 里的“会话摘要”会把同一会话的多轮输入合并成一条简短摘要，方便快速判断当天每个会话大概在做什么。

## 关于 AI 摘要

当前版本默认不调用外部 AI，原因是每日导出要足够简单、稳定，不要求每个人配置 API Key。

如果后续要引入 AI 梳理，建议基于 `team_inputs.jsonl` 再做一个独立步骤：

1. 按 `person + date + project + session_id` 分组。
2. 每组只发送 `input_preview` 和必要统计字段，不发送完整 `input_text`。
3. 让 AI 输出 `session_title`、`work_summary`、`risk_or_blocker`、`deliverable`。
4. 回写一个 `ai_session_summary.md` 或 `ai_session_summary.jsonl`。

这样不会影响每日基础导出，也能控制隐私和 token 成本。

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
