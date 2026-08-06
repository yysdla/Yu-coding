# ProjectLens Hermes Plugin 胶水层说明

更新日期：2026-08-04  
阶段：Phase 3–4.6（Plugin + RoleView + Feishu Card 原地更新）  
状态：默认 team 首屏、run_id RoleView replay、飞书 interactive card 按钮原地 PATCH 已落地；真机联调为下一步；不改 Hermes core

## 1. 这个 Phase 做了什么

Phase 3 做的不是“把 ProjectLens 改成 Hermes Skill”，而是在 ProjectLens 仓库里提供一个可复制到 Hermes 的薄插件包：

```text
Hermes slash command
-> ProjectLens HTTP API: POST /api/v1/project-agent/ask
-> ProjectLens Kernel / ProjectInvestigationAgent / Verifier
-> Answer Envelope
-> RoleView Markdown (默认 team)
-> Hermes Markdown reply
```

Phase 4 在此基础上补了可读性：

```text
同一份 ProjectAnswer / envelope
-> RoleView(team|technical|business|evidence|...)
-> POST /api/v1/project-agent/runs/{run_id}/role-view 回放（不重新调查）
```

Phase 4.5 补飞书卡片（仍不改 Hermes core）：

```text
Feishu /project
-> pre_gateway_dispatch（插件）
-> ask API
-> 飞书 Open API 发 interactive card（技术/业务/证据按钮）
-> 点击 → Hermes /card button {json}
-> role-view replay → Markdown 新消息
```

插件位置：

```text
src/project_lens/integrations/hermes_plugin/
```

它只负责：

- 注册 Hermes slash command（含 `/card`）；
- 读取环境变量配置；
- 调用 ProjectLens one-shot ask API；
- 按 RoleView 把 envelope 渲染成团队友好 Markdown（默认 team）；
- 用进程内缓存的 `run_id`（或显式 `run_id=`）调用 RoleView replay；
- 可选：经飞书 Open API 自建发 interactive card，并用 `pre_gateway_dispatch` skip 文本回发。

它不负责：

- 不直接查 ProjectLens DB；
- 不直接读 EvidenceIndex / graph store / 项目文件系统；
- 不写 Hermes memory；
- 不写 ProjectLens ProjectMemory；
- 不注册 Hermes skill；
- 不打开 Apply / PR / deploy / rollback / restart；
- 不在插件层生成新的项目事实；
- 不改 Hermes core / 不 fork Hermes；
- 开卡片开关时按钮成功路径原地 PATCH，不再另发 Markdown。

正式 Agent tool loop：

- 若 Hermes plugin `ctx` **已支持** `register_tool`，插件会注册 5 个 `projectlens_*` 只读工具，经 HTTP `GET /project-agent/tools` 发现、`POST /project-agent/tools/call` 调用。
- 若 `ctx` **不支持** `register_tool`，插件不改 Hermes core：只保留 ProjectLens 侧 helper（`tools.py` / client）与测试；等待 Hermes runtime tool API 接入后再启用注册。
- `/project` ask 仍保留为 **debug/smoke**，不是正式 tool loop 主路径。
- 不暴露内部 grep / list / range；不新增 `/project-*` 正式入口。

## 2. 新增文件

```text
src/project_lens/integrations/hermes_plugin/__init__.py
src/project_lens/integrations/hermes_plugin/client.py
src/project_lens/integrations/hermes_plugin/commands.py
src/project_lens/integrations/hermes_plugin/config.py
src/project_lens/integrations/hermes_plugin/render.py
src/project_lens/integrations/hermes_plugin/cards.py
src/project_lens/integrations/hermes_plugin/feishu_client.py
src/project_lens/integrations/hermes_plugin/gateway_hook.py
src/project_lens/integrations/hermes_plugin/tools.py
src/project_lens/integrations/hermes_plugin/plugin.yaml
tests/test_hermes_projectlens_plugin.py
tests/test_hermes_feishu_cards.py
docs/hermes-projectlens-plugin.md
```

