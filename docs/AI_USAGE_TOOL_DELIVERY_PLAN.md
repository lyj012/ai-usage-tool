# AI Usage Tool 研发工作分析版交付计划书

更新时间：2026-06-17

## 1. 文档用途

本文档用于记录 AI Usage Tool 从 v2.0 到 v3.0 的后续交付计划。

后续每完成一个需求，必须回到本文档更新对应条目的状态、完成日期、提交记录和验证结果，避免需求做完但没有沉淀。

状态约定：

| 状态 | 含义 |
|---|---|
| TODO | 尚未开始 |
| DOING | 正在实现 |
| VERIFYING | 已实现，正在验证 |
| DONE | 已完成、已验证、已记录 |
| BLOCKED | 暂时阻塞，需要人工决策或外部条件 |
| DEFERRED | 已确认延期 |

完成标记格式：

```text
状态：DONE
完成日期：YYYY-MM-DD
提交：commit hash / commit message
验证：实际执行过的命令或人工验证说明
备注：关键风险、限制或后续改进
```

## 2. 总体方向

AI Usage Tool 不再只做 AI 使用量统计，而是面向个人研发工作分析：

- 每天自动汇总做了什么。
- 识别产生了什么代码和文档成果。
- 识别哪里发生返工或异常。
- 结合 AI 使用、Git 记录和人工复盘生成日报。
- 稳定后扩展到周报、月报、趋势分析。
- 最后再通过 MCP 让 ChatGPT 读取本地研发数据。

总体阶段：

| 阶段 | 名称 | 目标 | 状态 |
|---|---|---|---|
| v2.0 | 个人研发日报版 | 本地采集、关联、复盘、日报 | DOING |
| v2.0.1 | 稳定化版 | 加固数据口径、验证、异常提示 | TODO |
| v2.1 | 趋势分析版 | 周报、月报、历史趋势、对比看板 | TODO |
| v3.0 | ChatGPT / MCP 集成版 | MCP 读取本地日报和趋势数据 | TODO |

## 3. 当前已确认事实

代码仓库：

- 本地仓库：`C:\Users\lenovo\Desktop\ai-usage-tool`
- 远程仓库：`https://github.com/lyj012/ai-usage-tool`
- 当前主分支：`main`
- 最新已推送提交：`e5804ad docs: record MCP delivery commit`

已实现能力：

- `aiusage.py`
  - `init-config`
  - `export-workday`
  - `list-reports`
  - `show-report`
  - `topic-trends`
  - `export-week`
  - `export-month`
- `workreport.py`
  - 本地项目配置读取。
  - Git commit 采集。
  - merge commit 识别。
  - 文件变更统计。
  - 文件分类。
  - 模块识别。
  - AI 会话与 Git commit 规则关联。
  - 返工信号识别。
  - 技术主题识别。
  - 工作质量指标估算。
  - `daily-report.json` / `daily-report.md` 输出。
- `app.py`
  - Streamlit `v2 个人日报` 页面。
  - 每日人工复盘表单。
  - 生成 / 刷新当天日报。
  - 查看项目分布、Git 工作量、AI 关联、返工异常、技术主题、Markdown 报告和原始 JSON。
- 脚本入口
  - `run_workday_report.bat`：Windows v2 日报生成入口。
  - `run_dashboard.bat`：Windows 看板入口。
  - 旧版 zip 导出入口已删除，避免误点旧方向。
- 本地忽略
  - `aiusage-config.json`
  - `data/`
  - `.venv/`
  - `__pycache__/`

## 4. 当前约束和禁止项

近期不做：

- 不接入 OpenAI API 自动分析。
- 不做云端上传。
- 不做团队管理系统。
- 不做复杂权限体系。
- 不做 SaaS 化。
- 不自动读取 ChatGPT 聊天记录。
- 不把 MCP 提前做成核心分析逻辑。

提交约束：

- 不提交 `aiusage-config.json`。
- 不提交 `data/`。
- 不提交 `.venv/`。
- 不提交 `AGENTS.md`。
- 不提交无关本地文件。
- 不使用 `git push --force`。
- 提交前展示文件列表和关键变更摘要。

## 5. 业务流程设计

### 5.1 日常使用流程

1. 用户在本地配置 `aiusage-config.json`。
2. 用户正常使用 Codex / Claude Code 做研发。
3. 用户在本地项目产生 Git commit。
4. 用户双击 `run_workday_report.bat` 或在看板点击生成日报。
5. 系统读取 AI 使用记录。
6. 系统读取配置项目的本地 Git 历史。
7. 系统读取人工复盘。
8. 系统生成 `daily-report.json` 和 `daily-report.md`。
9. 用户在看板查看结果，并补充或修正复盘。

