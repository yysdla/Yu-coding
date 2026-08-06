# ProjectLens read_agent 飞书灰度测试 Runbook

更新时间：2026-08-05

这份文档只解决一个问题：如何把 ProjectLens 从旧的 `workflow` 链路切到 `read_agent` 链路，并在飞书项目群里验证“自然语言项目问题 -> 只读调查 Agent -> 带引用回答”已经跑通；以及如何把 stub planner 安全灰度到 OpenAI-compatible live provider。

## 1. 当前要验证的产品效果

正式入口不是 `/project`，而是飞书项目群里的自然语言提问。

目标链路：

```text
飞书自然语言项目问题
-> FeishuIngressRouter 标记 entry_mode=natural_project_question
-> RunService(agent_mode=read_agent)
-> ProjectInvestigationAgent
-> 只读工具调用 search_context / read_project_file / query_graph 等
-> AnswerDraft
-> CitationVerifier / Unknown enforcement
-> Feishu AnswerView 卡片
```

`/project ...` 仍然保留，但只作为 debug/smoke 入口；创建 run 前会剥离 `/project`，并记录 `entry_mode=debug_slash`。

## 2. 必须保持的安全边界

- 只读：不打开 Apply / PR / deploy / rollback / restart。
- 飞书 adapter 不直接访问 EvidenceIndex、graph store、文件系统。
- 工具调用必须走 ProjectLens Gateway；live provider 只能看到只读 tool schema。
- fact 必须有 Evidence / ToolResult citation。
- 无引用结论必须降级为 inference / unknown。
- ProjectMemory 写入仍需审批。
- API key、Feishu secret 不写入文档正文和代码仓库；只放本机 `.env`。

## 3. `.env` 最小配置（先 stub）

先从 stub read_agent 开始，不急着接真实 LLM：

```text
PROJECT_LENS_AGENT_MODE=read_agent
PROJECT_LENS_MODEL_PROVIDER=stub
PROJECT_LENS_MODEL_LIVE=false
PROJECT_LENS_MODEL_FALLBACK_TO_STUB=true
PROJECT_LENS_CONVERSATION_STORE=sqlite
PROJECT_LENS_DATABASE_PATH=project_lens.db
```

飞书测试群需要：

```text
PROJECT_LENS_FEISHU_VERIFICATION_TOKEN=<和飞书后台一致>
PROJECT_LENS_FEISHU_SIGNING_SECRET=<建议配置>
PROJECT_LENS_FEISHU_APP_ID=<真实飞书应用 app id>
PROJECT_LENS_FEISHU_APP_SECRET=<真实飞书应用 app secret>
PROJECT_LENS_FEISHU_PROJECT_BINDINGS={"bindings":[{"tenant_key":"<tenant_key>","chat_id":"<chat_id>","project_id":"payment","service":"order-service","environment":"production"}]}
```

如果还没接真实飞书 outbound，可以先留空 `APP_ID/APP_SECRET`，系统会使用 recording messenger，适合本地测试。

## 4. 启动前自检

自动化入口（不联网、不消耗真实 API）：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m ruff check src tests
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_investigation_live_provider.py tests/test_project_investigation_agent.py tests/test_feishu_http_and_acl.py tests/test_feishu_read_agent_card_ux.py -q
```

补充 stub / free-question 回归：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_feishu_ingress.py tests/test_read_agent_free_questions.py tests/test_read_agent_multi_turn.py -q
```

