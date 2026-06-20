# ChatGPT Remote MCP 接入计划（方案 B）

## 1. 文档目标

本文档规划 AI Usage Tool 从“本地 stdio MCP Server”升级为“ChatGPT 可连接的 Remote MCP Server”的实施路径。

目标不是把项目做成 SaaS，也不是上传完整研发数据到云端，而是在用户明确启动本地服务并授权连接时，让 ChatGPT 读取本机已经生成的 `daily-report.json`、趋势数据和相关明细。

## 2. 当前官方接入要求

截至 2026-06-19，ChatGPT 自定义 MCP / Apps 接入的关键要求如下：

- ChatGPT 侧连接的是可访问的 HTTPS MCP endpoint，不是本地 `stdio` 命令。
- Apps SDK 使用 MCP 暴露工具给 ChatGPT；UI 组件是可选项，本项目第一阶段不做 UI widget。
- 部署时需要稳定 HTTPS endpoint，官方部署文档强调 `/mcp`、TLS、日志和故障排查能力。
- ChatGPT 自定义 connector / app 需要在 ChatGPT 设置中启用 developer mode 后创建。
- 如接入 OAuth，ChatGPT 会使用 `https://chatgpt.com/connector/oauth/{callback_id}` 这类回调地址。

参考：

- OpenAI Apps SDK Quickstart: https://developers.openai.com/apps-sdk/quickstart
- Build MCP server: https://developers.openai.com/apps-sdk/build/mcp-server
- Deploy your app: https://developers.openai.com/apps-sdk/deploy
- Connect from ChatGPT: https://developers.openai.com/apps-sdk/deploy/connect-chatgpt
- Authentication: https://developers.openai.com/apps-sdk/build/auth
- Developer mode and MCP apps in ChatGPT: https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt

## 3. 当前项目事实

当前已实现：

- `mcp_server.py`：本地 stdio JSON-RPC MCP Server。
- 只读工具：
  - `get_daily_work_report`
  - `get_work_trend`
  - `search_work_records`
  - `get_git_activity`
  - `get_ai_session_details`
- 数据来源：
  - `aiusage-config.json`
  - `data/reports/YYYY-MM-DD/daily-report.json`
  - `data/reports/YYYY-MM-DD/ai-inputs.jsonl`
  - `data/reports/YYYY-MM-DD/git-commits.jsonl`
  - `data/reports/YYYY-MM-DD/git-file-changes.jsonl`
  - `data/reports/YYYY-MM-DD/associations.jsonl`
- 当前 MCP 不上传云端、不调用 OpenAI API、不自动提交 Git、不删除报告文件。

当前不能直接接入 ChatGPT 的原因：

- ChatGPT 不能直接启动本地 `python mcp_server.py`。
- 当前 server 没有 HTTP transport。
- 当前 server 没有认证层。
- 当前 server 没有公网或可被 ChatGPT 访问的 HTTPS 地址。

## 4. 业务目标

用户在 ChatGPT 中可以问：

- “读取我 2026-06-15 的研发日报。”
- “总结我最近一周主要做了什么。”
- “最近哪些任务有返工信号？”
- “这个项目最近的技术主题是什么？”
- “哪天 Git 提交多但 AI-Git 关联低？”

系统应返回基于本地日报的结构化数据和可读解释。

不做：

- 不读取 ChatGPT 历史聊天。
- 不自动扫描未生成日报的原始 AI 会话。
- 不上传整个 `data/` 目录。
- 不做团队权限、SaaS 多租户、在线账号体系。
- 不做写入型工具，第一阶段只读。
- 不把 MCP 变成日报核心分析逻辑。

## 5. 推荐架构

```text
ChatGPT
  |
  | HTTPS /mcp
  v
公网 HTTPS 入口
  |
  | tunnel 或部署平台反向代理
  v
本机 Remote MCP HTTP Server
  |
  | 复用现有 report loader / tool handler
  v
本地 aiusage-config.json + data/reports/
```