### 5.2 异常流程

| 异常 | 当前表现 | 后续要求 |
|---|---|---|
| 配置文件不存在 | 看板已提示 | CLI 也应给出明确提示 |
| 项目 path 不存在 | Git warning | 日报中保留 warning |
| path 不是 Git 仓库 | Git warning | 日报中保留 warning |
| 当天没有 AI 记录 | 输出 0 | 说明是未扫描到，不等于没有工作 |
| 当天没有 Git commit | 输出 0 | 说明可能是未提交或 path 配置不对 |
| AI-Git 关联为空 | 目前说明不足 | 输出未关联原因 |
| 返工识别为空 | 目前提示“暂未识别” | 保持，但说明只是规则未识别 |

## 6. 数据流设计

### 6.1 输入数据

| 数据 | 来源 | 用途 |
|---|---|---|
| AI 使用记录 | `~/.codex`、`~/.claude`、项目内 `.codex` / `.claude` | AI 输入、会话、token、时长 |
| Git 记录 | `aiusage-config.json` 中项目 `path` | commit、文件、行数、模块 |
| 人工复盘 | `data/reflections/YYYY-MM-DD.json` | 目标、结果、阻塞、验收、返工 |
| 项目配置 | `aiusage-config.json` | 多项目扫描、报告输出目录 |

### 6.2 输出数据

| 文件 | 路径 | 用途 |
|---|---|---|
| `ai-inputs.jsonl` | `data/reports/YYYY-MM-DD/` | 当天 AI 输入明细 |
| `git-commits.jsonl` | `data/reports/YYYY-MM-DD/` | 当天 Git commit 明细 |
| `git-file-changes.jsonl` | `data/reports/YYYY-MM-DD/` | 当天文件变更明细 |
| `associations.jsonl` | `data/reports/YYYY-MM-DD/` | AI 会话与 Git 关联 |
| `daily-report.json` | `data/reports/YYYY-MM-DD/` | 结构化日报 |
| `daily-report.md` | `data/reports/YYYY-MM-DD/` | 人工可读日报 |

## 7. 需求拆解和交付计划

### R1. v2.0 数据口径稳定化

状态：DONE

目标：

- 固化 `daily-report.json` 的核心字段含义。
- 明确事实字段和估算字段。
- 避免用户把估算指标理解为绝对工时或绩效结论。

应该怎么做：

1. 新增日报 schema 说明文档。
2. 梳理 `overview`、`ai_usage`、`git_workload`、`associations`、`rework_and_exceptions`、`technical_topics`、`quality_metrics` 字段。
3. 在 `quality_metrics.note` 和 Markdown 报告中明确“估算”含义。
4. 对空数据场景输出解释，而不是只显示 0 或空表。

验收标准：

- `daily-report.json` 每个一级字段有文档说明。
- Markdown 中能看出哪些指标是估算。
- 空 AI、空 Git、空关联时有可读说明。

验证方式：

```powershell
python -m py_compile aiusage.py app.py workreport.py
python .\aiusage.py export-workday --person lenovo --date 2026-06-15 --config .\aiusage-config.json --verbose
```

完成记录：

```text
状态：DONE
完成日期：2026-06-17
提交：801c6a9 feat: add v2.1 reports and validation
验证：python -m py_compile aiusage.py app.py workreport.py
验证：python .\aiusage.py export-workday --person lenovo --date 2026-06-15 --config .\aiusage-config.json --verbose
验证：python .\aiusage.py export-workday --person lenovo --date 2026-06-17 --config .\aiusage-config.json --verbose
验证：Streamlit 看板 http://localhost:8502 打开成功，v2 个人日报和 2026-06-17 日报区域可显示，console error 为 0，已截取最终页面截图。
备注：新增 docs\daily-report-schema.md 固化 schema 口径；Markdown 和看板已标注估算指标非绩效结论；空 AI、空 Git、空关联均补充可读提示。提交后需回填实际 commit hash。
```

### R2. AI-Git 关联增强

状态：DONE

目标：

- 让 AI 会话与 Git commit 的关联更可解释。
- 低置信度或未关联时能说明原因。

应该怎么做：

1. 保留现有时间、项目路径、关键词重合规则。
2. 增加文件路径关键词权重。
3. 增加同一项目但时间不匹配的说明。
4. 增加同一时间窗口但项目不匹配的说明。
5. `associations` 中保留 score、confidence、evidence。
6. 新增 `unmatched_ai_sessions` 或类似字段，记录未关联会话和原因。
7. 新增 `commit_association_summary`，说明哪些 commit 没有关联到 AI 会话。

