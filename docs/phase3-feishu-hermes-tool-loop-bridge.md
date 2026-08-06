# Phase 3.1：飞书自然语言进入 Hermes LLM tool loop

日期：2026-08-06  
编码：UTF-8

这一阶段把飞书自然项目问题接到 **真实 Hermes `AIAgent` LLM/tool loop**（可注入 `HermesLoopRunner`）。不再使用规则 `_plan()`。

```text
Feishu natural project question
-> FeishuIngressRouter 判断为 PROJECT_QUESTION
-> FeishuHermesToolLoopBridge
-> HermesLoopRunner（生产：HermesAIAgentLoopRunner / AIAgent）
-> enabled_toolsets=["projectlens"] 仅 5 个 projectlens_* 只读工具
-> ProjectLens Tool Envelope（RolePolicy / ChatVisibility 在执行前解析）
-> citations / evidence_refs / tool_calls / audit_ref / allow_apply=false
-> Feishu card reply
```

## 1. 这一步解决什么

Phase 3.1 入口桥接已证明飞书自然语言能进入 tool-loop 路径。此前 bridge 内部仍是轻量规则 `_plan()`。

本轮目标（不是优化卡片，不是新增 Skill）：

1. 删除规则 `_plan()`；
2. 用可替换的 `HermesLoopRunner` 驱动工具选择；
3. 生产路径调用 Hermes `AIAgent`（`enabled_toolsets=["projectlens"]`）；
4. Hermes 不可用时返回明确失败，**不** silent fallback 到规则 planner。

## 2. 新增 / 变更代码

```text
src/project_lens/integrations/feishu/hermes_loop_runner.py
src/project_lens/integrations/feishu/hermes_tool_loop.py
src/project_lens/config.py
src/project_lens/main.py
tests/test_feishu_hermes_llm_tool_loop.py
```

核心类型：

```python
HermesLoopRunner          # Protocol
HermesAIAgentLoopRunner   # 生产：import AIAgent，不改 Hermes core
FeishuHermesToolLoopBridge(loop_runner=...)
```

开关（默认关闭，旧 workflow/read_agent 不受影响）：

```text
PROJECT_LENS_FEISHU_USE_HERMES_TOOL_LOOP=true
PROJECT_LENS_HERMES_REPO=C:\Users\Administrator\Desktop\hermes-agent-main
PROJECT_LENS_FEISHU_HERMES_PROVIDER=deepseek
PROJECT_LENS_FEISHU_HERMES_MODEL=deepseek-v4-flash
```

凭证继续使用进程环境中的 DeepSeek / Hermes 已有变量（如 `DEEPSEEK_API_KEY`），不写入仓库。

## 3. 边界

已做到：

- 飞书自然项目问题可进入 Hermes LLM/tool loop bridge；
- 工具选择来自 runner / AIAgent，不是 `_plan()`；
- 仅 5 个 `projectlens_*` 只读工具；
- Tool Envelope 在调用时已完成 ProjectSpace / RolePolicy / ChatVisibility；
- 返回含 `tool_calls` / `citations` / `evidence_refs` / `audit_ref` / `allow_apply=false`；
- Feishu adapter 不直连 EvidenceIndex / GraphStore / 文件系统；
- Hermes 不可用 → `ok=false` + 明确错误文案。

明确不做（本轮）：

- Hermes session/profile 深接；
- AgentRun 持久化；
- 新卡片 / 新 Skill；
- Apply / PR / deploy / rollback / restart；
- 修改 Hermes core。

## 4. 验收测试

```text
tests/test_feishu_hermes_llm_tool_loop.py
  - test_bridge_uses_injected_runner_not_rule_plan
  - test_bridge_reports_explicit_unavailable_without_rule_fallback
tests/test_feishu_http_and_acl.py
  - test_feishu_natural_project_question_uses_hermes_tool_loop_when_enabled
```

覆盖：

- 模块不再存在 `_plan` / `_needs_graph` / `_FILE_PATH_RE`；
- Fake runner 对「含 `.py` 路径」的问题只选 `projectlens_authorized_evidence`（与旧规则 search+read 不同）；
- envelope 含 citations / evidence_refs / tool_calls / `allow_apply=false`；
- Hermes 不可用时不伪装成功；
- 打开开关后不调用 `RunService.create`。

回归命令：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_feishu_hermes_llm_tool_loop.py tests/test_feishu_http_and_acl.py tests/test_hermes_projectlens_plugin.py tests/test_project_agent_tools_api.py tests/test_projectlens_mcp_server.py -q
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m ruff check src tests
```

结果：69 passed；ruff All checks passed。

## 5. 下一步（仍不要做卡片 / Skill）

1. Hermes session/profile 参与飞书项目问答（可选）；
2. 将 Hermes tool trace 映射为 ProjectLens 可审计事件；
3. 再考虑是否落成 `AgentRun`（RoleView / MemoryProposal）。
