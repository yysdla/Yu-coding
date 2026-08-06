# ProjectLens 后续开发计划（Cursor 交接版）

> 2026-08-05 方向冻结：后续开发必须以 `docs/project-agent-final-plan.md` 为唯一 canonical 规划，并优先参考 `docs/projectlens-next-plan-simple.md` 理解产品目标。当前正式主线已经从“`/project` 命令 + Skill 流水线 + 通用答案 + 卡片角色过滤”纠偏为 **Hermes Runtime + ProjectLens Kernel + 可插拔 ProjectSpace + 运行前 RolePolicy**。
>
> 当前 6 条硬约束：
>
> 1. Hermes 最终主导 Agent loop；ProjectLens 不再长期自建完整通用 Agent runtime。
> 2. ProjectLens 收敛为项目智能内核：ProjectSpace、RAG/GraphRAG、Evidence、RolePolicy、ToolGateway、CitationVerifier、ProjectMemory。
> 3. `/project` 只保留为 debug / smoke 入口，正式产品入口是飞书自然语言项目问答。
> 4. 先落 ProjectSpace / ProjectMemberRolePolicy / ChatVisibilityPolicy，不继续堆卡片和业务 Skill。
> 5. 角色权限必须在生成前进入运行时；RoleView 只做展示翻译，不做权限过滤。
> 6. 短期继续 read-only，Apply / PR / deploy / rollback / restart 不打开。
>
> Cursor 下一步不要继续加 `/project-*` 命令、角色按钮或固定卡片模板；应先做 ProjectSpace/RolePolicy 运行时前置，然后接 Hermes-driven ProjectLens read-only tools。

> 2026-08-05 Phase 2 第一刀已落地：`src/project_lens/integrations/feishu/ingress.py` 新增 `FeishuIngressRouter` / `FeishuIngressDecision`，统一标记飞书入口类型；自然语言项目问题进入 `entry_mode=natural_project_question`，`/project ...` 保留但降级为 `entry_mode=debug_slash`。`FeishuEventService` 两条创建 run 的路径都会把 `entry_mode` 写入 `RUN_CREATED` lifecycle payload；不改 RoleView 权限、不新增命令、不打开 Apply。测试入口：`tests/test_feishu_http_and_acl.py`、`tests/test_feishu_collaboration_gate.py`、`tests/test_feishu_integration.py`。
> 2026-08-05 Phase 2/3 接缝修正已落地：`FeishuEventService` 现在真正使用 `FeishuIngressRouter` 作为入口中枢；自然语言项目问题会把 `entry_mode=natural_project_question` 写入 `RUN_CREATED`，`/project ...` 会先剥离 debug 前缀再创建 run，并写入 `entry_mode=debug_slash`。新增验收：当 `RunService.agent_mode=read_agent` 时，飞书自然语言项目问题会进入 `ProjectInvestigationAgent`，最终回答 `skill=project_investigation`，并触发只读工具调用。下一步 Cursor 不要再重做 ingress/router；应转向 read_agent 的飞书灰度配置、真实 LLM 安全开关和 ProjectLens tool envelope。
> 2026-08-05 read_agent 飞书灰度 Runbook 已新增：`docs/read-agent-feishu-grey-release.md`。同时 `/api/v1/integrations/feishu/status` 已暴露 `agent_mode`、`ask_agent_mode`、`model_provider`、`model_live`，用于 Day-0 自检。Cursor 下一步请以该 runbook 为准做灰度补强，不要回到 `/project` 主入口或固定 Skill/card 路线。
> 2026-08-05 Tool Envelope 第二刀已落地：MCP `list_projectlens_tools` / `call_projectlens_tool` 与 Hermes plugin HTTP `list_tools` / `call_tool` 均走 `ProjectAgentToolService`（同源 `GET/POST /project-agent/tools[/call]`）。正式 tool loop 仅 5 个 `projectlens_*`；`projectlens_ask_project` 与 `/project` ask 保留为 debug/smoke。Hermes `register_tool` 有则注册、无则只保留 helper（不改 Hermes core）。文档见 `docs/projectlens-tool-envelope.md` §4。
>
> 2026-08-05 Tool Envelope 第一刀已落地：新增 `ProjectAgentToolService` 和 HTTP API：`GET /api/v1/project-agent/tools`、`POST /api/v1/project-agent/tools/call`。外部只暴露 5 个 `projectlens_*` 只读工具：search_context / read_project_file / query_graph / authorized_evidence / list_knowledge_gaps；每次调用先解析 ProjectSpace + RolePolicy + ChatVisibilityPolicy，再经 ReadContextGateway / ToolGateway 执行，并返回 citation/audit/visibility envelope。文档见 `docs/projectlens-tool-envelope.md`。Cursor 下一步只能在这个基础上扩展 Hermes/MCP 接入，不要暴露写工具或新增 `/project-*` 正式入口。

> 2026-08-05 Phase 1 第一刀已落地：`src/project_lens/project_space/policies.py` 新增 `RoleKind`、`AnswerDepth`、`VisibilityLevel`、`ProjectMemberRolePolicy`、`ChatVisibilityPolicy`、`EffectiveAccessScope`、`ResolvedProjectRuntimeContext`、`ProjectRuntimeContextResolver`；`ProjectSpace` 已支持 `role_policies` / `chat_visibility_policies`，JSON loader 已能解析这两类配置。测试入口：`tests/test_project_space_policies.py`。

> 2026-08-04 重要说明：当前 canonical 规划已迁移到 `docs/project-agent-final-plan.md`。  
> 这份文件保留历史交接、阶段记录和执行提示，但后续产品方向请以新总规划为准。  
> 特别是：不要再把角色做成“卡片过滤器”，角色策略必须前置到运行时；不要再继续加厚 Skill 流水线或固定卡片模板。

本文档保留历史交接与阶段记录；**产品方向以 `docs/project-agent-final-plan.md` 为准**。

优先阅读：

- `docs/project-agent-final-plan.md`：唯一 canonical 规划（Hermes Runtime + ProjectLens Kernel + ProjectSpace + RolePolicy）。
- `docs/projectlens-next-plan-simple.md`：白话版产品目标。
- `docs/hermes-projectlens-plugin.md`：当前 Hermes Plugin 胶水层。
- `docs/agent-harness-design.md`：Agent harness（ToolGateway / Policy / ContextPack 等）。
- `docs/architecture.md`：产品边界与三大平面。
- `docs/pilot-launch-config.md`：Stub / Safe Live / 灰度配置。

当前 Cursor 如果已经完成 KnowledgeGap、Git/Feishu 文档索引、图谱查询、MemoryProposal、Engineering Propose/Validate、ProjectOps 时间窗查询等横向能力，下一阶段应按 final-plan：先做 ProjectSpace / RolePolicy 运行时前置，再接 Hermes-driven read-only tools；不要继续横向堆新业务 Skill 或卡片模板。