验收标准：

- 有关联时能看到依据。
- 无关联时能看到原因。
- 不因为无关联导致日报看起来像系统失败。

验证方式：

- 用真实 2026-06-15 数据验证。
- 构造一个无 commit 日期验证。
- 构造一个 path 错误配置验证 warning。

完成记录：

```text
状态：DONE
完成日期：2026-06-17
提交：801c6a9 feat: add v2.1 reports and validation
验证：python -m py_compile aiusage.py app.py workreport.py
验证：python .\aiusage.py export-workday --person lenovo --date 2026-06-15 --config .\aiusage-config.json --verbose
验证：python .\aiusage.py export-workday --person lenovo --date 2026-06-17 --config .\aiusage-config.json --verbose
验证：使用临时错误 path 配置导出 2026-06-15，Git warning 和 commit_association_summary 可正常输出。
验证：Streamlit 看板 http://localhost:8502 打开成功，AI 关联 tab 可显示“未关联 AI 会话”和“Commit 关联概览”，console error 为 0。
备注：新增文件路径关键词加权；新增 unmatched_ai_sessions 和 commit_association_summary；Markdown 和看板已展示未关联原因。内置浏览器截图接口连续超时，已完成 DOM/console 验证但未成功保存最终截图。提交后需回填实际 commit hash。
```

### R3. 返工识别增强

状态：DONE

目标：

- 返工识别必须有依据。
- 减少主观结论。
- 识别更多真实返工场景。

应该怎么做：

1. 保留已有返工词规则。
2. 增加同一会话内多次相似输入检测。
3. 增加多个会话处理同一模块或同一文件的聚合说明。
4. 增加 fix / revert / rollback commit 的上下文展示。
5. 返工信号按 `low`、`medium`、`high` 标记。
6. Markdown 中展示依据，不只展示结论。

验收标准：

- 每条返工信号都有 `type`、`confidence`、`evidence`。
- 无返工时提示“规则未识别到明确信号”，而不是绝对没有返工。
- 返工信号不会只因为一个普通 `fix` 就过度判断为严重返工。

完成记录：

```text
状态：DONE
完成日期：2026-06-17
提交：801c6a9 feat: add v2.1 reports and validation
验证：python -m py_compile aiusage.py app.py workreport.py tests\test_workreport.py
验证：python -m unittest discover -s tests
验证：python .\aiusage.py export-workday --person lenovo --date 2026-06-15 --config .\aiusage-config.json --verbose
备注：保留原返工词规则；新增同一会话相似输入检测、fix/revert 上下文、模块重复变更聚合；每条返工信号保持 type/confidence/evidence。提交后需回填实际 commit hash。
```

### R4. 技术主题长期统计

状态：DONE

目标：

- 从“当天识别技术主题”升级到“跨天趋势统计”。
- 为 v2.1 周报、月报和 MCP 查询打基础。

应该怎么做：

1. 读取 `data/reports/*/daily-report.json`。
2. 聚合 `technical_topics`。
3. 统计：
   - 出现天数。
   - 相关任务数量。
   - 涉及项目。
   - 是否重复出现。
   - 是否值得专项学习。
4. 输出 `topic-trends.json` 或在周报中输出。

验收标准：

- 能按日期范围统计主题。
- 同一主题多天出现时能被识别。
- 缺失日报或损坏 JSON 不会导致整体失败。

完成记录：

```text
状态：DONE
完成日期：2026-06-17
提交：801c6a9 feat: add v2.1 reports and validation
验证：python .\aiusage.py topic-trends --from 2026-06-15 --to 2026-06-17 --config .\aiusage-config.json
验证：检查 data\reports\2026-06-15_2026-06-17\topic-trends.json，包含 topic_trends、repeated、worth_learning、warnings。
备注：基于已有 daily-report.json 聚合，不重新扫描原始 AI/Git；缺失日报会进入 warnings。提交后需回填实际 commit hash。
```

### R5. 历史日报查询基础

状态：DONE

目标：

- 为 v2.1 趋势分析提供统一历史读取能力。

应该怎么做：

1. 新增历史日报读取函数。
2. 支持列出已有日报日期。
3. 支持按日期读取日报。
4. 支持按日期范围读取日报。
5. 对缺失文件、损坏 JSON 给出 warning。

建议命令：

```text
list-reports
show-report --date YYYY-MM-DD
```

验收标准：

- 能列出 `data/reports/` 下已有日报。
- 能读取指定日期日报。
- 能区分“没生成”和“文件损坏”。

完成记录：