## 3. 注册的 Hermes 命令

### `/project <问题>`

用于自由项目问题。

示例：

```text
/project 这个项目的支付入口在哪里？
```

默认 audience：

```text
team
```

### `/project-map`

用于项目地图 / 架构 / 模块入口。

等价问题：

```text
Please introduce this project's architecture, entrypoints, and main modules.
```

默认 audience：

```text
technical
```

### `/project-gaps`

用于知识库缺口。

等价问题：

```text
What important project knowledge is missing or weak right now?
```

默认 audience：

```text
team
```

### `/project-role <audience> [run_id=<id>] [问题]`

用于角色化展示。优先回放同一份已完成 run 的 RoleView，不重新编造事实。

支持 audience：

```text
team
technical
business
qa
manager
evidence
onboarding
debug
```

行为：

1. 参数含 `run_id=...` → 调用 `POST /project-agent/runs/{run_id}/role-view`
2. 否则若进程内有最近一次 ask 缓存的 `run_id` → 同样走 replay
3. 否则仍调用 ask API（保持兼容）

示例：

```text
/project 这个项目是做什么的？
/project-role technical
/project-role business
/project-role evidence
/project-role technical run_id=<uuid>
/project-role qa 哪些地方最需要补测试？   # 无缓存时才会重新 ask
```

`allow_apply` 在 replay 路径仍为 `false`。

### `/card button {json}`

Hermes 飞书卡片点击会合成此命令。开 `PROJECTLENS_FEISHU_CARDS=1` 时：

1. 插件 hook 拦截 `/card button …`
2. 调用 RoleView replay（不重新调查）
3. 用飞书 `PATCH /im/v1/messages/{message_id}` **原地更新同一条卡片**
4. `skip`，成功路径**不再另发 Markdown**

按钮默认：技术视图 / 查看证据 / 项目地图（非团队视图时另有「团队视图」）。

关开关或 CLI：仍走 Markdown `/card` / `/project-role`（兼容兜底）。

## 4. 配置项

插件使用环境变量，不依赖 Hermes 内部 config API。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PROJECTLENS_API_BASE_URL` | `http://127.0.0.1:8000/api/v1` | ProjectLens API v1 地址 |
| `PROJECTLENS_DEFAULT_TENANT_ID` | `demo` | 默认租户 |
| `PROJECTLENS_DEFAULT_PROJECT_ID` | `payment` | 默认项目 |
| `PROJECTLENS_DEFAULT_USER_ID` | `hermes-user` | 默认调用用户 |
| `PROJECTLENS_TIMEOUT_SECONDS` | `30` | ask API 超时 |
| `PROJECTLENS_FEISHU_BINDINGS_JSON` | 空 | Feishu chat 到 ProjectSpace 的绑定 |
| `PROJECTLENS_FEISHU_CARDS` | 关 | `1`/`true` 时飞书 `/project` 等走 interactive card |
| `FEISHU_APP_ID` | 空 | 发卡片用（与 Hermes 同源） |
| `FEISHU_APP_SECRET` | 空 | 发卡片用（与 Hermes 同源） |
| `FEISHU_BASE_URL` | `https://open.feishu.cn` | 飞书 Open API 根地址 |

绑定示例：

```json
{
  "oc_xxx_group_id": {
    "tenant_id": "demo",
    "project_id": "payment"
  },
  "oc_yyy_group_id": {
    "tenant_id": "demo",
    "project_id": "crm",
    "environment": "staging"
  }
}
```

当前 Hermes slash command handler 第一版不稳定传入 chat_id，所以 Phase 3 主要使用 default tenant/project。`ProjectLensPluginConfig.project_for_chat(chat_id)` 已预留 chat binding 能力，后续接 Hermes message metadata 或 hook 时可以直接复用。

## 5. Markdown 输出原则（RoleView）

插件只渲染已验证 envelope，不新增事实。默认 audience 为 `team`。

