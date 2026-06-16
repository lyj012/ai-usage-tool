# daily-report.json Schema 说明

更新时间：2026-06-17

本文档说明 AI Usage Tool v2.0 生成的 `daily-report.json` 核心字段口径。该文件用于个人研发复盘，不用于绩效结论。

## 总体原则

- 事实字段：来自本地 AI 会话记录、Git 历史、人工复盘表单或项目配置。
- 估算字段：由本地规则根据事实字段推导，字段名通常带有 `estimate`，或在说明中标注“规则估算”。
- 关联字段：AI-Git 关联只表示规则匹配结果，带有 `score`、`confidence`、`evidence`，不是绝对因果证明。
- 空数据：`0` 或空列表通常表示“未采集到或未匹配到”，不等于当天没有工作或 AI 没有参与。

## 一级字段

| 字段 | 类型 | 口径 |
|---|---|---|
| `schema_version` | string | 日报结构版本。当前为 `2.0`。 |
| `date` | string | 日报归属日期，格式 `YYYY-MM-DD`。 |
| `person` | string | 导出人，由命令行或看板输入。 |
| `overview` | object | 当天核心汇总。混合事实统计和少量估算字段。 |
| `project_distribution` | array | 按项目聚合的 AI、Git 和文件变更统计。 |
| `ai_usage` | object | 当天 AI 输入、会话、工具和 token 明细。 |
| `git_workload` | object | 当天本地 Git commit 与文件变更明细。 |
| `associations` | array | AI 会话与 Git commit 的规则关联结果。 |
| `unmatched_ai_sessions` | array | 未关联到 Git commit 的 AI 会话及原因。 |
| `commit_association_summary` | object | Git commit 与 AI 会话关联的汇总和未关联原因。 |
| `manual_reflection` | object | 人工复盘表单内容。 |
| `main_completed_items` | array | 从人工复盘、业务提交和高/中置信 AI-Git 关联推断的完成事项。 |
| `work_focus` | array | 从人工目标、commit message 和 AI 输入摘要推断的工作重点。 |
| `rework_and_exceptions` | array | 返工或异常信号，必须包含类型、置信度和依据。 |
| `technical_topics` | array | 当天识别到的技术主题信号。 |
| `quality_metrics` | object | 本地规则估算的个人复盘指标，不代表绩效结论。 |
| `today_outcome` | string | 今日成果摘要，优先使用人工复盘结果，否则根据 Git 和关联记录推断。 |
| `tomorrow_suggestions` | array | 基于验收、返工和技术主题生成的明日建议。 |
| `warnings` | array | 采集警告和空数据提示。 |

## overview

| 字段 | 类型 | 口径 |
|---|---|---|
| `ai_turn_count` | number | 事实统计：当天 AI 输入轮数。 |
| `ai_session_count` | number | 事实统计：当天 AI 会话数。 |
| `commit_count` | number | 事实统计：当天本地 Git commit 数。 |
| `business_commit_count` | number | 事实统计：排除 merge commit 后的 commit 数。 |
| `merge_commit_count` | number | 事实统计：merge commit 数。 |
| `files_changed` | number | 事实统计：按项目和路径去重后的变更文件数。 |
| `insertions` | number | 事实统计：Git numstat 新增行数，二进制文件按 0 处理。 |
| `deletions` | number | 事实统计：Git numstat 删除行数，二进制文件按 0 处理。 |
| `ai_active_seconds` | number | 统计/估算：AI 执行时长。Codex 优先原生耗时，Claude 按会话事件估算。 |
| `project_switch_count` | number | 估算：按 AI 输入时间顺序推断的项目切换次数。 |

## ai_usage

| 字段 | 类型 | 口径 |
|---|---|---|
| `by_tool` | object | 按工具聚合的 AI 输入轮数。 |
| `turns` | array | AI 输入明细摘要，包含工具、项目、会话、时间、估算或原生 AI 时长、token 和输入摘要。 |

`turns[].ai_active_seconds` 可能来自工具原生记录，也可能来自事件间隔估算。具体来源见原始 `ai-inputs.jsonl` 中的 `duration_source` 和 `is_estimated`。

## git_workload

| 字段 | 类型 | 口径 |
|---|---|---|
| `commits` | array | Git commit 明细，包含 hash、作者、时间、message、是否 merge、文件数、行数、分类和模块。 |
| `file_changes` | array | Git 文件变更明细，来自 `git log --numstat`。 |
| `by_category` | object | 按 `frontend`、`backend`、`sql`、`doc`、`config`、`test`、`other` 聚合文件数量。 |
| `top_modules` | object | 按路径推断的模块变更数量。 |

Git 工作量只读取 `aiusage-config.json` 中 `projects[].path` 指向的本地 Git 仓库，不从 GitHub URL 拉取远程数据。

## associations

每条关联表示一个 AI 会话与若干 Git commit 的规则匹配结果。