```text
状态：DONE
完成日期：2026-06-17
提交：801c6a9 feat: add v2.1 reports and validation
验证：python .\aiusage.py list-reports --config .\aiusage-config.json
验证：python .\aiusage.py show-report --date 2026-06-15 --config .\aiusage-config.json
备注：新增历史日报列出、单日报读取、日期范围读取函数；缺失文件和损坏 JSON 返回 warning。提交后需回填实际 commit hash。
```

### R6. v2.1 周报

状态：DONE

目标：

- 基于日报生成周报，不重新扫描原始 AI / Git 数据。

应该怎么做：

1. 读取一周内 `daily-report.json`。
2. 聚合 overview。
3. 聚合项目分布。
4. 聚合返工信号。
5. 聚合技术主题。
6. 输出：
   - `weekly-report.json`
   - `weekly-report.md`

建议命令：

```text
export-week --person lenovo --week 2026-W25 --config .\aiusage-config.json
```

验收标准：

- 周报能说明一周主要工作。
- 周报能列出高频项目、主题、返工信号。
- 缺失某天日报时给出 warning。

完成记录：

```text
状态：DONE
完成日期：2026-06-17
提交：801c6a9 feat: add v2.1 reports and validation
验证：python .\aiusage.py export-week --person lenovo --week 2026-W25 --config .\aiusage-config.json
验证：检查 data\reports\2026-W25\weekly-report.json 和 weekly-report.md，包含 overview、project_distribution、topic_trends、rework_trends、warnings。
备注：周报只读取日报；缺失日期保留 warning，不中断输出。提交后需回填实际 commit hash。
```

### R7. v2.1 月报

状态：DONE

目标：

- 基于日报或周报生成月度总结。

应该怎么做：

1. 读取一个月内日报。
2. 聚合项目趋势、AI 使用趋势、Git 工作量趋势。
3. 输出技术主题重复出现情况。
4. 输出返工趋势和改进建议。
5. 输出：
   - `monthly-report.json`
   - `monthly-report.md`

验收标准：

- 月报不重新扫描原始数据。
- 月报能展示项目、主题、返工、工作节奏趋势。

完成记录：

```text
状态：DONE
完成日期：2026-06-17
提交：801c6a9 feat: add v2.1 reports and validation
验证：python .\aiusage.py export-month --person lenovo --month 2026-06 --config .\aiusage-config.json
验证：检查 data\reports\2026-06\monthly-report.json 和 monthly-report.md，包含项目、主题、返工、工作节奏趋势。
备注：月报只读取日报；当前月份缺失日期较多，会明确写入 warnings。提交后需回填实际 commit hash。
```

### R8. Streamlit 看板升级

状态：DONE

目标：

- 从当天日报查看升级到历史分析和趋势查看。

应该怎么做：

1. 增加历史日报列表。
2. 增加日期范围选择。
3. 增加不同日期对比。
4. 增加不同项目对比。
5. 增加技术主题趋势页。
6. 增加返工趋势页。

验收标准：

- 用户能在看板选择某一天查看日报。
- 用户能选择日期范围查看趋势。
- 无历史数据时有明确空状态。
- 前端页面不静默显示 0 条误导用户。

验证方式：

- 运行 Streamlit。
- 用浏览器验证 v2 页面。
- 检查 console error。

完成记录：

```text
状态：DONE
完成日期：2026-06-17
提交：801c6a9 feat: add v2.1 reports and validation
验证：Streamlit 看板 http://localhost:8504 打开成功，v2 页面显示历史趋势、日期范围、日报列表、项目趋势、技术主题、返工趋势和趋势 Markdown，console error 为 0。
验证：修复 Streamlit “Missing Submit Button” 警告，复盘保存按钮可显示。
备注：历史趋势复用 workreport.py 聚合函数；无历史数据和缺失日期均有明确提示。提交后需回填实际 commit hash。
```

### R9. 最小验证体系

状态：DONE

目标：

- 避免后续改规则时破坏已验证口径。

应该怎么做：

1. 增加轻量 fixture。
2. 覆盖：
   - Git commit 分类。
   - merge 排除。
   - AI-Git 关联评分。
   - 返工信号。
   - 技术主题。
   - Markdown 输出。
3. 增加测试或验证脚本。

建议命令：

```powershell
python -m py_compile aiusage.py app.py workreport.py
python .\aiusage.py export-workday --person lenovo --date 2026-06-15 --config .\aiusage-config.json --verbose
```

后续可增加：

```powershell
python -m unittest
```

验收标准：

- 至少有一套可重复执行的本地验证流程。
- 验证结果能确认日报核心字段存在。
- 测试数据不包含真实隐私内容。