**Harness 已完成到第三刀：**

1. 第一刀：ConversationSession + FollowupRewriter、ContextPack、TaskScratchpad、compression、ToolSpec/boundaries、Approval、Lifecycle、Evaluation/Replay probes。
2. 第二刀：SQLite ConversationSession 持久化 + `context_prompt.py` L0-L5 renderer。
3. 第三刀：Run 路径强制 `ContextPack -> ContextPrompt -> StubModelAdapter`；lifecycle `context.prompt_rendered`；重启后多轮追问可恢复。答案路径仍为 analyze/verify/compose；真实 LLM 尚未接入。

Evaluation CLI 不要继续横向扩展。配置：`PROJECT_LENS_CONVERSATION_STORE=sqlite|memory`（默认 sqlite）。

**第四刀（可插拔 ModelProvider）已完成：** `workflow/providers` 统一 ContextPrompt Provider；默认 Stub；预留 OpenAI/Internal/Local（live=False）；timeout/retry/usage 进入 adapter audit refs。Workflow 只依赖 ModelAdapter，不依赖具体 Provider。仍禁止真实 LLM、Apply、Feishu 自拼 prompt。

**Feishu Audit 卡片已完成（路线图 Phase 1）：** 回答卡片展示安全 Audit 摘要；数据来自 run/event + context_pack/context_prompt/model_adapter audit_refs；不含 prompt 全文、Evidence 正文、密钥；无 Apply 按钮。

**Phase 2–7 已完成：** Provider Runtime Hardening、Safe Live LLM、Durable ConversationSession、Engineering Validate 卡片、Knowledge Loop、Graph Expansion（Document/Endpoint）。

**Phase 8 ProjectOps 已完成三刀：**

1. 第一刀：只读 ProjectOps correlation，关联 route / trace_id / metric / service / module / symbol / recent changes。
2. 第二刀：incident graph hints + route hint scanner，让 `/orders` 能稳定关联到 `create_order`。
3. 第三刀：Deploy/Release Timeline Correlation，回答“是不是上线后开始异常”，并在 Feishu 卡片展示 **ProjectOps 时间线相关性**。当前 demo 数据会输出 `release_after_ops`，说明 release 更像后续 hotfix / mitigation。

**当前准星：** 以 `docs/project-agent-final-plan.md` 为准——ProjectSpace / RolePolicy 前置，Hermes-driven read-only tools；不继续 Phase 8 细节，不打开 Apply。

## 0.1 2026-08-03 补充优先级：Feishu AnswerView 2.0 / RoleView

当前飞书卡片体验的新问题是：报告仍然过度展示信息来源、Skill、trace、audit 等内部细节，首屏不像“人在回答项目问题”，更像系统运行报告。

产品目标应调整为：

```text
同一份 Evidence-backed ProjectAnswer
-> 按问题类型选择 AnswerView
-> 按阅读对象选择 RoleView / AudienceView
-> 在飞书里渲染成可读、可追问、可协作的卡片
```

注意：这不是继续新增业务 Skill。角色视图只是展示层，不是事实决策层。

建议新增：

```python
class AnswerAudience(StrEnum):
    TEAM = "team"
    TECHNICAL = "technical"
    BUSINESS = "business"
    QA = "qa"
    MANAGER = "manager"
    ONBOARDING = "onboarding"
    EVIDENCE = "evidence"
    DEBUG = "debug"
```

其中：

- `AnswerView` 解决“用户问的是什么问题”：项目概览、项目地图、变更影响、故障协作、代码解释、知识缺口等。
- `AnswerAudience` 解决“这份答案给谁看”：团队默认、技术、业务/产品、测试、管理、新人、证据、调试。

默认飞书首屏应使用 `AnswerAudience.TEAM`，重点展示：

- 一句话结论；
- 当前状态；
- 对团队/业务的影响；
- 已确认事实；
- 当前未知项；
- 建议下一步；
- 简短来源摘要，例如“已基于 4 条项目资料生成回答，可点击查看来源”。

技术视图应展示：

- 相关文件、模块、函数、接口、服务、commit；
- 已确认事实；
- 技术推断/假设；
- 可能原因；
- 排查步骤；
- 验证建议；
- 只读边界：Apply / PR / deploy / rollback / restart 仍关闭。

业务/产品视图应展示：

- 这个项目/模块用人话解释是什么；
- 当前问题对业务或用户意味着什么；
- 技术团队正在做什么；
- 哪些已经确认；
- 哪些仍未知；
- 需要产品/业务补充什么资料或做什么决策。

QA/测试视图应展示：

- 影响场景；
- 回归范围；
- 建议测试用例；
- 验收标准；
- 环境/版本信息。

管理/进度视图应展示：

- 当前状态；
- 负责人或责任团队；
- 阻塞点；
- 风险；
- 下一里程碑或决策点。

新人理解视图应展示：

- 项目定位；
- 主模块；
- 入口文件；
- 关键文档；
- 推荐阅读路径。

证据和调试信息的处理原则：

- Evidence 不删除，但默认不要占据首屏大部分空间。
- 默认只展示来源摘要。
- “查看证据”再展示 README、源码文件、飞书文档、commit、ToolResult 摘要。
- provider、model、tool calls、SkillGuide、trace_id、audit summary 默认进入 debug 视图或卡片底部。

最小实现任务：

1. 新增 `AnswerAudience` / `RoleView` 展示层。
2. `render_answer_card()` 默认使用 `AnswerAudience.TEAM`。
3. 保留现有 `AnswerView`，但不要让它继续等同于完整报告模板。
4. 移动 `参考来源` 到主内容后部，或改成“来源摘要 + 查看证据按钮”。
5. 增加飞书按钮：`给技术看的版本`、`给产品/业务看的版本`、`给测试看的版本`、`查看证据`。
6. 角色切换复用 ConversationSession / FollowupRewriter，不要求用户重新输入项目。
7. Renderer 只能消费 `ProjectAnswer`，不能访问 EvidenceIndex / graph store / ops store，也不能生成新事实。

验收标准：

- 默认卡片首屏不被 Evidence/source/audit 淹没。
- 默认首屏看起来像团队协作答案，而不是内部运行报告。
- 技术视图能展示文件/函数/接口/排查步骤。
- 业务视图能展示业务影响、工程进度和需要配合的事项。
- “查看证据”能看到来源，但不会挤占默认主屏。
- 所有事实仍来自 `ProjectAnswer.claims/evidence`，无引用 fact 不得进入角色视图。
- 不新增业务 Skill；不打开 Apply / PR / deploy / rollback / restart。

## 1. 项目定位