第一阶段建议使用“本机 HTTP Server + 临时 HTTPS 隧道”：

- 用户主动启动本地服务。
- 用户主动启动 tunnel。
- ChatGPT connector 填入 tunnel 的 HTTPS `/mcp` 地址。
- 服务停止后，ChatGPT 不能再读取本地数据。

这样最符合当前“个人本地工具”定位，避免过早引入云端存储和 SaaS 化。

## 6. 方案拆分

### 6.1 阶段 B1：HTTP Remote MCP 最小版

目标：

- 在不破坏现有 stdio MCP 的前提下，新增 Remote-safe MCP HTTP 能力。
- ChatGPT 最终接入必须使用标准 Streamable HTTP MCP endpoint。
- 复用现有工具定义和 handler。
- 保持只读。

建议新增：

- `mcp_http_server.py`
- 或在 `mcp_server.py` 中拆出 transport 层：
  - `handle_request()` 保持复用。
  - `serve_stdio()` 保持现有能力。
  - 新增 `serve_http()`。

推荐保守做法：

- 先新增独立 `mcp_http_server.py`，减少影响现有 Codex stdio 接入。
- 后续稳定后再考虑合并 transport。

技术选择：

- `mcp_chatgpt_server.py` 使用 MCP Python SDK 的 Streamable HTTP transport，作为 ChatGPT 自定义 MCP 的推荐入口。
- `mcp_http_server.py` 使用标准库 `http.server`，只保留为本地 HTTP JSON-RPC 调试入口和 Remote-safe 边界测试，不再声明可直接接入 ChatGPT。
- 第一版不引入数据库、不引入后台任务、不引入云服务 SDK。

本地 HTTP JSON-RPC 调试验收：

- `python mcp_http_server.py --host 127.0.0.1 --port 8765`
- `POST http://127.0.0.1:8765/mcp` 可完成：
  - `initialize`
  - `tools/list`
  - `tools/call get_daily_work_report`
  - 缺失日期返回结构化错误

ChatGPT Streamable HTTP 验收：

- `python -m pip install -e ".[chatgpt]"`
- `python .\mcp_chatgpt_server.py --host 127.0.0.1 --port 8765 --config .\aiusage-config.json`
- tunnel 暴露 `http://127.0.0.1:8765`
- ChatGPT connector 填入 `https://<tunnel-host>/mcp`
- ChatGPT `Scan Tools` 能扫描出工具并调用日报、趋势工具。

### 6.2 阶段 B2：本地访问控制

目标：

- 防止 tunnel 地址泄露后被任意读取本地日报。

第一版认证建议：

- 使用本地环境变量或启动参数设置 bearer token。
- HTTP 请求必须带：

```text
Authorization: Bearer <token>
```

规则：

- token 不写入仓库。
- token 不写入 README 示例的真实值。
- token 可通过 `AIUSAGE_MCP_TOKEN` 注入。
- 未配置 token 时，默认只允许本机访问。
- 一旦配置 token，所有 `/mcp` 请求都必须带正确 token，包括经 tunnel 转发后看起来来自 `127.0.0.1` 的请求。

说明：

- 上述 bearer token 只适用于 `mcp_http_server.py` 这个本地调试入口。
- ChatGPT 自定义 MCP 的长期认证路径是 OAuth 2.1；静态 `AIUSAGE_MCP_TOKEN` 不能作为 ChatGPT 生产认证方案。
- 当前 `mcp_chatgpt_server.py` 第一版建议只用于用户本人主动启动的本地开发 tunnel，认证方式选择无认证 / None。

验收：

- 无 token 请求返回 401。
- 错 token 请求返回 401。
- 正确 token 请求返回 MCP JSON-RPC 响应。

### 6.3 阶段 B3：HTTPS 隧道接入 ChatGPT

目标：

- 用临时 HTTPS 地址让 ChatGPT 访问本机 MCP。

可选隧道：

