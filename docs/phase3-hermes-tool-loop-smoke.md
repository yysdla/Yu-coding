# Phase 3：Hermes tool-loop smoke 地基

日期：2026-08-05

这份文档记录 Phase 3：不要继续把 `/project` 当作正式产品链路，而是建立并验证 Hermes 正式工具循环。

## 1. 这一阶段到底在做什么

Phase 3 的目标不是继续做卡片，也不是继续加 Skill，而是验证这条正式链路：

```text
Hermes Agent loop
-> 发现 ProjectLens 只读工具
-> 选择 projectlens_* 工具
-> 调用 ProjectLens /tools/call
-> ProjectLens Kernel 执行 ProjectSpace / RolePolicy / Gateway / Evidence 约束
-> 返回 citations / audit / allow_apply=false
```

这条链路和 `/project` 的关系：

- `/project` 只保留为 debug/smoke；
- `projectlens_ask_project` 只保留为 MCP debug/smoke；
- 正式 Agent loop 只能走 5 个 `projectlens_*` 只读工具。

## 2. 已落地的 smoke 入口

```text
src/project_lens/integrations/hermes_plugin/smoke.py
```

| 入口 | 作用 |
|------|------|
| `run_projectlens_tool_loop_smoke` | HTTP：`GET /tools` → `POST /tools/call`（不经 ask） |
| `run_hermes_projectlens_tool_registry_smoke` | 真 Hermes PluginManager 发现 tools + `registry.dispatch`（不改 Hermes core） |

Hermes plugin 注册使用真实 kwargs API：

```python
ctx.register_tool(
    name=...,
    toolset="projectlens",
    schema={...},  # OpenAI-style
    handler=...,   # handler(args: dict) -> JSON envelope
    description=...,
)
```

## 3. 只允许的正式工具

```text
projectlens_search_context
projectlens_read_project_file
projectlens_query_graph
projectlens_authorized_evidence
projectlens_list_knowledge_gaps
```

禁止进入正式 tool loop：`projectlens_ask_project`、`/project`、grep/list/range、apply/pr/deploy/rollback/restart。

## 4. Live 联调命令

先启动 ProjectLens API：

```powershell
cd C:\Users\Administrator\Desktop\project-lens
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m uvicorn project_lens.main:app --port 8010
```

（若 8000 已被占用，用 8010 或其它空闲端口，并把下方 `--api-base-url` 改成对应端口。）

### 4.1 payment：三工具

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -c "from project_lens.integrations.hermes_plugin.smoke import run_projectlens_tool_loop_smoke; r=run_projectlens_tool_loop_smoke(api_base_url='http://127.0.0.1:8010/api/v1', tenant_id='demo', project_id='payment', chat_id='oc_payment', tool_calls=({'tool_name':'projectlens_search_context','arguments':{'query':'create_order coupon','limit':3}},{'tool_name':'projectlens_read_project_file','arguments':{'path':'src/order_service.py'}},{'tool_name':'projectlens_query_graph','arguments':{'limit':5}})); print(r.output); raise SystemExit(0 if r.ok else 1)"
```

### 4.2 crm：ProjectSpace 切换

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -c "from project_lens.integrations.hermes_plugin.smoke import run_projectlens_tool_loop_smoke; r=run_projectlens_tool_loop_smoke(api_base_url='http://127.0.0.1:8010/api/v1', tenant_id='demo', project_id='crm', chat_id='oc_crm', tool_calls=({'tool_name':'projectlens_search_context','arguments':{'query':'contact','limit':3}},)); print(r.output); raise SystemExit(0 if r.ok else 1)"
```

### 4.3 Hermes tool registry（不经 /project）

确保 `hermes-agent-main/plugins/projectlens` 已导出并指向本仓库 `src`。Hermes `config` 通过临时 `HERMES_HOME` 写入 `plugins.enabled: [projectlens]`。

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m project_lens.integrations.hermes_plugin.smoke `
  --hermes-tool-registry `
  --hermes-repo C:\Users\Administrator\Desktop\hermes-agent-main `
  --api-base-url http://127.0.0.1:8010/api/v1 `
  --tenant-id demo `
  --project-id payment `
  --question "create_order coupon"
```

预期：发现 5 个 `projectlens_*`；`registry.dispatch projectlens_search_context` 成功；`allow_apply=false`；含 citations/evidence_refs。

### 4.4 Hermes runtime 启用 toolset

在 Hermes 平台/CLI 配置中启用 toolset `projectlens`（与内置 toolset 并列）。插件需在 `plugins.enabled` 中包含 `projectlens`。LLM tool loop 只会看到 formal 只读工具，不会把 `/project` 当主路径。

### 4.5 本机 live 验收结果（2026-08-05）

```text
payment tool-loop: ok (search_context / read_project_file / query_graph), allow_apply=false
crm tool-loop: ok (search_context), project_id=crm
Hermes registry: discovered 5 projectlens_* tools; dispatch search_context ok; citations/evidence_refs present
formal path did not use /project-agent/ask
```

## 5. 测试

```text
tests/test_hermes_projectlens_plugin.py
```

覆盖：

- HTTP tool-loop smoke（发现 + 调用 + 拒 debug/write + ProjectSpace 切换）
- Hermes kwargs `register_tool(toolset/schema/handler)`
- handler(args) → POST `/tools/call`
- Hermes PluginManager registry smoke（本机有 hermes-agent-main 时）

回归：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_hermes_projectlens_plugin.py tests/test_project_agent_tools_api.py tests/test_projectlens_mcp_server.py -q
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m ruff check src tests
```

## 6. 验收标准

- [x] Hermes 能发现 5 个 ProjectLens read-only tools（HTTP catalog + Hermes registry）
- [x] 至少能调用一个 `projectlens_*` 工具（HTTP smoke + registry.dispatch）
- [x] 返回 envelope 含 citations / evidence_refs / audit_ref
- [x] `audit_ref.allow_apply=false`
- [x] 正式链路不依赖 `/project`
- [x] 不改 Hermes core；Apply / PR / deploy / rollback / restart 仍关闭
- [ ] 飞书自然语言入口进入 tool loop（本轮不做，下一步）

## 7. 不要做什么

- 不要改 Hermes core；
- 不要新增 `/project-*` 正式入口；
- 不要继续加固定业务 Skill / 卡片样式；
- 不要让 Hermes plugin / Feishu adapter 直接访问 EvidenceIndex、GraphStore 或文件系统；
- 不要打开 Apply / PR / deploy / rollback / restart。