ProjectLens 是一个围绕“项目空间”的团队协作 Agent，不是普通聊天机器人，也不是只做研发故障解释的小工具。

核心目标：

1. 团队成员在飞书里可以快速理解项目架构、模块职责、入口、依赖、版本信息、负责人和历史决策。
2. RAG 知识库和知识图谱沉淀项目资料、代码结构、任务、发布、事故、团队文档和决策记录。
3. 项目报错、上线异常、版本影响时，Agent 能基于证据解释问题、定位影响面、提出验证路径，让技术和非技术成员能在同一个上下文里协作。
4. 后续可扩展成 InsightOS 的通用企业协作 Agent：项目代码能力只是一个 Skill，未来还可以接任务管理、会议纪要、跨部门问答、通用执行工具等。

一句话定位：

> ProjectLens 是一个接入飞书的项目理解、项目记忆和项目协作 Agent，先服务研发项目，最终成为团队协作入口。

## 2. 当前代码基线

当前项目根目录：

```text
C:\Users\Administrator\Desktop\project-lens
```

## 0. 2026-08-03 最新优先级覆盖：Agent 壳 + 可插拔 Project Space

本节优先级高于上面“知识库缺口报告 / Git 变更 Evidence / 图谱查询服务”等旧排序。旧能力不是废弃，而是暂时不要继续横向加厚；现在先把主架构纠偏，否则越做越像固定 Skill 流水线。

详细方案见：

```text
docs/project-agent-final-plan.md
docs/projectlens-next-plan-simple.md
```

尤其要掌握：角色策略生成前进入运行时；`/project` 只做 debug；先做 ProjectSpace / RolePolicy，再接 Hermes-driven tools。

这不是一句抽象原则，而是当前后续开发的主判断：ProjectLens 没有偏离安全目标，但已经偏离 Agent 产品目标。下一步不是继续加 Skill，而是把“谁决定读什么”从 workflow 还给 LLM。

### 新产品判断

ProjectLens 不应该是 payment demo 的固定机器人，而应该是：

```text
稳定 Agent Shell + 可插拔 Project Space
```

稳定不变的部分：

- Feishu 入口和身份映射；
- ProjectContext / AccessContext；
- LLM read-only investigation loop；
- ReadContextGateway / ToolGateway；
- ContextPack / ContextPrompt；
- EvidenceLedger / citation verifier；
- AnswerView；
- MemoryApprovalGateway；
- audit / lifecycle / replay；
- Apply / PR / deploy / rollback / restart 关闭。

可替换的部分：

- repositories；
- docs / tasks / releases / incidents connectors；
- graph namespace；
- memory namespace；
- ACL policy；
- file allowlist；
- project glossary；
- project-specific SkillGuide。

### 当前代码基线

仓库已有项目注册雏形，不要重复造轮子：

- `context/bootstrap.py`：`LocalProjectRegistration`、`parse_local_project_registrations`；
- `workflow/resolver.py`：`ProjectResolver`；
- `integrations/feishu/identity.py`：Feishu chat -> registered project binding；
- `tests/test_project_registry.py`：已证明第二项目可以隔离检索、飞书绑定能拒绝未注册项目。

这些应升级为 `ProjectSpace / ProjectRegistry`，而不是继续只传 `ProjectRef + access_scope`。

### 两个阶段先做哪个

战略顺序：

```text
先产品方向纠偏，再项目空间插件化。
```

代码顺序：

```text
先薄切 ProjectSpace / ProjectRegistry，再接 read-only ProjectInvestigationAgent。
```

解释：

- 产品方向必须先定死，否则 Cursor 会继续沿着旧路线新增 Skill、关键词和卡片模板。
- 代码上不能先孤立做 AgentLoop，因为没有 ProjectSpace 时，新 Agent 容易继续写死 payment demo。
- 也不能只大做 ProjectSpace，因为不接 LLM 自主工具选择时，它只会变成“多项目版 Skill 流水线”。
- 因此最小正确路径是：`方向冻结 -> ProjectSpace 薄壳 -> read-only AgentLoop -> verifier/AnswerView 接入`。

最终要证明的不是“多了一个项目配置”，而是：

```text
同一个 Agent 壳切换项目后，LLM 能在权限内自主选择只读工具，并基于 Evidence / ToolResult 回答未预置项目问题。
```

### Cursor 下一步任务

1. 新增或重构 `ProjectSpace` 契约。
   - 第一版可以仍然从 JSON / YAML / env JSON 加载。
   - 必须包含 project ref、repositories、source connectors、graph namespace、memory namespace、ACL、file allowlist、SkillGuide bindings。

2. 在现有注册逻辑上增加 `ProjectRegistry`。
   - 保留兼容层：旧 `LocalProjectRegistration` 可以转换成 `ProjectSpace`。
   - `ProjectResolver` 后续返回 `ResolvedProjectSpace` 或在 `ResolvedProject` 内携带 `space`。

3. Feishu binding 只绑定项目身份。
   - chat binding 只解析 tenant / project_id / service / env / allowed_users。
   - 不允许在 Feishu adapter 写项目业务逻辑。

4. 新增 read-only `ProjectInvestigationAgent` skeleton。
   - 用 `PROJECT_LENS_AGENT_MODE=workflow|read_agent` 灰度。
   - `workflow` 模式保持现状。
   - `read_agent` 模式下，任意项目自由问题进入 LLM 只读调查循环。

5. Skill 改为 `SkillGuide`。
   - `classify_project_question` 可以保留给旧 workflow 和提示建议。
   - 新路径里 Skill 只是 checklist / prompt hint，不是所有问题必须进入的业务桶。

6. 只开放 read lane。
   - 允许：`search_context`、`authorized_evidence`、`query_graph`、`list_knowledge_gaps`、后续可补 `read_project_file`。
   - 禁止：`create_patch_plan`、`validate_patch_plan`、`apply_patch_plan`、`create_pr`、`deploy`、`rollback`、`restart`。

### 最小验收标准

- 同一个 Agent Shell 至少可挂两个 ProjectSpace fixture。
- 飞书 chat binding 能切换到不同 ProjectSpace，且不改 Agent 代码。
- 一个未预置自由项目问题能触发只读工具调用，例如“这个项目里哪个文件最像订单创建入口？”。
- tool call 必须经过 Gateway，并写入 audit。
- 最终答案 facts 必须带 Evidence / ToolResult citation。
- 答不上来时说明“查了什么 / 缺什么资料 / 建议补什么”，不能套路化显示 0%。
- Apply / PR / deploy / rollback / restart 继续关闭。

### 不要继续做

- 不要给 `classify_project_question` 继续堆关键词。
- 不要增加更多业务 Skill 来覆盖自由问法。
- 不要把每个问题都做成固定卡片模板。
- 不要让 LLM 只做 expression enhancer。
- 不要为 payment demo 写死路径、服务名或 fixture。
- 不要打开写代码、发 PR、发布、回滚、重启等动作。