- Cloudflare Tunnel
- ngrok
- VS Code / Dev Tunnel
- 其他能提供稳定 HTTPS 转发的工具

建议先用临时 tunnel 验证，不急着固定域名。

ChatGPT Streamable HTTP 示例流程：

```powershell
python -m pip install -e ".[chatgpt]"
```

另一个终端启动 tunnel：

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

复制 tunnel hostname 后启动 MCP server：

```powershell
$env:MCP_ALLOWED_HOSTS = "<tunnel-host>"
$env:MCP_ALLOWED_ORIGINS = "https://<tunnel-host>"
python .\mcp_chatgpt_server.py --host 127.0.0.1 --port 8765 --config .\aiusage-config.json
```

ChatGPT connector 填：

```text
https://<tunnel-host>/mcp
```

ChatGPT connector 第一版认证选择无认证 / None。若要长期或公开暴露，必须补 OAuth 2.1 后再使用。

验收：

- ChatGPT developer mode 中可以创建 connector。
- ChatGPT 可以列出工具。
- ChatGPT 可以读取指定日期日报。
- 关闭本地服务后，连接不可用。

### 6.4 阶段 B4：ChatGPT 操作说明

目标：

- 给普通用户一份“按哪个按钮”的说明。

文档建议新增：

- `docs/chatgpt-connector-user-guide.md`

内容：

1. 启动本地日报服务。
2. 启动 HTTPS tunnel。
3. 打开 ChatGPT。
4. 进入 `Settings`。
5. 进入 `Apps & Connectors`。
6. 打开 developer mode。
7. 创建 custom MCP connector。
8. 填入 `/mcp` HTTPS 地址。
9. 在聊天中通过 `+` / `More` 选择 connector。
10. 用示例问题验证。

### 6.5 阶段 B5：生产化可选项

仅在本地 tunnel 验证稳定后再做：

- 固定域名。
- 正式 OAuth。
- 操作日志。
- request id / trace id。
- rate limit。
- 工具级 allowlist。
- 只暴露脱敏字段。
- UI widget。
- 发布到 workspace。

这些不进入第一版。

## 7. 接口设计

### 7.1 HTTP endpoint

```text
POST /mcp
Content-Type: application/json
```

ChatGPT 推荐入口由 MCP SDK 的 Streamable HTTP transport 管理请求、响应和会话行为。本项目工具执行层仍复用 `mcp_server.handle_request(remote=True)`。

### 7.2 健康检查

建议新增：

```text
GET /health
```

返回：

```json
{
  "name": "ai-usage-tool",
  "version": "3.0.0",
  "transport": "http",
  "status": "ok"
}
```

`/health` 不返回任何日报数据。

## 8. 数据安全设计

敏感数据边界：

- `ai-inputs.jsonl` 可能含用户原始输入。
- `daily-report.json` 可能含 commit message、文件路径、工作内容。
- `aiusage-config.json` 含本地路径。
- `data/` 不应整体上传。

控制策略：

- 第一版只读。
- 默认只读取 `data/reports/`。
- 不提供任意文件读取。
- 不允许工具参数传入任意绝对 data_dir。
- `config` 参数应限制为当前工作目录下的配置，或后续改为服务启动时固定配置路径。
- tunnel 必须配 token。
- 日志不打印完整报告内容。
- 错误信息不暴露完整本地路径，或只在 verbose 模式暴露。

## 9. 风险和处理

| 风险 | 影响 | 处理 |
|---|---|---|
| tunnel URL 泄露 | 外部可读取本地日报 | 强制 bearer token |
| ChatGPT 读取过多原始输入 | 隐私泄露 | 默认工具优先返回日报摘要，原始 turns 可限制字段 |
| 任意 config 路径 | 被读取非预期文件 | 限制 config 路径范围 |
| 本地服务常驻 | 增加暴露面 | 默认手动启动，用完关闭 |
| OAuth 复杂度过早引入 | 延误最小验证 | 第一版 bearer token，OAuth 延后 |
| ChatGPT remote MCP 协议细节变化 | 接入失败 | 先按官方文档做最小 HTTP，再用 ChatGPT 实测调整 |
| Windows 编码问题 | 中文乱码 | 统一 UTF-8 输出和 HTTP `charset=utf-8` |

