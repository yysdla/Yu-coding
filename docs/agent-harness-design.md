# ProjectLens Agent Harness 设计说明

本文档用于指导 Cursor 继续把 ProjectLens 从“飞书项目分析工具”推进为“可长期运行、可审计、可扩展、可审批的项目协作 Agent”。设计参考 `3_mewcode-python` 代码助手项目的 harness 思路，但不能照搬代码助手的任意 shell / 文件写入能力。

核心原则：

> LLM 只负责理解、推理和生成候选动作；Harness 负责上下文、权限、工具、状态、验证、审批、回滚、审计和交付。

## 1. Harness 的目标

ProjectLens 的 Agent harness 要解决这些问题：

1. 多轮飞书对话不丢上下文。
2. 长任务和工程任务不因为上下文压缩丢文件、diff、测试和审批状态。
3. Agent 不能绕过 ACL、ToolGateway、Approval。
4. 所有事实必须来自 Evidence 或 Approved Memory。
5. 所有写路径必须先 Explain -> Propose -> Validate -> Approval -> Apply。
6. 后台任务、同步任务、文档索引、工程验证都有可复盘的 Run/Event/Audit。
7. 可以从一次中断、压缩或重启后恢复任务状态。

## 2. 从代码助手项目借鉴什么

从 `3_mewcode-python` 借鉴的是 harness 模式，不是直接暴露能力。

| 代码助手 harness 能力 | ProjectLens 中的落点 | 注意 |
|---|---|---|
| Agent loop / driver / conversation | `workflow/` + `runtime/loop.py` + future `conversation_service.py` | 飞书消息要先绑定 ProjectContext |
| Tool schema / tool search / MCP | `runtime/tools.py` + `runtime/tool_gateway.py` | 工具必须 project-scoped 和 risk-classed |
| Permission / sandbox | `runtime/policy.py` + `context/access.py` | 权限在 gateway 和 context 边界强制执行 |
| Worktree | `runtime/worktree.py` + `workflow/engineering_skill.py` | 只用于 Validate，不直接生产 Apply |
| Hooks / lifecycle | `runtime/events.py` + Run events + audit logs | 用于 Feishu 进度、审计、回放 |
| Session memory / compaction | future `ConversationSession` + `ContextPack` | 只能做运行态上下文，不能绕过 ProjectMemory 审批 |
| Skills | `workflow/skills.py` + future Skill registry | Skill + Connector + Policy 扩展 |
| Specialist / teams | `workflow/specialists.py` + future specialist registry | 多 specialist 输出结构化 evidence/findings，不比拼散文答案 |

不要照搬：

- 任意 shell。
- 任意文件写入。
- 任意 MCP 工具暴露。
- 助手本地 memory 直接成为项目事实。
- UI 层权限选择替代后端权限校验。

## 3. 五层上下文压缩机制

ProjectLens 应采用“逻辑五层、工程六块”的上下文机制。模型每次工作时不吃完整历史，而是由 ContextPack 装配必要上下文。

### L0：不可压缩锚点层

永远不压缩，只版本化或精确替换。

包含：

- System / developer / project policy。
- Tool schema。
- Skill registry。
- Approval rules。
- ProjectContext。
- AccessContext。
- 当前 allow_apply / risk policy。
- 当前 connector 配置边界。

示例：

```text
tenant_id=demo
project_id=payment
service=order-service
environment=production
access_scope=project:payment:read
allow_apply=False
```

规则：

- 不能总结成“当前用户有权限”。
- 不能把 tool schema 压缩成自然语言。
- 不能让 LLM 改写 policy。

### L1：当前活跃窗口层

保留最近飞书对话和当前 Run 的短窗口。

包含：

- 最近 4-8 轮用户消息。
- 最近一次机器人回答摘要。
- 当前用户原文。
- rewrite 后的问题。
- 当前 run_id / trace_id。
- 当前 Feishu card 状态。

策略：

- 群聊默认保留最近 4-6 轮。
- 私聊或工程任务可保留 8 轮。
- 超出后转入 L2。

### L2：滚动会话摘要层

Claude Code compact 后留下的“当前任务摘要”应落在这一层。

固定结构：

```markdown
## Session Intent

## Project Context

## Active Topic

## Verified Facts

## Inferences

## Unknowns

## Decisions

## Next Actions

## Pinned IDs
```

必须保留：

- run_id
- trace_id
- evidence_id
- proposal_id
- memory_id
- incident_id
- commit_sha
- doc_token
- file path
- symbol name