| 字段 | 类型 | 口径 |
|---|---|---|
| `session_id` | string | AI 会话 ID。 |
| `project` | string | AI 会话所属项目。 |
| `start_at` / `end_at` | string | 会话起止时间。 |
| `turn_count` | number | 会话输入轮数。 |
| `input_summary` | string | 会话输入摘要。 |
| `matched_commits` | array | 匹配到的 commit 列表，最多保留前 5 条。 |
| `best_confidence` | string | 最高匹配置信度：`high`、`medium`、`low`。 |
| `best_score` | number | 最高匹配分数。 |

匹配依据包括项目路径或项目名、提交时间窗口、输入内容与提交信息关键词重合、输入内容与文件路径关键词重合。文件路径关键词会单独加权，用于提高“提到模块/文件但 commit message 较短”场景的可解释性。

## unmatched_ai_sessions

每条记录表示一个没有达到关联阈值的 AI 会话。

| 字段 | 类型 | 口径 |
|---|---|---|
| `session_id` | string | AI 会话 ID。 |
| `project` | string | AI 会话所属项目。 |
| `start_at` / `end_at` | string | 会话起止时间。 |
| `turn_count` | number | 会话输入轮数。 |
| `input_summary` | string | 会话输入摘要。 |
| `reason` | string | 未关联原因，例如无 Git 提交、同项目但时间不匹配、时间接近但项目不匹配、最高分未达阈值。 |
| `best_candidate` | object/null | 最接近的 Git commit 候选及项目、时间、关键词匹配情况。 |

无关联只代表规则未匹配，不代表 AI 没有参与。

## commit_association_summary

该字段从 Git commit 视角说明哪些提交已关联或未关联到 AI 会话。

| 字段 | 类型 | 口径 |
|---|---|---|
| `total_commits` | number | 当天 Git commit 总数。 |
| `associated_commit_count` | number | 出现在 `associations[].matched_commits` 中的 commit 数。 |
| `unassociated_commit_count` | number | 未关联到 AI 会话的 commit 数。 |
| `associated_commits` | array | 已关联 commit 摘要。 |
| `unassociated_commits` | array | 未关联 commit 摘要和原因。 |

## rework_and_exceptions

返工和异常信号来自以下规则：

- 同一文件被多个 commit 反复修改。
- AI 输入中出现返工词。
- commit message 包含 fix、revert、rollback、修复、回滚等信号。
- 多个 AI 会话关联到同一模块。

每条记录应包含：

| 字段 | 类型 | 口径 |
|---|---|---|
| `type` | string | 信号类型。 |
| `project` | string | 项目名。 |
| `target` | string | 文件、模块、commit 或会话目标。 |
| `confidence` | string | 规则置信度：`high`、`medium`、`low`。 |
| `evidence` | array | 判断依据。 |

无返工信号只表示当前规则未识别，不代表绝对没有返工。

## technical_topics

技术主题由 AI 输入、commit message 和文件路径关键词识别。

| 字段 | 类型 | 口径 |
|---|---|---|
| `topic` | string | 技术主题名称。 |
| `related_task_count` | number | 命中该主题的信号数量。 |
| `evidence` | array | 命中的文本或路径摘要。 |
| `appeared_today` | boolean | 当天是否出现。 |
| `worth_learning` | boolean | 是否达到建议专项学习的规则阈值。 |

## quality_metrics

该字段全部用于个人复盘参考，不代表绝对工时、产出价值或绩效结论。

| 字段 | 类型 | 口径 |
|---|---|---|
| `note` | string | 质量指标免责声明。 |
| `ai_collaboration_seconds_estimate` | number | 统计/估算：AI 执行时长合计。 |
| `manual_review_seconds_estimate` | number | 估算：AI 完成后到下一次输入之间的接续/审查时间，单轮最多按 30 分钟计。 |
| `effective_work_seconds_estimate` | number | 估算：AI 协作时长 + 人工审查时长。 |
| `rework_ratio_estimate` | number | 规则估算：返工信号数相对于会话和 commit 数的比例，上限为 1。 |
| `project_switch_count` | number | 估算：AI 输入项目切换次数。 |
| `session_repeat_rate_estimate` | number | 规则估算：同一 commit 被多个会话关联的比例。 |
| `avg_turns_per_task` | number | 统计/估算：平均每个 AI 会话输入轮数。 |
| `high_consumption_low_output_tasks` | array | 规则提示：有 AI 输入但未关联到 Git 提交等场景。 |
| `high_value_tasks` | array | 高/中置信 AI-Git 关联的任务摘要。 |

## 空数据说明

- AI 输入为 0：可能是未扫描到记录、日期不对、时区不匹配，或工具记录路径未覆盖。
- Git 提交为 0：可能是当天未提交、项目 path 配置错误、本地仓库不是 Git 仓库，或提交时间不在所选日期内。
- AI-Git 关联为空：只表示当前规则未匹配，不代表 AI 没有参与。
- 返工为空：只表示当前规则未识别到明确信号，不代表绝对没有返工。