### 最新 Cursor 提示词

```text
请先不要继续新增 Skill 或卡片模板。当前 ProjectLens 的核心偏航是：项目问题进入后仍由 classify_project_question + deterministic workflow 主导，LLM 只是 expression enhancer。我们要做最小纠偏：新增 read-only ProjectInvestigationAgent，让 LLM 能在权限内自主选择只读工具读取项目资料，并最终输出带引用的答案草稿。

目标：
1. 先薄切 ProjectSpace / ProjectRegistry，复用并升级现有 LocalProjectRegistration / ProjectResolver / Feishu binding。
2. 再新增 ProjectInvestigationAgent / ProjectAgentLoop，只用于 read-only 项目问题。
3. 保留旧 ProjectWorkflow，用 PROJECT_LENS_AGENT_MODE=workflow|read_agent 灰度。
4. 保留 Feishu intent 分流、AnswerView、ReadContextGateway、MemoryApprovalGateway、Evidence verifier、Apply 关闭。
5. LLM 可选择工具：search_context、read_project_file、query_graph、authorized_evidence、list_knowledge_gaps。
6. 所有工具调用必须走 Gateway，记录 audit，不允许 Feishu adapter 直接访问 EvidenceIndex / graph store / ops store。
7. 最终 LLM 输出 structured answer draft：facts / inferences / unknowns / next_actions / citations。
8. Verifier 必须拒绝或降级无 citation 的 fact。
9. Apply / PR / deploy / rollback / restart 继续关闭。
10. 不要继续给 classify_project_question 堆关键词，不要新增业务 Skill 来覆盖自由问法。

验收：
- 同一个 Agent Shell 至少可挂两个 ProjectSpace fixture。
- 未预置项目自由问题能触发至少一次只读工具调用。
- Tool audit 记录 read tool name、参数、结果摘要。
- ProjectAnswer facts 都有 Evidence / ToolResult 引用。
- 答不上来时说明查了什么、缺什么，而不是套路化 0%。
- Feishu 卡片仍用 AnswerView 展示。
- ruff 和 pytest 通过。
```

---

## 19. 重要更新：后续开发请以新路线图为准

截至 2026-08-02，ProjectLens 已经完成更多 Agent Harness 相关能力，包括：

- ConversationSession / FollowupRewriter。
- ContextPack。
- ContextPrompt。
- StubModelAdapter。
- 可插拔 ModelProvider。
- Evaluation / Replay harness。

因此后续 Cursor 不要继续按本文档较早的优先级直接横向加业务能力。

请优先阅读并执行：

```text
docs/project-agent-final-plan.md
docs/projectlens-next-plan-simple.md
```

（旧路线图 Phase 1–8 已完成并归档删除；当前准星见 final-plan。）

历史近期优先级（已完成，勿再按此扩展）：

```text
1. Feishu Audit 卡片
2. Provider Runtime Hardening
3. Safe Live LLM Provider
4. Durable ConversationSession
5. Engineering Validate 卡片增强
6. Knowledge Loop 增强
```

继续保持禁止事项：

- 不要打开 Engineering Apply。
- 不要自动创建 PR。
- 不要自动发布 / 回滚 / 重启。
- 不要让 Feishu adapter 直接访问 EvidenceIndex。
- 不要让 LLM 绕过 ContextPrompt。
- 不要把未审批内容写入 ProjectMemory。
- 不要把 logs / metrics / traces 写入长期 RAG。


当前已完成能力：

- FastAPI API：
  - `POST /api/v1/runs`
  - `POST /api/v1/runs/{run_id}/execute`
  - `GET /api/v1/runs/{run_id}`
  - `GET /api/v1/runs/{run_id}/events`
  - `POST /api/v1/projects/snapshot`
  - `POST /api/v1/projects/timeline`
  - `POST /api/v1/projects/change-impact`
  - `POST /api/v1/feishu/events`
- Feishu：
  - URL verification
  - verification token
  - optional HMAC signature
  - event deduplication
  - chat -> project binding
  - group message -> AgentRun
  - background workflow execution
  - progress text
  - final interactive card
  - card 中展示项目范围、业务结论、技术细节、已验证结论、未知项、证据来源、建议动作、可继续追问
- Knowledge / Context：
  - 本地 Python 代码索引
  - Markdown/TXT/JSON incident 索引
  - ACL 过滤
  - BM25 检索
  - traceback 精确定位
  - RRF 融合
  - `ProjectSnapshot`
  - `TimelineEvent`
  - `ChangeImpact`
- Agent / Workflow：
  - Skill 路由：
    - `architecture`
    - `version_change`
    - `code_explanation`
    - `incident_diagnosis`
    - `project_knowledge`
  - `resolve -> collect -> analyze -> verify -> compose`
  - facts / inferences / unknowns / actions 分离
  - factual claim 必须引用 Evidence
- Persistence：
  - SQLite run/event repository
  - approval request store
  - approval endpoints
- Evaluation：
  - retrieval metrics
  - graph tests
  - replay tests
  - approval safety tests

最近验证结果：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest -q
# 72 passed, 1 warning

& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m ruff check src tests
# All checks passed
```

注意：`py -3.12` 在当前环境可能找不到解释器，可以优先用上面的 Python 绝对路径。

## 3. 必须保持的架构边界

### 3.1 Knowledge 层负责什么

当前代码里 Knowledge 层主要落在：

```text
src/project_lens/context/
src/project_lens/graph/
src/project_lens/evaluation/
```

后续可以逐步重命名或抽象成更清晰的 `knowledge/` 模块，但不要一次性大重构。

Knowledge 层职责：

- source ingestion
- normalize
- chunking
- indexing
- retrieval
- graph
- snapshot
- timeline
- change impact
- memory governance
- ACL filtering
- Evidence provenance

Knowledge 层输出必须是结构化对象，尤其是：

- `Evidence`
- `ProjectSnapshot`
- `TimelineEvent`
- `ChangeImpact`
- 后续新增的 `GraphEvidence`
- 后续新增的 `MemoryProposal`

不要让 Knowledge 层直接生成最终自然语言答案。

### 3.2 Agent 层负责什么

当前代码里 Agent 层主要落在：

```text
src/project_lens/workflow/
src/project_lens/runtime/
src/project_lens/application/
```

Agent 层职责：

- goal / skill routing
- run state
- planning
- specialist dispatch
- tool gateway
- verification
- answer composition
- approval flow
- collaboration flow
- future code execution workflow

Agent 可以消费 Knowledge 的结构化结果，但不能直接绕过 `ContextEngine` 访问底层索引、向量库、图数据库或文件存储。

### 3.3 Adapter 层负责什么

当前 Feishu 适配层：

```text
src/project_lens/integrations/feishu/
src/insight_project/adapters/feishu/
```

Adapter 层职责：

- Feishu callback parsing
- Feishu signature / token verification
- event deduplication
- external identity -> ProjectContext
- card rendering
- outbound message sending

不要在 Feishu adapter 里做检索、分析或业务决策。

## 4. 最终版本建议目录结构

短期不要立刻大搬家。中长期建议演进成下面结构：

```text
src/insight_project/
  shared/
    contracts/
      project_context.py
      evidence.py
      findings.py
      tools.py
      approvals.py
      memory.py

  knowledge/
    sources/
      feishu_docs.py
      git_repo.py
      task_tracker.py
      releases.py
      incidents.py
      ops_signals.py
    normalize/
      documents.py
      code.py
      events.py
    retrieval/
      bm25.py
      vector.py
      fusion.py
      rerank.py
    graph/
      models.py
      extractor.py
      linker.py
      store.py
      query.py
    memory/
      proposal.py
      approval.py
      store.py
    services/
      context_engine.py
      snapshot.py
      timeline.py
      change_impact.py
      project_map.py

  agent/
    runtime/
      loop.py
      events.py
      run_store.py
      session.py
    skills/
      project_knowledge.py
      project_engineering.py
      project_ops.py
      general_collaboration.py
    specialists/
      architecture.py
      version_change.py
      code_explanation.py
      incident_diagnosis.py
    tools/
      gateway.py
      schemas.py
      registry.py
    approval/
      policy.py
      workflow.py
    execution/
      worktree.py
      patch_plan.py
      validation.py
    composer/
      answer.py
      cards.py

  adapters/
    feishu/
      routes.py
      service.py
      identity.py
      cards.py
      messenger.py
    http/
    cli/

  persistence/
    sqlite.py
    postgres.py

  evaluation/
    datasets/
    replay.py
    metrics.py
```

迁移原则：

1. 每次只迁移一个模块。
2. 先加兼容导入，再替换调用方。
3. 每次迁移后运行全量测试。
4. 当前阶段优先补功能，不要为了“目录漂亮”打断产品闭环。

## 5. 后续开发阶段总览

建议按 `docs/project-agent-final-plan.md` 推进；下列旧 Phase 表仅作历史对照。

| 阶段 | 目标 | 风险等级 | 是否只读 |
|---|---|---:|---:|
| Phase 8 | Feishu 项目指令和专属卡片 | 低 | 是，已完成第一版 |
| Phase 9 | 知识库增强：Feishu 文档、Git 变更、任务数据 | 中 | 是 |
| Phase 10 | 知识图谱增强：实体、关系、图检索 | 中 | 是 |
| Phase 11 | 项目记忆：人工确认后沉淀事实和决策 | 中 | 默认只读，写记忆需确认 |
| Phase 12 | Project Engineering Skill：代码编辑提案、测试、diff | 高 | 否，必须审批 |
| Phase 13 | ProjectOps Skill：日志/指标/trace 只读关联 | 中 | 是 |
| Phase 14 | 通用协作 Skill：非研发场景扩展 | 中 | 先只读 |
| Phase 15 | 多租户、治理、成本、评估和生产化 | 中高 | 混合 |

## 6. Phase 8：Feishu 项目指令和专属卡片（第一版已实现）

目标：让用户在飞书里不需要写复杂问题，发简单指令就能进入对应项目能力。

当前状态：

- 已新增 `src/project_lens/integrations/feishu/commands.py`。
- 已在 `src/project_lens/integrations/feishu/service.py` 创建 `AgentRun` 前接入短指令 rewrite。
- 已在 `src/project_lens/integrations/feishu/cards.py` 增加 Skill 专属视图：
  - `项目地图视图`
  - `变更影响视图`
  - `故障协作视图`
  - `知识库缺口视图`
- 已新增 `tests/test_feishu_commands.py`。
- 已扩展 `tests/test_feishu_integration.py`，覆盖四个飞书快捷入口。
- 当前实现仍然只改写问题文本，不直接指定 Skill；Skill 选择继续复用 `classify_project_question`。
- 当前实现不引入任何写操作，不改变 ACL、权限或审批模型。

### 6.1 支持的指令

先支持这些自然语言入口：

```text
项目地图
项目架构
最近变更
版本影响
最近故障
故障复盘
知识库缺什么
负责人是谁
```

映射：

| 指令 | Skill | 预期输出 |
|---|---|---|
| 项目地图 / 项目架构 | `architecture` | 服务、入口、依赖、风险、证据 |
| 最近变更 / 版本影响 | `version_change` | 时间线、相关事故、影响服务、回归建议 |
| 最近故障 / 故障复盘 | `incident_diagnosis` | 历史事故、影响范围、处理建议、未知项 |
| 知识库缺什么 | `project_knowledge` | 缺失资料、未解决证据项、建议沉淀 |
| 负责人是谁 | `project_knowledge` | owner 证据；没有证据就明确未知 |

### 6.2 已实现文件

已新增：

```text
src/project_lens/integrations/feishu/commands.py
```

已实现类型：

```python
class FeishuProjectCommand(StrEnum):
    PROJECT_MAP = "project_map"
    RECENT_CHANGES = "recent_changes"
    RECENT_INCIDENTS = "recent_incidents"
    KNOWLEDGE_GAPS = "knowledge_gaps"
    OWNER_LOOKUP = "owner_lookup"
    FREE_QUESTION = "free_question"
```

已实现函数：

```python
def classify_feishu_command(text: str) -> FeishuProjectCommand:
    ...

def rewrite_command_to_question(command: FeishuProjectCommand, text: str) -> str:
    ...

def rewrite_feishu_message(text: str) -> str:
    ...
```

已在：

```text
src/project_lens/integrations/feishu/service.py
```

创建 run 前，把简单指令 rewrite 成更稳定的问题。

不要在 Feishu service 里直接指定 Skill，先复用已有 `classify_project_question`，降低改动风险。

### 6.3 已实现专属卡片

当前统一卡片在：

```text
src/project_lens/integrations/feishu/cards.py
```

第一版没有拆成多个公开渲染函数，而是新增了内部函数：

```python
def _skill_focus_section(run: AgentRun, answer: ProjectAnswer) -> dict[str, object] | None:
    ...