策略：

- 使用 anchored iterative summarization。
- 只压缩新移出 L1 的 turns。
- 合并到现有 summary，不要每次重写完整 summary。
- 被压缩内容必须带 compression_cycle。

### L3：任务工作台 / Scratchpad 层

这是代码助手 harness 里最重要的任务状态层。它不是聊天摘要，而是结构化工作台。

包含：

- 当前 plan。
- 当前 phase。
- files_read。
- files_changed。
- patch_plan。
- diff 摘要。
- test_commands。
- test_results。
- tool_audit_events。
- approval status。
- subtask 状态。
- failed attempts。
- rejected plans。

Engineering 示例：

```text
phase=Validate
files_read=examples/payment_service/src/order_service.py
patch_plan=Add null guard for optional coupon
diff=...
test_command=pytest tests/test_order_service.py
test_passed=true
approval_status=pending
allow_apply=false
```

规则：

- L3 尽量结构化存储，不能只依靠 LLM 摘要。
- 文件路径、测试命令、diff 摘要、审批状态必须原样保留。
- 原始 tool events 可写入 append-only audit，prompt 里只放压缩后的 task state。

### L4：证据 / Artifact 检索层

不常驻 prompt，通过 ID 和检索按需加载。

包含：

- Evidence。
- ProjectSnapshot。
- TimelineEvent。
- ChangeImpact。
- KnowledgeGapReport。
- GraphEvidence。
- OpsFinding。
- FeishuDocSyncState。
- Git commit evidence。
- Task / Release evidence。

策略：

- prompt 里放 evidence_id + source_id + snippet + citation。
- 需要细节时通过 ContextEngine 再取。
- Agent 不直接访问 EvidenceIndex / graph store / vector store。
- 所有 Evidence 先 ACL 过滤，再进入 LLM 上下文。

### L5：长期项目记忆层

跨会话复用的长期知识。

包含：

- Approved ProjectMemory。
- 人工确认的负责人。
- 人工确认的架构事实。
- 决策记录。
- 风险规则。
- 复盘结论。
- 团队约定。

规则：

- L5 只能由 MemoryProposal + 人工审批写入。
- L2 会话摘要不能自动升级为 ProjectMemory。
- L5 内容也必须保留 evidence_ids、approved_by、valid_from、valid_to。

## 4. ContextPack：五层上下文装配器

`ContextPack` 已落地为结构化载体；模型可读上下文由 `context_prompt.py` deterministic renderer 从 ContextPack 生成（尚未接真实 LLM）。

位置：

```text
src/project_lens/workflow/context_pack.py
src/project_lens/workflow/context_prompt.py
```

建议模型：

```python
class ContextPack(FrozenModel):
    anchors: AnchorContext
    recent_turns: tuple[ConversationTurn, ...]
    session_summary: ConversationSummary | None
    task_state: TaskScratchpad | None
    evidence: tuple[Evidence, ...]
    memories: tuple[ProjectMemory, ...]
```

装配流程：

```text
Feishu message
-> identity mapping
-> load ConversationSession
-> rewrite follow-up
-> resolve ProjectContext
-> retrieve Evidence / Memory / Graph / Ops as needed
-> build ContextPack
-> ProjectWorkflow.execute
-> update ConversationSession + TaskScratchpad + RunEvents
```

## 5. ConversationSession：多轮飞书对话

已落地；会话存储支持 SQLite（默认）与 InMemory fallback。

```text
src/project_lens/domain/conversation.py
src/project_lens/application/conversation_service.py
src/project_lens/context/conversation_store.py  # InMemory + SQLite
src/project_lens/workflow/followup.py
```

模型：

```python
class ConversationTurn(FrozenModel):
    run_id: UUID | None
    user_id: str
    text: str
    rewritten_question: str | None
    created_at: datetime

class ConversationSummary(FrozenModel):
    project: ProjectRef
    active_skill: str | None
    active_topic: dict[str, str]
    verified_claim_ids: tuple[UUID, ...]
    evidence_ids: tuple[UUID, ...]
    unknowns: tuple[str, ...]
    next_actions: tuple[str, ...]
    artifact_refs: tuple[str, ...]
    updated_at: datetime

class ConversationSession(FrozenModel):
    id: UUID
    tenant_id: str
    chat_id: str
    user_id: str | None
    project: ProjectRef
    last_run_id: UUID | None
    recent_turns: tuple[ConversationTurn, ...]
    summary: ConversationSummary
    expires_at: datetime
```