## 10. 验证计划

本地验证：

```powershell
python -m py_compile aiusage.py app.py workreport.py mcp_server.py mcp_http_server.py
python -m unittest discover -s tests
```

stdio 回归：

```powershell
python .\mcp_server.py
```

HTTP smoke test：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/mcp `
  -Headers @{ Authorization = "Bearer $env:AIUSAGE_MCP_TOKEN" } `
  -ContentType "application/json" `
  -Body '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

ChatGPT 验证：

- connector 能创建。
- connector 能显示工具。
- 能读取 `2026-06-15` 日报。
- 能查询一周趋势。
- 无 token 时无法读取。
- 关闭本地 server 后 ChatGPT 调用失败且提示可理解。

## 11. 推荐实施顺序

1. 新增 `mcp_http_server.py`，只实现 `/health` 和 `/mcp`。
2. 复用 `mcp_server.handle_request()`，不复制工具逻辑。
3. 加 bearer token 校验。
4. 加 HTTP smoke tests。
5. 本地启动服务验证。
6. tunnel 验证。
7. ChatGPT developer mode connector 验证。
8. 写 `docs/chatgpt-connector-user-guide.md`。
9. 再决定是否做 OAuth / 固定域名 / UI widget。

## 12. 第一版验收标准

必须满足：

- 不影响 Codex 当前 `ai_usage_tool` stdio MCP。
- ChatGPT 可以通过 HTTPS `/mcp` 调用只读工具。
- 无授权不能读取数据。
- 不上传 `data/`。
- 不提交 `aiusage-config.json`。
- 不提交 tunnel token。
- 所有工具错误都返回结构化 JSON。

暂不要求：

- OAuth。
- UI widget。
- 多用户。
- 云端部署。
- 团队权限。
- 写入日报。
- 自动生成日报。

## 13. 第一版实现记录

状态：HARDENED / 未完成 ChatGPT 端到端验证

完成日期：2026-06-19；2026-06-20 补充 ChatGPT Streamable HTTP 入口

已实现：

- 新增 `mcp_chatgpt_server.py`，使用 MCP Python SDK Streamable HTTP transport，作为 ChatGPT 自定义 MCP 推荐入口。
- `mcp_chatgpt_server.py` 复用 `mcp_server.handle_request(remote=True)`，不复制日报、趋势或搜索业务逻辑。
- ChatGPT 入口通过 `--config` 使用服务端固定配置，不暴露调用方 `config` 参数。
- ChatGPT 入口返回 Remote-safe 脱敏结果，不暴露原始 `session_id`，使用 `session_ref`。
- `mcp_server.py` stdio 启动入口新增 `--remote-safe --config`，真实启动链路可以启用 Remote-safe schema 和脱敏结果。
- 新增 `mcp_http_server.py`，独立于 `mcp_server.py` 的 stdio transport。
- `GET /health` 只返回 `status: ok`，不返回版本、工具数量或日报数据。
- `POST /mcp` 接收 JSON-RPC 请求并复用 `mcp_server.handle_request(remote=True)`。
- 复用 `TOOLS`、`SERVER_NAME`、`SERVER_VERSION`，没有复制 MCP 工具业务逻辑。
- 启动参数支持 `--host` 和 `--port`，默认 `127.0.0.1:8765`。
- bearer token 从 `AIUSAGE_MCP_TOKEN` 读取。
- 未配置 token 时，本机无 token 可访问，方便本地 smoke test；配置 token 后，本机和 tunnel 转发请求都必须带正确 bearer token。
- Remote HTTP MCP 忽略调用方传入的 `config`，只使用服务端 `AIUSAGE_MCP_CONFIG` 或默认 `aiusage-config.json`。
- Remote HTTP MCP 返回统一脱敏视图，移除本地路径、完整 AI 输入、邮箱、完整 session ID、完整 hash 等远程不必要字段，并对 URL、常见 token/key/password 形态做文本脱敏。
- Remote `tools/list` 不暴露本地 stdio 专用的 `config` 参数，也不暴露原始 `session_id` 查询参数；跨工具会话查询使用 `session_ref`。
- HTTP 增加请求体大小、Content-Length、未知方法、query path、no-store、nosniff、Accept、Origin 和 `MCP-Protocol-Version` 处理；`GET /mcp` 明确返回不支持 SSE / Streamable HTTP。
- 每个 MCP 工具的 `outputSchema` 描述各自的 `structuredContent`，而不是整个 ToolResult 包装结构。
- HTTP 响应统一 `application/json; charset=utf-8`。
- 单元测试覆盖 initialize、tools/list、tools/call get_daily_work_report、缺失日期结构化错误、无 token、错 token、正确 token 和 localhost 无 token。
- README 已补充 ChatGPT Streamable HTTP MCP 第一版启动、tunnel、认证限制和只读边界说明。

验证：

```powershell
python -m py_compile aiusage.py app.py workreport.py mcp_server.py mcp_http_server.py mcp_chatgpt_server.py tests\test_workreport.py tests\test_mcp_server.py tests\test_mcp_http_server.py tests\test_mcp_chatgpt_server.py
python -m unittest discover -s tests
```

HTTP smoke test：

```powershell
$env:AIUSAGE_MCP_TOKEN = "test-token"
python .\mcp_http_server.py --host 127.0.0.1 --port 8765
```

另一个进程验证：

- `GET /health`
- `POST /mcp initialize`
- `POST /mcp tools/list`
- `POST /mcp tools/call get_daily_work_report`
- localhost 无 token 允许访问
- 错 token 返回 401
- 正确 token 返回 MCP JSON-RPC 响应

真实状态和剩余项：

- 本地自动化测试已执行：`python -m py_compile aiusage.py app.py workreport.py mcp_server.py mcp_http_server.py mcp_chatgpt_server.py tests\test_workreport.py tests\test_mcp_server.py tests\test_mcp_http_server.py tests\test_mcp_chatgpt_server.py`。
- 本地自动化测试已执行：`python -m unittest discover -s tests -v`。
- `python .\mcp_chatgpt_server.py --help` 已确认启动参数存在。
- 本地环境尝试安装 MCP SDK 可选依赖失败，原因是 pip 访问 PyPI 代理握手超时，无法下载 `setuptools` / `mcp`；未完成 `mcp_chatgpt_server.py` 实际启动验证。
- 真实 HTTPS tunnel 验证。
- ChatGPT developer mode connector 实测。
- MCP Inspector 或其他标准 Remote MCP 客户端验证。
- OAuth、固定域名、rate limit、工具级 allowlist 和访问日志仍属于后续生产化选项。
- `mcp_http_server.py` 是本项目的受限 HTTP JSON-RPC transport，不应文档化为“已完整兼容 ChatGPT Remote MCP”。
- `mcp_chatgpt_server.py` 已切到标准 Streamable HTTP transport，但需要 MCP Inspector / ChatGPT connector 真实验证后再更新兼容性结论。

## 14. 待确认问题

实施前需要确认：

1. 你希望第一版 tunnel 用 Cloudflare Tunnel、ngrok，还是其他工具？
2. ChatGPT 账号当前是否能创建 custom MCP connector？
3. 第一版是否允许 ChatGPT 读取 `ai_usage.turns` 的原始输入摘要，还是只允许读日报汇总？
4. 是否需要固定端口，例如 `8765`？

默认建议：

- 使用本地 `127.0.0.1:8765`。
- 先用临时 tunnel。
- 只读。
- bearer token。
- 不做 OAuth。