完成记录：

```text
状态：DONE
完成日期：2026-06-17
提交：801c6a9 feat: add v2.1 reports and validation
验证：python -m py_compile aiusage.py app.py workreport.py tests\test_workreport.py
验证：python -m unittest discover -s tests
验证：python .\aiusage.py export-workday --person lenovo --date 2026-06-15 --config .\aiusage-config.json --verbose
备注：新增 tests\test_workreport.py，覆盖文件分类、merge 排除、AI-Git 关联评分、返工信号、技术主题和 Markdown 输出；测试数据为合成数据，不含真实隐私。提交后需回填实际 commit hash。
```

### R10. v3.0 MCP Server

状态：DONE

目标：

- 让 ChatGPT 或其他 MCP Client 读取本地研发数据。
- MCP 不承担日报核心分析逻辑，只作为读取和写入入口。

前置条件：

- v2.0 数据口径稳定。
- v2.1 历史日报和趋势读取稳定。
- 本地报告 schema 已文档化。

第一版只读工具：

```text
get_daily_work_report
get_work_trend
search_work_records
get_git_activity
get_ai_session_details
```

第二版写入工具：

```text
save_daily_reflection
save_manual_work_item
save_technical_asset
```

应该怎么做：

1. 新增独立 `mcp_server.py`。
2. 只读取现有本地文件和稳定函数。
3. 生成日报时调用现有 `export-workday`，不复制逻辑。
4. 所有写入能力必须只写 `data/` 下本地文件。
5. 禁止自动提交 Git、自动上传云端、自动删除报告。

验收标准：

- MCP 工具能读取指定日期日报。
- MCP 工具能读取趋势数据。
- MCP 工具返回结构化 JSON。
- 没有报告时给出明确错误。
- 不需要 OpenAI API Key。

完成记录：

```text
状态：DONE
完成日期：2026-06-19
提交：f569e00 feat: add local MCP report server
验证：python -m py_compile aiusage.py app.py workreport.py mcp_server.py tests\test_workreport.py tests\test_mcp_server.py
验证：python -m unittest discover -s tests
验证：使用 Python subprocess 通过 stdio JSON-RPC 调用 initialize、tools/list、get_git_activity、get_work_trend、get_daily_work_report 缺失日期错误，均返回结构化 JSON。
备注：新增零依赖 mcp_server.py；当前只实现只读工具 get_daily_work_report、get_work_trend、search_work_records、get_git_activity、get_ai_session_details；不接入 OpenAI API、不上传云端、不自动提交 Git、不删除报告文件。提交后需回填实际 commit hash。
```

## 8. 推荐执行顺序

第一优先级：

1. R1 v2.0 数据口径稳定化
2. R2 AI-Git 关联增强
3. R3 返工识别增强
4. R9 最小验证体系

第二优先级：

1. R5 历史日报查询基础
2. R4 技术主题长期统计
3. R6 v2.1 周报
4. R8 Streamlit 看板升级

第三优先级：

1. R7 v2.1 月报
2. R10 v3.0 MCP Server

## 9. 每次需求交付流程

每次开始一个需求时：

1. 执行 `git status`。
2. 阅读本文档对应需求。
3. 确认影响范围。
4. 只修改当前需求相关文件。
5. 执行最小验证命令。
6. 更新本文档完成记录。
7. 提交前展示文件列表和关键变更摘要。
8. 经确认后再 `git add`、`git commit`、`git push`。

每次完成一个需求时，必须在对应需求下更新：

- 状态
- 完成日期
- commit
- 验证命令
- 剩余风险

## 10. 风险清单

| 风险 | 影响 | 控制方式 |
|---|---|---|
| AI-Git 关联误判 | 日报成果归因不准确 | 保留置信度和依据 |
| 返工识别过度判断 | 给用户造成误导 | 所有结论必须有 evidence |
| 估算指标被当成真实工时 | 误读工作质量 | 文档和报告明确“估算” |
| path 配置错误 | Git 数据为 0 | CLI 和看板都要明确提示 |
| 历史报告 schema 变化 | 趋势读取失败 | 固化 schema，兼容旧字段 |
| MCP 提前接入 | 底层数据不稳，输出不可信 | MCP 延后到 v3.0 |

## 11. 当前下一步建议

下一步建议启动：

```text
R1 v2.0 数据口径稳定化
```

原因：

- 它是 v2.1 趋势和 v3.0 MCP 的基础。
- 现在日报已经能生成，但字段含义、空数据说明、估算说明还需要加固。
- 先稳定数据契约，再做趋势和 MCP，后续返工最少。