第一版不要用 LLM 压缩，先做 deterministic update：

- 从 ProjectAnswer 提取 skill、claims、evidence、unknowns、actions。
- 从 EngineeringProposal 提取 files、patch、diff、tests。
- 从 MemoryProposal 提取 proposal_id、status。
- 从 FeishuDocSyncResult 提取 doc_tokens、revision、failure。

## 6. FollowupRewriter：追问改写

飞书多轮里，“那是谁改的？”这类问题必须改写成完整问题。

建议位置：

```text
src/project_lens/workflow/followup.py
```

第一版规则驱动：

| 原始追问 | 依赖上下文 | rewrite |
|---|---|---|
| 那是谁改的 | incident/version topic | 查找相关 commit、任务和负责人 |
| 影响哪里 | incident/version topic | 分析变更影响面和受影响服务 |
| 怎么修 | incident/code topic | 生成只读 Engineering 修复提案 |
| 发给产品看 | any topic | 生成业务可读摘要 |
| 还缺什么 | any topic | 查询 KnowledgeGapReport |
| 同步了吗 | docs topic | 查询 Feishu 文档同步状态 |

规则：

- rewrite 后仍走现有 ProjectWorkflow。
- 不在 Feishu adapter 里做业务分析。
- rewrite 结果要写入 ConversationTurn，方便审计。

## 7. Tool Harness

代码助手项目最核心的是工具 harness。ProjectLens 也必须让所有工具走统一 gateway。

建议工具分层：

```text
Read tools:
- search_context
- get_project_snapshot
- get_timeline
- get_change_impact
- query_graph
- query_logs
- query_metrics
- query_traces
- read_project_file
- get_doc_sync_status

Validate tools:
- create_patch_plan
- validate_patch_plan
- run_allowed_tests
- summarize_diff

Approval tools:
- create_action_proposal
- create_memory_proposal
- decide_memory_proposal

Apply tools:
- apply_patch_plan
- create_pr
- rollback_release
- restart_service
```

当前阶段：

- Read tools 可用。
- Validate tools 可用但必须隔离。
- Approval tools 可用。
- Apply tools 禁止或只保留 stub。

每个 Tool 必须声明：

```text
tool_name
category
risk_class
project_scope
allowed_paths
timeout
idempotency
requires_approval
audit_payload
```

## 8. Surface 分类

参考代码助手 harness，要把环境分成四类 surface。

| Surface | ProjectLens 例子 | 规则 |
|---|---|---|
| Locked | eval tests、policy、ACL、tool schema、approval rule | Agent 可读，不可改 |
| Editable | isolated worktree、patch draft、prompt draft、卡片文案 | Agent 可在范围内改 |
| Append-only | RunEvent、ToolAuditEvent、sync log、memory proposal log | 只能追加，不可改历史 |
| Human-controlled | Apply、PR、发布、回滚、重启、正式 ProjectMemory | 必须人工审批 |

实现要求：

- Locked surface 不允许 agent 自己改 evaluator 再自评。
- Append-only logs 不允许 rewrite。
- Human-controlled action 必须有 ApprovalRequest 和 audit。

## 9. Hooks / Lifecycle

代码助手里 hook 很重要。ProjectLens 也需要生命周期 hook。

建议事件：

```text
run.created
run.resolved
context.collected
context.prompt_rendered
analysis.completed
verification.completed
answer.composed
tool.requested
tool.completed
approval.requested
approval.decided
memory.proposed
memory.approved
engineering.proposed
engineering.validated
doc_sync.started
doc_sync.completed
ops_signal.queried
compression.triggered
compression.completed
```

用途：

- Feishu 进度通知。
- audit。
- replay。
- evaluation。
- 后台监控。
- 上下文压缩触发。

## 10. Approval Harness

审批不是 Feishu 卡片按钮这么简单，而是一条受控状态机。

状态：

```text
pending -> approved
pending -> rejected
pending -> expired
```

审批对象：

- MemoryProposal。
- Engineering Apply。
- Create PR。
- Release rollback。
- Config change。
- Production restart。

每个审批必须记录：

```text
approval_id
proposal_id/action_id
project
requested_by
allowed_approvers
decided_by
decision
expires_at
rollback_plan
audit_events
```

近期重点：

- Memory 审批可继续完善 allowed_approvers。
- Engineering Apply 继续禁止，直到审批权限和 rollback plan 完整。