```

后续如果卡片越来越复杂，再拆成：

```python
def render_project_map_section(answer: ProjectAnswer) -> dict[str, object] | None
def render_version_impact_section(answer: ProjectAnswer) -> dict[str, object] | None
def render_incident_section(answer: ProjectAnswer) -> dict[str, object] | None
def render_knowledge_gap_section(answer: ProjectAnswer) -> dict[str, object] | None
```

短期不要新增复杂模型；继续从 `answer.claims`、`answer.evidence`、`answer.unknowns`、`answer.recommended_actions` 中提取。

### 6.4 测试

已新增或修改：

```text
tests/test_feishu_commands.py
tests/test_feishu_integration.py
```

已覆盖：

- “项目地图” -> card 中出现 `architecture`
- “最近变更” -> card 中出现 `version_change`
- “最近故障” -> card 中出现 `incident_diagnosis`
- “知识库缺什么” -> card 中出现 `project_knowledge`
- command rewrite 后不影响 event dedup

仍建议后续补充：

- 空消息仍 ignored 的显式测试。
- 未映射 chat 仍 403 的快捷入口回归测试。
- “负责人是谁”入口的端到端测试。

验收命令：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_feishu_commands.py tests/test_feishu_integration.py -q
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m ruff check src tests
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest -q
```

最近一次已通过：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_feishu_commands.py tests/test_feishu_integration.py -q
# 15 passed, 1 warning
```

## 7. Phase 9：知识库增强

目标：让知识库从 demo 文件升级为真正能接入项目资料的 Knowledge Loop。

### 7.1 Feishu 文档 Connector

新增：

```text
src/project_lens/context/sources/feishu_docs.py
src/project_lens/context/indexing/feishu_documents.py
```

输入建议：

- `tenant_id`
- `project_id`
- `doc_token`
- `doc_url`
- `revision`
- `updated_at`
- `owner_user_id`
- `access_scope`

输出必须是 `Evidence`，不能直接返回文本答案。

关键要求：

- 必须带 `source.system = "feishu_doc"`
- 必须带 `source.url`
- 必须带 `metadata.revision`
- 必须带 `metadata.doc_token`
- 必须带 `access_scope`

测试：

```text
tests/test_feishu_document_indexing.py
```

覆盖：

- 文档转 Evidence
- revision 变化会生成新 hash
- ACL 不匹配不可检索
- 空文档或无权限文档不进入结果

### 7.2 Git 变更 Connector

新增：

```text
src/project_lens/context/sources/git_changes.py
src/project_lens/context/indexing/git_changes.py
```

先做本地 Git，不急着接 GitHub/GitLab API。

输出 Evidence 类型：

- `EvidenceType.COMMIT`
- `EvidenceType.PULL_REQUEST`（本地可以先用 fixture 模拟）

metadata 建议：

- `commit_sha`
- `author`
- `branch`
- `files_changed`
- `insertions`
- `deletions`
- `parent_sha`
- `release_tag`

必须能支持：

- 按 project 过滤
- 按 branch 过滤
- 按 time_range 过滤
- commit -> timeline event
- commit -> change impact

测试：

```text
tests/test_git_change_indexing.py
tests/test_context_engine.py
```

### 7.3 任务/发布/事故资料

新增 Evidence 类型或沿用现有类型：

- `TASK`
- `INCIDENT`
- `DOCUMENT`
- 后续可加 `RELEASE`

如果要新增 `EvidenceType.RELEASE`，必须补：

- domain model 测试
- timeline builder
- change impact builder
- API serialization 测试

建议短期先用 `DOCUMENT` 或 `TASK` 携带 release 信息，避免改动太大。

## 8. Phase 10：知识图谱增强

目标：让项目不只靠文本检索，还能回答“谁影响谁”“哪个模块依赖哪个服务”“这个事故关联哪些提交和任务”。

### 8.1 图谱实体

图谱实体建议：

```text
Project
Service
Repository
Module
Symbol
Endpoint
Owner
Task
Release
Commit
PullRequest
Incident
Decision
Document
Environment
```

### 8.2 图谱关系

建议关系：

```text
Project HAS_SERVICE Service
Service OWNS_MODULE Module
Module DEFINES Symbol
Endpoint CALLS Service
Commit CHANGES Module
PullRequest CONTAINS Commit
Release INCLUDES Commit
Incident AFFECTS Service
Incident CAUSED_BY Commit
Task TRACKS Incident
Document DESCRIBES Project/Service/Decision
Owner RESPONSIBLE_FOR Service/Module
```

### 8.3 实现路径

当前已有：

```text
src/project_lens/graph/models.py
src/project_lens/graph/builder.py
```

下一步新增：

```text
src/project_lens/graph/query.py
src/project_lens/context/graph_service.py
```

`ContextEngine` 增加只读方法：

```python
def query_graph(
    self,
    project: ProjectRef,
    access: AccessContext,
    query: GraphQuery,
) -> tuple[GraphEvidence, ...]:
    ...
```

不要让 Agent 直接访问 graph store。

### 8.4 测试

新增：

```text
tests/test_graph_query.py
tests/test_context_graph_service.py
```

覆盖：

- ACL 先于图查询
- 跨 project 节点不可返回
- commit -> module -> service 路径可解释
- incident -> service -> owner 路径可解释
- 每条 GraphEvidence 都能追溯到 Evidence ID

## 9. Phase 11：项目记忆 Memory

目标：把团队确认过的项目事实、决策、风险和负责人信息沉淀为长期记忆。

绝对不要自动把模型回答写入项目记忆。

### 9.1 Memory 类型

新增：

```text
src/project_lens/domain/memory.py
```

建议模型：

```python
class MemoryProposal(FrozenModel):
    project: ProjectRef
    proposed_by: str
    claim_text: str
    claim_type: ClaimType
    evidence_ids: tuple[UUID, ...]
    reason: str
    status: Literal["pending", "approved", "rejected"]

class ProjectMemory(FrozenModel):
    project: ProjectRef
    text: str
    evidence_ids: tuple[UUID, ...]
    approved_by: str
    valid_from: datetime
    valid_to: datetime | None = None