**team 首屏（默认）包含：**

- 结论；
- 已确认点，最多 3 条（去掉冗长 citation UUID）；
- 暂不确定；
- 建议下一步；
- 来源一行提示（`已基于 N 条项目资料…`，可用 `/project-role evidence` 展开）；
- 精简 Audit：`allow_apply=false` + `run_id`（不展开 tool 列表）。

**evidence / debug** 才会展开来源与更完整 audit。

失败输出包含：

- error code；
- message；
- recovery hint；
- `allow_apply=false`。

失败输出不会出现“已确认”区，避免把错误说明包装成项目事实。

## 6. 为什么这不是“低含金量接 Hermes”

低价值做法是：

```text
Hermes + 一个 ProjectLens Skill + 普通 RAG
```

Phase 3 避开了这条路。现在的边界是：

```text
Hermes owns runtime command entry.
ProjectLens owns project truth.
```

Hermes 插件只是入口胶水。真正的项目智能仍在 ProjectLens：

- ProjectSpace；
- ReadContextGateway；
- ProjectInvestigationAgent；
- Evidence / ToolResult；
- RAG / GraphRAG；
- CitationVerifier；
- Unknown enforcement；
- ProjectMemoryApprovalGateway；
- RoleView / AnswerView；
- Evaluation Harness。

所以面试或答辩时可以这样讲：

> 我复用了 Hermes 的通用 Agent 外壳和飞书入口能力，但没有把项目事实判断交给 Hermes。ProjectLens 仍然是项目智能内核，所有事实必须经过 ProjectLens 的项目空间、权限、证据和引用校验。Hermes 插件只是把飞书/Hermes 的命令接到 ProjectLens Kernel。

## 7. 已验证内容

新增测试：

```text
tests/test_hermes_projectlens_plugin.py
tests/test_hermes_feishu_cards.py
```

覆盖：

- `/project` 能构造 ask request；
- `/project-map` / `/project-gaps` 使用默认问题；
- `/project-role` 能解析 audience / 缓存 run_id replay；
- 未知 audience 不调用 API；
- chat_id -> ProjectSpace binding 的预留逻辑；
- error envelope 不编造事实；
- success envelope 默认 team 首屏（来源不占满）；
- `register(ctx)` 注册 command（含 `/card`），不注册 tool / skill；
- `/card button {...}` 走 role-view replay；
- Feishu card JSON 含 `projectlens_role` + `run_id` + 技术/证据/项目地图；
- 出站 post 后 patch 写入 `message_id`；入站 `/card` patch 成功则 skip；
- `pre_gateway_dispatch` 发卡片后 skip；发失败则 allow；
- 空 `/project` 提示 usage。

验证命令：

```powershell
python -m pytest tests\test_hermes_projectlens_plugin.py tests\test_hermes_feishu_cards.py -q
```

当前结果：

```text
15 passed
```

### 7.1 真实 API smoke 验收记录

2026-08-04 已完成一次真实本地 smoke：

```text
exported plugin path:
  C:\Users\Administrator\Desktop\hermes-agent-main\plugins\projectlens

ProjectLens API:
  http://127.0.0.1:8000/docs -> 200

smoke command:
  python -m project_lens.integrations.hermes_plugin.smoke

result:
  /project handler called /api/v1/project-agent/ask successfully
  response included citations
  response included run_id and trace_id
  response kept allow_apply=false
  registered commands: project, project-gaps, project-map, project-role
```

这一步证明：当前已经不是“插件能注册”的状态，而是“导出的 Hermes 插件能真实调用 ProjectLens Kernel 的 ask API”。

## 8. 下一步真实联调

Phase 3 代码完成后，下一步不是继续堆插件命令，而是做真实 Hermes 安装与飞书试点。

建议顺序：

1. 启动 ProjectLens API，确认 `/api/v1/project-agent/ask` 可访问。
2. 导出 Hermes 可加载的 ProjectLens bootstrap plugin。