## 11. Worktree / Sandbox Harness

工程能力必须隔离。

流程：

```text
Explain
-> Propose PatchPlan
-> create isolated worktree
-> apply patch in worktree
-> run allowed tests
-> collect diff/test result
-> create ActionProposal
-> wait approval
```

限制：

- 不改主仓库。
- 不允许任意 shell。
- test command 白名单。
- path 必须在 project_root 下。
- diff 必须展示给用户。
- Apply 默认关闭。

## 12. Evaluation / Replay Harness

**状态：已完成第一版（不要继续横向扩展 evaluation CLI）。**

实现位置：

```text
src/project_lens/evaluation/harness_probes.py
src/project_lens/evaluation/harness_replay.py
tests/test_harness_replay.py
```

已覆盖 probe：

| Probe | 检查什么 |
|---|---|
| Context recall | 多轮追问是否记得 active incident / commit / doc_token |
| Artifact trail | 是否记得读过/改过哪些文件 |
| Evidence safety | claim 是否引用授权 Evidence |
| Approval safety | 未审批是否无法 Apply |
| Compression quality | 压缩后是否保留 run_id/evidence_id/proposal_id/file path |
| Ops boundary | logs/metrics/traces 是否没有进入长期 RAG |
| Memory boundary | 未审批内容是否没有进入 ProjectMemory |
| Lifecycle trail | 关键 lifecycle hooks 是否出现 |

相关测试：

```text
tests/test_conversation_session.py
tests/test_context_pack.py
tests/test_followup_rewriter.py
tests/test_context_compression.py
tests/test_harness_boundaries.py
tests/test_lifecycle_hooks.py
tests/test_approval_harness.py
tests/test_harness_replay.py
tests/test_conversation_persistence.py
tests/test_context_prompt.py
```

## 13. Cursor 下一步建议

不要继续横向加新业务能力，也不要扩展 evaluation CLI。

### 已完成

**第一刀：** ConversationSession + FollowupRewriter、ContextPack、TaskScratchpad、deterministic compression、ToolSpec/boundaries、Approval harness、Lifecycle hooks、Evaluation/Replay probes。

**第二刀：** ConversationSession SQLite 持久化（`PROJECT_LENS_CONVERSATION_STORE=sqlite|memory`）+ `context_prompt.py` L0-L5 renderer。

**第三刀：** Run 路径强制 `ContextPack -> ContextPrompt -> ModelAdapter`；lifecycle `context.prompt_rendered`；SQLite 重启后多轮追问可恢复。

**第四刀：** 可插拔 `workflow.providers.ModelProvider`（默认 Stub；预留 OpenAI/Internal/Local，live=False）；`ProviderModelAdapter` 统一 timeout/retry/usage audit；配置 `PROJECT_LENS_MODEL_PROVIDER` 等。答案仍走 analyze/verify/compose。

**Feishu Audit 卡片已完成：** `integrations/feishu/audit_summary.py` + 回答卡片「本次回答依据 / Audit 摘要」；只展示安全 refs。

### 下一阶段候选

仍不接真实 LLM、不做 Apply：

1. **Worktree Validate harness 收口**（隔离验证、白名单测试、diff 展示；Apply 继续关闭）。
2. 在 Provider 接口稳定后，再考虑受控 live 接入（仍必须吃 ContextPrompt）。

### 仍不要做

- 新的业务 Skill
- Engineering Apply
- Feishu adapter 直接访问 EvidenceIndex 或自拼 prompt
- 真实 LLM 调用（直到 ContextPrompt 入口稳定）
- evaluation CLI 横向扩展
- 把 session summary 写入 ProjectMemory

## 14. 给 Cursor 的提示词

可以直接发：

```text
请阅读 docs/agent-harness-design.md 与 docs/cursor-development-plan.md。

第一～三刀 harness 已收口（会话持久化、ContextPrompt、StubModelAdapter 接线）。
不要扩展 evaluation CLI，不要接真实 LLM。

下一步可选：
1. Worktree Validate harness 收口（不 Apply）
2. 可插拔 ModelProvider 接口（仍用 stub/fixture）
3. Feishu 只展示 prompt/adapter audit refs

禁止：
- Feishu adapter 直接拼 prompt 或访问 EvidenceIndex
- 未审批写 ProjectMemory / Apply
- 引入真实 LLM

完成后运行：
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m ruff check src tests
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest -q
```

