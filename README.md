# AI Usage Tool

一个本地小工具，用来导出和汇总 Codex / Claude Code 的每日使用记录。

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
  run_export_today.bat
  run_dashboard.bat
  run_export_today.command
  run_dashboard.command
```

普通 Windows 用户每天只需要双击：

```text
run_export_today.bat
```

macOS 用户每天双击：

```text
run_export_today.command
```

脚本会在桌面生成：

```text
ai-usage-用户名-YYYY-MM-DD.zip
```

然后把这个 zip 发给你。

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
.\run_export_today.bat
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