```

### 9.2 Workflow

流程：

```text
Agent 发现可沉淀信息
-> 生成 MemoryProposal
-> 飞书卡片展示“是否沉淀到项目记忆”
-> 人工确认
-> Knowledge.commit_memory
-> 后续检索可读取 ProjectMemory
```

必须满足：

- 提案必须引用 Evidence
- 人工确认前不能进入正式项目记忆
- 记忆要有时间有效性
- 记忆过期或被替换要可追溯

测试：

```text
tests/test_project_memory.py
tests/test_memory_approval_flow.py
```

## 10. Phase 12：Project Engineering Skill（代码编辑能力）

目标：让 Agent 可以在项目上线前后做“小范围代码修复建议”，但必须有审批和验证。

这是第一个写路径，必须遵循：

```text
Explain -> Propose -> Validate -> Apply
```

### 10.1 绝对限制

不要把代码助手的任意 shell/file write 直接暴露给飞书用户。

禁止：

- 飞书消息直接触发生产修改
- 飞书消息直接重启服务
- 飞书消息直接回滚发布
- 未审批直接创建 PR
- Agent 直接执行任意 shell

允许的最小能力：

- 读取代码
- 生成修复方案
- 在隔离 worktree 里修改
- 运行限定测试
- 输出 diff
- 创建 `ActionProposal`
- 等待人工审批

### 10.2 新增模块

```text
src/project_lens/workflow/engineering_skill.py
src/project_lens/runtime/tool_gateway.py
src/project_lens/runtime/policy.py
src/project_lens/runtime/worktree.py
src/project_lens/runtime/patch_plan.py
```

或中长期迁移到：

```text
src/insight_project/agent/execution/
```

### 10.3 Tool Gateway

借鉴 `3_mewcode-python`，但只能抽象模式，不能复制任意权限。

建议工具：

```text
read_project_file
search_project_code
create_isolated_worktree
apply_patch_plan
run_allowed_tests
summarize_diff
create_action_proposal
```

每个工具必须有：

- project scope
- risk class
- timeout
- allowed paths
- audit event
- structured result

### 10.4 测试

新增：

```text
tests/test_engineering_skill.py
tests/test_tool_gateway_policy.py
tests/test_worktree_execution.py
tests/test_patch_proposal.py
```

覆盖：

- 未审批不能 Apply
- 只能改项目允许路径
- 不允许跨 project 文件
- 测试失败不能创建 Apply 建议
- diff 必须出现在 ActionProposal
- audit event 必须记录每一步

## 11. Phase 13：ProjectOps Skill

目标：项目上线后报错或异常时，Agent 可以关联日志、指标、trace、release 和 incident。

先只读，不做生产动作。

### 11.1 原则

日志、指标、trace 不要作为普通长期文档直接写入 RAG。

它们应该通过时间窗口工具查询：

```text
query_logs(project, environment, time_range, filter)
query_metrics(project, environment, time_range, metric)
query_traces(project, environment, time_range, trace_id)
```

只把人工确认后的 incident summary 或 postmortem 作为 Evidence/Memory 沉淀。

### 11.2 新增模型

```text
OperationalSignal
OpsQuery
OpsFinding
```

### 11.3 输出

ProjectOps 回答必须区分：

- 已确认事实：来自日志/指标/trace
- 推断：release 与异常时间相关
- 假设：可能原因
- 建议：下一步验证

## 12. Phase 14：通用协作 Skill

目标：从研发团队走向更多非技术成员。

候选 Skill：

- 会议纪要整理
- 任务同步
- 跨角色解释：把技术结论翻译给产品/运营/管理
- 文档缺口识别
- 决策记录沉淀
- 负责人和行动项追踪

限制：

- 仍然复用 ProjectContext / Evidence / Run / Approval。
- 不要把通用聊天和项目知识混在一起。
- 每个通用 Skill 都要有 Connector + Policy。

## 13. Phase 15：生产化治理

目标：让系统可长期服务多个团队和项目。

需要补：

- PostgreSQL persistence adapter
- 多租户 project registry
- Feishu tenant/user/role ACL
- secret management
- cost tracking
- rate limit
- replay evaluation dashboard
- prompt/version registry
- tool audit dashboard
- memory review queue
- disaster recovery

## 14. Cursor 执行顺序建议

Cursor 不要一次做所有阶段。建议按下面顺序一段段交付：

### 第 1 个 Cursor 任务：Feishu 指令入口（已完成）

完成记录：

- `src/project_lens/integrations/feishu/commands.py` 已新增。
- `FeishuEventService` 已在创建 Run 前使用 `rewrite_feishu_message`。
- 已支持 `项目地图 / 项目架构 / 最近变更 / 版本影响 / 最近故障 / 故障复盘 / 知识库缺什么 / 负责人是谁`。
- 已新增 Skill 专属卡片视图。
- 已新增 `tests/test_feishu_commands.py` 并扩展 `tests/test_feishu_integration.py`。

历史任务描述：

```text
请在 ProjectLens 中实现 Feishu 项目指令入口。

要求：
1. 新增 src/project_lens/integrations/feishu/commands.py。
2. 支持“项目地图、项目架构、最近变更、版本影响、最近故障、故障复盘、知识库缺什么、负责人是谁”等中文短指令。
3. 将短指令 rewrite 成更稳定的项目问题，然后复用现有 workflow，不要绕过 classify_project_question。
4. 修改 FeishuEventService，在创建 AgentRun 前使用 command rewrite。
5. 新增 tests/test_feishu_commands.py。
6. 补充 tests/test_feishu_integration.py，验证简单指令能返回对应 skill 卡片。
7. 不要引入写操作，不要改权限模型。
8. 运行 ruff 和全量 pytest。
```

验收：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_feishu_commands.py tests/test_feishu_integration.py -q
# 15 passed, 1 warning
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m ruff check src tests
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest -q
```

### 第 2 个 Cursor 任务：知识库缺口报告（下一步推荐）

任务描述：

```text
请把当前“知识库缺什么”快捷入口升级成结构化 KnowledgeGapReport。

要求：
1. 不新增 LLM 调用，先基于 ProjectSnapshot.unresolved_items、retrieval warnings、Evidence 类型覆盖率生成 KnowledgeGap。
2. 可新增 domain model：KnowledgeGapReport。
3. ContextEngine 增加 knowledge_gaps(project, access) 只读方法。
4. API 增加 POST /api/v1/projects/knowledge-gaps。
5. Feishu 指令“知识库缺什么”当前已能进入 `project_knowledge`，请升级为真正读取 KnowledgeGapReport 的专属卡片。
6. 所有结果必须 ACL 过滤后生成。
7. 添加 context/API/Feishu 测试。
```

验收：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_context_engine.py tests/test_api.py tests/test_feishu_integration.py -q
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest -q
```

### 第 3 个 Cursor 任务：Git 变更 Evidence

任务描述：

```text
请实现本地 Git 变更索引。

要求：
1. 新增 src/project_lens/context/indexing/git_changes.py。
2. 从本地 git log 生成 EvidenceType.COMMIT Evidence。
3. Evidence.metadata 至少包含 commit_sha、author、branch、files_changed。
4. commit Evidence 必须能进入 timeline 和 change-impact。
5. 不要直接调用远程 GitHub/GitLab。
6. 添加 tests/test_git_change_indexing.py 和 change-impact 回归测试。
```

注意：如果当前目录不是 git repo，测试要用临时 fixture repo 或 fake log parser，不要依赖项目本身是 git repo。

### 第 4 个 Cursor 任务：图谱查询服务

任务描述：

```text
请补充 ProjectLens 图谱查询服务。

