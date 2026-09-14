# ProjectLens Tool Envelope 第一版

更新时间：2026-08-05

这份文档描述 ProjectLens 暴露给 Hermes runtime 的只读项目工具接口。它不是 `/project` 命令，也不是新的业务 Skill；它是 ProjectLens Kernel 给外部 Agent runtime 使用的安全工具层。

## 1. 当前定位

目标链路：

```text
Hermes Agent loop
-> 选择 projectlens_* 只读工具
-> ProjectLens 解析 ProjectSpace / RolePolicy / ChatVisibilityPolicy
-> ProjectLens 通过 ReadContextGateway / ToolGateway 执行
-> 返回 Tool Envelope
-> Hermes 基于 evidence_refs / citations 继续推理
```

边界：

- Hermes 可以决定要调哪个 `projectlens_*` 工具。
- ProjectLens 决定当前项目、用户、群聊能不能读。
- ProjectLens 返回 citation-ready 结果，不返回 secrets、完整 prompt 或未授权内容。
- Apply / PR / deploy / rollback / restart 仍关闭。

## 2. HTTP API

### `GET /api/v1/project-agent/tools`

返回当前对外暴露的 ProjectLens 工具目录。

当前正式工具面向证据检索、项目文件读取和知识缺口；`query_graph` 仅为旧集成保留的兼容目录项，已经不属于可调用能力：

```text
projectlens_search_context
projectlens_read_project_file
projectlens_authorized_evidence
projectlens_list_knowledge_gaps
```

旧名称 `projectlens_query_graph` 仍可能出现在目录中并带有 `deprecated: true`，但调用会被拒绝。项目关联使用结构化来源元数据、版本、状态和引用，不建设 GraphRAG。

不暴露内部 grep/list/range 工具，也不暴露任何写工具。

### `POST /api/v1/project-agent/tools/call`

请求示例：

```json
{
  "tool_name": "projectlens_search_context",
  "project": {
    "tenant_id": "demo",
    "project_id": "payment"
  },
  "user_id": "u1",
  "chat_id": "chat-1",
  "arguments": {
    "query": "create_order coupon",
    "limit": 3
  }
}
```

对于需求范围、需求状态、开发进度、测试状态、技术决策或负责人问题，优先传入 `fact_type`：

```json
{
  "query": "当前正式需求范围是什么？",
  "fact_type": "requirement_scope"
}
```

`result.state` 会返回 `confirmed`、`conflicted`、`unconfirmed`、`stale` 或 `unknown`，并携带选中来源、冲突来源、版本、更新时间和引用。

成功响应核心字段：

```json
{
  "ok": true,
  "tool_name": "projectlens_search_context",
  "internal_tool_name": "search_context",
  "tool_result_id": "...",
  "project": {
    "tenant_id": "demo",
    "project_id": "payment"
  },
  "summary": "search_context returned 3 authorized hit(s).",
  "citations": [],
  "evidence_refs": [],
  "unknowns": [],
  "audit_ref": {
    "tool_name": "projectlens_search_context",
    "internal_tool_name": "search_context",
    "allow_apply": false,
    "read_tool_names": ["search_context"]
  },
  "visibility_scope": {
    "role": "guest",
    "allowed_tools": ["search_context", "read_project_file", "list_knowledge_gaps"]
  }
}
```

错误响应仍使用 envelope：

```json
{
  "ok": false,
  "error_code": "UNKNOWN_TOOL",
  "message": "...",
  "agent_recovery_hint": "Retry with one of the projectlens_* read-only tools from GET /api/v1/project-agent/tools.",
  "audit_ref": {
    "allow_apply": false
  }
}
```

## 3. 第一版已经锁住的测试

测试文件：

```text
tests/test_project_agent_tools_api.py
```

覆盖：

- 工具目录只返回 5 个 `projectlens_*` 只读工具；
- `search_context` 返回标准 envelope；
- 未知工具和写工具被拒绝；
- `read_project_file` 受 ProjectSpace file_allowlist / RolePolicy / ChatVisibilityPolicy 约束；
- 同一接口可切换 `payment` / `crm` ProjectSpace。

相关回归：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_project_agent_tools_api.py tests/test_project_agent_ask_api.py tests/test_projectlens_mcp_server.py tests/test_hermes_projectlens_plugin.py tests/test_feishu_http_and_acl.py -q
```

当前验证结果：

```text
58 passed, 1 warning
```

## 4. Hermes / MCP 第二刀（接入准备）

### 发现

- HTTP：`GET /api/v1/project-agent/tools`
- MCP：`list_projectlens_tools(ProjectAgentToolService)`（与 HTTP 同源）
- Hermes plugin：`ProjectLensApiClient.list_tools()`；若 `ctx.register_tool` 可用则注册 5 个正式工具，否则只保留 helper，等待 Hermes runtime tool API（不改 Hermes core）

### 调用

- HTTP：`POST /api/v1/project-agent/tools/call`
- MCP：`call_projectlens_tool(...)` → `ProjectAgentToolService.call_tool`
- Hermes plugin：`ProjectLensApiClient.call_tool(payload)` → 同上 HTTP

### Debug / smoke

- `projectlens_ask_project`（MCP）与 `/project` ask（Hermes slash）仅 debug/smoke
- 正式 Agent tool loop 只允许 5 个 `projectlens_*` 只读工具

### 错误恢复

失败 envelope 含 `agent_recovery_hint`，便于 Hermes 自我修正（例如改用目录内工具、换 allowlisted path）。

### 仍不暴露

内部 `grep` / `list` / `range`、写工具、Apply / PR / deploy / rollback / restart。

## 5. 不要做

- 不新增 `/project-*` 作为正式入口；
- 不暴露内部 grep/list/range 工具给 Hermes；
- 不打开 Apply / PR / deploy / rollback / restart；
- 不让 Hermes plugin 或 Feishu adapter 直接查 EvidenceIndex / graph store / 文件系统；
- 不把 RoleView 当权限过滤器；
- 不把 Tool Envelope 做成新的 Skill 分类系统；
- 不改 Hermes core。

## 6. Cursor 下一步可以做什么

可以做：

- 给 tool envelope 增加更好的错误码和参数校验文案；
- 增加 payment / crm 多项目真实 fixture 验收；
- 等 Hermes runtime 正式暴露 `register_tool` 后，做真机 tool-loop 联调。

不要做：见上一节。
# 结构化项目事实查询

`projectlens_search_context` 除了普通全文检索，还支持可选参数
`fact_type`。当问题属于需求范围、需求状态、开发进度、测试状态、
技术决策或负责人时，Hermes 应优先使用该模式：

```json
{
  "query": "当前正式需求范围是什么？",
  "fact_type": "requirement_scope"
}
```

允许的 `fact_type`：

- `requirement_scope`
- `requirement_status`
- `development_progress`
- `test_status`
- `technical_decision`
- `owner`

返回的 `result.state` 取值为 `confirmed`、`conflicted`、
`unconfirmed`、`stale` 或 `unknown`。结果同时携带选中来源、候选来源、
冲突来源、版本、更新时间和引用。`unknown` 是正常业务结果，Hermes 不得
根据常识或历史对话补全缺失事实。