启动服务：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m uvicorn project_lens.main:app --host 0.0.0.0 --port 8000
```

访问状态接口：

```text
GET /api/v1/integrations/feishu/status
```

重点看：

```json
{
  "agent_mode": "read_agent",
  "ask_agent_mode": "read_agent",
  "model_provider": "stub",
  "model_live": false,
  "model_fallback_to_stub": true,
  "model_openai_api_key_configured": false,
  "binding_count": 1,
  "webhook_path": "/api/v1/feishu/events"
}
```

如果 `agent_mode` 不是 `read_agent`，说明 `.env` 没生效或服务没重启。状态接口**不会**返回 API key。

## 5. 飞书群内手测问题（stub）

优先测未预置的自由项目问题，不要只测“项目地图 / 最近故障”。

建议顺序：

```text
介绍一下这个项目
order_service.py 里的 create_order 为什么要检查 coupon？
这个项目里哪个文件最像订单创建入口？
create_order 和 coupon 的关系是什么？
知识库现在缺什么资料？
那是谁改的？
```

期望现象：

- 用户不用输入 `/project`。
- 机器人先发进度消息，再发最终卡片。
- 最终 run 的 answer skill 是 `project_investigation`。
- 卡片底部调试区或事件里能看到 `agent_mode=read_agent`。
- 至少出现一次 read tool 调用，例如 `search_context`、`read_project_file`。
- 有事实结论时必须有 citation。
- 答不上来时说明查了什么、缺什么，而不是显示套路化 0%。

## 6. Debug 入口怎么测

可以在飞书或本地发送：

```text
/project introduce this project
```

期望：

- `RUN_CREATED.entry_mode=debug_slash`
- run.question 里不再包含 `/project`
- 这只是 debug/smoke，不是正式用户入口

## 7. 真实 LLM Safe Live 灰度步骤

### 7.1 前置条件

先满足这些条件，再开 live：

- read_agent stub 在飞书测试群连续通过自然语言问题；
- status endpoint 显示 `agent_mode=read_agent`；
- 工具调用 audit 能看到 `read_tools`；
- 卡片首屏没有被来源/audit 淹没；
- 无引用 fact 不会出现（`tests/test_investigation_live_provider.py` 覆盖 demote）；
- fallback 与失败卡片体验可接受。

### 7.2 本机 `.env` 打开 live（不要把 key 写进仓库）

```text
PROJECT_LENS_AGENT_MODE=read_agent
PROJECT_LENS_MODEL_PROVIDER=openai
PROJECT_LENS_MODEL_LIVE=true
PROJECT_LENS_MODEL_OPENAI_API_KEY=<只放本机 .env>
PROJECT_LENS_MODEL_OPENAI_MODEL=<OpenAI-compatible model>
PROJECT_LENS_MODEL_OPENAI_BASE_URL=<可选；默认官方 OpenAI>
PROJECT_LENS_MODEL_FALLBACK_TO_STUB=true
PROJECT_LENS_MODEL_TIMEOUT_SECONDS=30
```

重启 uvicorn 后，再查 status：

```json
{
  "agent_mode": "read_agent",
  "model_provider": "openai",
  "model_live": true,
  "model_fallback_to_stub": true,
  "model_openai_api_key_configured": true
}
```

### 7.3 飞书手测 live 行为

在同一测试群发送：

```text
create_order 和 coupon 的关系是什么？
```

期望：

1. 仍走自然语言入口（`entry_mode=natural_project_question`）。
2. LLM 通过只读工具调查，而不是直接编答案。
3. 卡片底部调试区可见：
   - `agent_mode: read_agent`
   - `provider: openai_loop`（成功 live）或 fallback 后 `provider: stub_planner`
   - `live_effective: True` 或 fallback 时 `False`
   - `fallback_from: openai_loop`（仅当 fallback 发生）
   - `read_tools: search_context, ...`
4. 事实结论有 citation；无 citation 的候选会进入未知项。
5. 不出现 Apply / PR / deploy / rollback / restart。

### 7.4 怎么验证 fallback

临时制造失败（任选其一）：

- 把 `PROJECT_LENS_MODEL_OPENAI_BASE_URL` 指到不可达地址；或
- 使用无效 API key；或
- 跑自动化：`tests/test_investigation_live_provider.py::test_live_provider_failure_falls_back_to_stub`

期望：

- lifecycle 出现 `model.provider_failed`
- `last_provider_meta.fallback_from == openai_loop`
- 最终仍完成回答（stub 接手）
- Feishu audit 调试区显示 `fallback_from: openai_loop`、`live_effective: False`

关闭 fallback 的负面对照：

```text
PROJECT_LENS_MODEL_FALLBACK_TO_STUB=false
```

期望：完成态但业务结论说明未能完成 live 调查，并带 unknowns（见 `test_live_provider_failure_without_fallback_returns_clear_answer`）。

### 7.5 立刻回滚

一旦异常：

```text
PROJECT_LENS_MODEL_PROVIDER=stub
PROJECT_LENS_MODEL_LIVE=false
```

重启服务，再用 status 确认 `model_live=false`。

## 8. 本地 smoke 入口一览

| 目的 | 命令 / 入口 |
|---|---|
| live provider + fallback + citation + readonly tools | `pytest tests/test_investigation_live_provider.py -q` |
| stub investigation 主路径 | `pytest tests/test_project_investigation_agent.py -q` |
| Feishu ACL + status | `pytest tests/test_feishu_http_and_acl.py -q` |
| 卡片首屏不被 debug 淹没 | `pytest tests/test_feishu_read_agent_card_ux.py -q` |
| 飞书 Day-0 配置自检 | `GET /api/v1/integrations/feishu/status` |

## 9. 不要交给 Cursor 做偏的事

近期不要做：

- 不新增 `/project-*` 命令作为正式入口。
- 不继续堆业务 Skill 来覆盖问法。
- 不继续加厚卡片模板来假装 Agent 变强。
- 不让 RoleView 承担权限过滤。
- 不改 Hermes core。
- 不打开 Apply / PR / deploy / rollback / restart。
- 不把 API key 写入文档或代码仓库。

Cursor 下一步应该做：

- 按本 runbook 做真实 LLM 小流量灰度；
- 梳理 ProjectLens tool envelope，准备给 Hermes runtime 调用；
- 保持 ProjectLens 作为 kernel：ProjectSpace、RolePolicy、RAG/GraphRAG、Evidence、Verifier。