```powershell
cd C:\Users\Administrator\Desktop\project-lens
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m project_lens.integrations.hermes_plugin.export `
  --project-lens-root C:\Users\Administrator\Desktop\project-lens `
  --hermes-plugins-dir C:\Users\Administrator\Desktop\hermes-agent-main\plugins `
  --overwrite
```

导出后 Hermes plugins 目录会出现：

```text
C:\Users\Administrator\Desktop\hermes-agent-main\plugins\projectlens\
  __init__.py
  plugin.yaml
```

这个目录只是 bootstrap，不复制 `project_lens.db`、Evidence、Graph、项目源码或记忆数据。

3. 在 Hermes 中启用 project plugin。
4. 配置：

```powershell
$env:PROJECTLENS_API_BASE_URL="http://127.0.0.1:8000/api/v1"
$env:PROJECTLENS_DEFAULT_TENANT_ID="demo"
$env:PROJECTLENS_DEFAULT_PROJECT_ID="payment"
```

5. 先用 ProjectLens 自带 smoke 工具验证导出插件能打通 ask API：

```powershell
cd C:\Users\Administrator\Desktop\project-lens
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m project_lens.integrations.hermes_plugin.smoke `
  --plugin-dir C:\Users\Administrator\Desktop\hermes-agent-main\plugins\projectlens `
  --api-base-url http://127.0.0.1:8000/api/v1 `
  --tenant-id demo `
  --project-id payment `
  --question "这个项目是做什么的？"
```

这个 smoke 会模拟 Hermes 的 `ctx.register_command()`，加载导出的插件，执行 `/project` handler，然后确认它真的通过 HTTP 调用了 ProjectLens ask API。

如果 smoke 失败，优先排查：

- ProjectLens API 是否启动；
- `PROJECTLENS_API_BASE_URL` 是否正确；
- 导出的 `plugins\projectlens\__init__.py` 是否指向正确的 ProjectLens `src`；
- 默认 `tenant_id/project_id` 是否存在。

6. 在 Hermes CLI 先测：

```text
/project 这个项目是做什么的？
/project-map
/project-gaps
/project-role business 这个项目现在的交付风险是什么？
```

7. 再接 Feishu 测试群。
8. 检查 ProjectLens audit 是否出现对应 AgentRun / tool trace。

如果不想覆盖已有 Hermes 插件目录，不要传 `--overwrite`。导出器默认会拒绝覆盖，避免误删你在 Hermes 里手工调整过的插件文件。

## 9. 后续不要急着做什么

近期不要：

- 不要把更多业务问题都做成 slash command；
- 不要把插件改成直接读 ProjectLens 存储；
- 不要让插件写 memory；
- 不要为了卡片好看绕过 ProjectLens AnswerView / RoleView；
- 不要打开 Apply；
- 不要 fork Hermes。

下一步更值得做的是：

- 飞书真机验收：开 `PROJECTLENS_FEISHU_CARDS=1`，点技术/证据/项目地图确认原地更新；
- Feishu group -> ProjectSpace binding 稳定传入 chat_id；
- 端到端 eval：Hermes 命令触发 ask / role-view，回答有 citation 或 unknown；
- Phase 5 Collaboration Plane（缺口派单 / 周报等）。

## 10. 飞书 interactive card 验收（下一步真机）

在 Hermes 家目录 `.env`（与 gateway 同源）增加：

```text
PROJECTLENS_FEISHU_CARDS=1
# FEISHU_APP_ID / FEISHU_APP_SECRET 应已存在
```

重启 gateway 后在飞书群：

```text
/project 这个项目是做什么的？
```

期望：

1. 收到 interactive card（结论/未知/下一步为主）
2. 有「技术视图 / 查看证据 / 项目地图」按钮
3. 点击后**同一条卡片原地更新**，不另发 Markdown 成功回复
4. `allow_apply=false`；按钮不新建 investigation run

关闭开关或 CLI：仍为 Phase 4 纯 Markdown 行为。