要求：
1. 基于现有 src/project_lens/graph/models.py 和 builder.py 扩展。
2. 新增 graph/query.py，支持按实体类型和关系查询。
3. ContextEngine 暴露 query_graph(project, access, query)。
4. Agent/workflow 只能通过 ContextEngine 查询图谱，不能直接访问 graph builder/store。
5. GraphEvidence 必须引用 Evidence ID。
6. 测试 ACL、跨项目隔离、incident->service、commit->module 路径。
```

### 第 5 个 Cursor 任务：项目记忆提案

任务描述：

```text
请实现项目记忆提案，不要自动写入正式记忆。

要求：
1. 新增 MemoryProposal / ProjectMemory 模型。
2. Agent 可以在 answer 中推荐“沉淀到项目记忆”的 ActionProposal。
3. 新增 pending memory store。
4. 新增 approval endpoint 或复用现有 approval flow。
5. 只有 approved 后才能进入 ProjectMemory。
6. ProjectMemory 必须保留 evidence_ids、approved_by、valid_from。
7. 添加 memory approval flow 测试。
```

### 第 6 个 Cursor 任务：Project Engineering 只到 Propose/Validate

任务描述：

```text
请开始 Project Engineering Skill，但只实现 Explain -> Propose -> Validate，不实现生产 Apply。

要求：
1. 新增工程 Skill 路由，但默认不开启写入。
2. 支持根据 traceback 生成 patch plan。
3. 在隔离 worktree 或临时目录中应用 patch plan。
4. 运行允许列表中的测试。
5. 输出 diff、测试结果和 ActionProposal。
6. 未审批不能 Apply。
7. Feishu 卡片只能展示方案和审批需求，不能直接执行生产修改。
```

## 15. 每次提交前必须检查

Cursor 每完成一个任务必须运行：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m ruff check src tests
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest -q
```

如果改了 Feishu：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_feishu_integration.py tests/test_feishu_http_and_acl.py -q
```

如果改了 Context / Knowledge：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_context_engine.py tests/test_context_indexing.py tests/test_graph_and_replay.py -q
```

如果改了 Workflow / Agent：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_workflow.py tests/test_runtime_loop.py tests/test_runtime_security.py tests/test_runtime_tools.py -q
```

## 16. Cursor 开发禁区

Cursor 不要做这些事：

1. 不要把飞书消息直接映射成 shell 命令。
2. 不要让 Agent 绕过 ACL 访问 EvidenceIndex、向量库或图数据库。
3. 不要把模型生成的回答直接写入项目记忆。
4. 不要在 Feishu adapter 里写业务分析逻辑。
5. 不要在未审批时修改项目代码、发布、回滚或重启服务。
6. 不要把日志/指标/trace 永久作为普通文档塞进 RAG。
7. 不要大规模重命名目录，除非先加兼容层并保证全量测试通过。

## 17. 当前优先级排序

最推荐 Cursor 先做：

1. 知识库缺口报告。
2. Git 变更 Evidence。
3. 图谱查询服务。
4. 项目记忆提案。
5. Project Engineering Propose/Validate。
6. ProjectOps 只读信号查询。
7. 通用协作 Skill。

原因：

- 先提高飞书里的可用性，用户马上能感知。
- 再补知识来源，让回答更真实。
- 再加强图谱和记忆，让系统越用越懂项目。
- 最后再进入代码修改和运维能力，避免过早引入高风险写路径。

## 18. 建议给 Cursor 的总提示词

可以直接复制下面这段给 Cursor：

```text
你正在继续开发 C:\Users\Administrator\Desktop\project-lens。

请先阅读：
1. docs/project-agent-final-plan.md
2. docs/projectlens-next-plan-simple.md
3. docs/cursor-development-plan.md
4. docs/architecture.md
5. README.md

当前项目是 Hermes Runtime + ProjectLens Kernel。必须保持边界：
- Hermes 负责对话、工具循环、飞书网关、session、profile。
- ProjectLens 负责 ProjectSpace、Evidence、RAG/GraphRAG、RolePolicy、Gateway、Verifier、Memory。
- `/project` 只做 debug；正式入口是自然语言项目问答。
- 角色策略生成前进入运行时；RoleView 只做展示翻译。

不要引入未审批写操作。所有事实必须引用 Evidence。所有检索必须先 ACL 过滤。Apply 保持关闭。
按 docs/project-agent-final-plan.md 推进 ProjectSpace / RolePolicy，不要继续堆 Skill 或卡片。
每完成一步运行：
  & 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m ruff check src tests
  & 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest -q
```
> 2026-08-05 Phase 3 tool-loop live 联调已通过：Hermes plugin 对齐 `register_tool(name, toolset=\"projectlens\", schema, handler)`；`run_hermes_projectlens_tool_registry_smoke` 发现 5 个 formal tools 并 `registry.dispatch` 成功；payment（search/read/query_graph）与 crm ProjectSpace 切换 live HTTP smoke 通过；`allow_apply=false`；正式链路不经 `/project`。文档见 `docs/phase3-hermes-tool-loop-smoke.md`。下一步可接飞书自然语言入口进入 Hermes tool loop（本轮不做）。
>
> 2026-08-05 Phase 3 第一块地基已落地：新增 `docs/phase3-hermes-tool-loop-smoke.md` 和 `run_projectlens_tool_loop_smoke()`。后续 Cursor 不要继续把 `/project` 当正式入口；正式链路应验证 Hermes 发现 5 个 `projectlens_*` 只读工具，并通过 `POST /api/v1/project-agent/tools/call` 调用 ProjectLens Kernel。`/project` / `projectlens_ask_project` 只保留 debug/smoke；不改 Hermes core；不打开 Apply / PR / deploy / rollback / restart。
> 2026-08-06 Phase 3.1 已补：新增 `docs/phase3-feishu-hermes-tool-loop-bridge.md` 与 `FeishuHermesToolLoopBridge`。飞书自然项目问题现在可以通过 `PROJECT_LENS_FEISHU_USE_HERMES_TOOL_LOOP=true` 进入 Hermes-style tool loop；默认仍关，旧 workflow/read_agent 不受影响。下一步只剩“把轻量 planner 替换成真正 Hermes LLM/tool loop”，不要再回到 `/project` 或卡片/Skill 过滤路线。

> 2026-08-06 Phase 3.1 续：`FeishuHermesToolLoopBridge` 已删除规则 `_plan()`，改为可注入 `HermesLoopRunner`；生产默认 `HermesAIAgentLoopRunner`（Hermes `AIAgent` + `enabled_toolsets=["projectlens"]`）。Hermes 不可用不 silent fallback。测试用 Fake runner 证明工具选择不同于旧规则（如仅 `authorized_evidence`），并保留 `tool_calls` / `citations` / `evidence_refs` / `audit_ref` / `allow_apply=false`。文档见 `docs/phase3-feishu-hermes-tool-loop-bridge.md`。下一步可做 session/profile 或审计映射，不要再加卡片/Skill。
