# ProjectLens Context Composer 开发实施方案

版本：v0.1  
日期：2026-09-05  
状态：设计稿，尚未实现  
适用范围：ProjectLens 当前只读 AgentRun、飞书私聊/群聊与本地 SQLite 持久化链路

## 1. 一句话定义

**ProjectLens Context Composer（上下文编排器）** 是一个让已授权用户在发起一次 AgentRun 前，显式选择、排序并预览“本次模型可以看到什么”的上下文快照编辑器。

它不是聊天记录删除器，也不直接改变项目中的真实状态。

```text
可信身份 + EffectiveAccessScope
-> 可授权的上下文候选项
-> 用户选择、排序、预算控制
-> ContextSelectionDraft
-> Prompt 预览
-> 不可变 ContextSnapshot
-> AgentRun(context_snapshot_id)
-> 统一 Prompt 渲染 / Agent 执行 / 审计
```

## 2. 产品边界

### 2.1 MVP 要解决的问题

当前 Agent 会自动携带最近会话、摘要、证据和已审批记忆。这个默认行为方便，但在以下场景里不够可控：

- 用户要针对一个旧问题重新调查，不希望历史中的已废弃讨论影响本次回答；
- 用户需要在 Token 预算内保留关键决策、剔除噪声；
- 用户希望明确知道模型本次看到了哪些消息、文件读取结果、证据和记忆；
- 团队需要回放一次回答为何会使用某段上下文。

MVP 给用户一套“本次运行”的选择器：可选、可取消、可排序、可预览、可保存运行快照。

### 2.2 核心不变式

1. **选择只影响本次快照。** 取消历史项不删除原始会话，不回滚 Git，不删除文件、数据库记录、Evidence 或 ProjectMemory。
2. **快照冻结。** 运行创建后，模型实际使用的上下文集合不能被事后修改。
3. **授权优先于选择。** 用户只能看见并选择当前 `EffectiveAccessScope` 允许的候选项；前端传来的 ID 不是授权凭据。
4. **发送前再次授权。** Draft 创建时和 Snapshot 冻结时均需检查 tenant、project、actor、chat、ACL、证据撤销和记忆有效期。
5. **模型上下文必须走统一渲染器。** 预览与真实 Agent 看到的上下文必须来自同一个 `ContextSnapshot -> ContextPack -> ContextPrompt` 路径。
6. **审计保留引用，不默认保留敏感正文。** 保存来源 ID、顺序、Token 估算、策略版本和哈希；不在运行审计中重复存完整 Prompt、文件全文或 Secret。
7. **用户控制详细历史，系统保护任务连续性。** 系统不偷偷注入用户未选择的普通历史，但必须保留不可取消的安全锚点，并对缺少任务状态、最近运行结果或必要背景的选择显示完整性警告。

### 2.3 明确非目标

MVP 不做：

- 原始消息永久删除、编辑或“从历史中抹除”；
- 根据取消勾选自动撤销项目记忆、Git、数据库或飞书任务；
- 自动推断 `H2 -> M1` 这类对话到记忆的依赖图；
- 自动重新生成长期记忆、摘要或任务计划；
- 自动写代码、创建 PR、部署、回滚或修改外部系统；
- 用用户提供的 `role`、`tenant_id` 或 `project_id` 绕过服务端权限判断。

## 3. 与现有 ProjectLens 的对应关系

| 现有对象 | 当前作用 | Context Composer 中的定位 | MVP 改动 |
| --- | --- | --- | --- |
| `ConversationSession` | 最近轮次、滚动摘要、任务 scratchpad | 运行时会话缓存，不是完整历史库 | 继续保留；另建完整历史存储 |
| `ConversationTurn` | 单轮用户消息，存在于 session 的 `recent_turns` | `conversation_turn` 候选项的领域模型基础 | 增加持久化 ID、角色、可见范围等元数据 |
| `ConversationSummary` | L2 滚动摘要 | `session_summary` 候选项 | 可选择是否加入本次快照 |
| `TaskScratchpad` | L3 工作状态 | `task_state` 候选项 | 可选择是否加入本次快照 |
| `Evidence` | 外部来源的已读取事实 | `evidence` 候选项 | 只引用可见、未撤销的 Evidence |
| `ProjectMemory` | 已审批的长期事实 | `project_memory` 候选项 | 不因取消历史而变化 |
| `ContextPack` | L0-L5 结构化单次上下文 | Snapshot 的运行载体 | 支持从 Snapshot 精确组装 |
| `ContextPrompt` | `ContextPack` 的确定性渲染结果 | 预览和真实 Prompt 的共同来源 | 增加 snapshot 审计引用 |
| `AgentRun` | 正式一次 Agent 运行 | 绑定不可变快照 | 新增 `context_snapshot_id` |
| `EffectiveAccessScope` | 当前 actor/project/chat 的权限交集 | 候选查询与发送二次校验的前提 | 复用，不在客户端重算 |

### 3.1 为什么不能只复用 `ConversationSession`

现有 `ConversationSession.recent_turns` 默认只保存约六轮，旧轮次会被压缩进 `ConversationSummary`。这非常适合普通多轮问答，却不能提供“从完整历史任意挑选 H2、H17、H46”的能力。

因此 Composer 需要单独的**追加写入完整对话历史**。Session 仍是高频、短生命周期的工作缓存；完整历史是可查询、带权限边界、可被快照引用的事实记录。二者不能互相替代。

### 3.2 为什么必须统一三条上下文路径

当前仓库存在至少三类上下文拼装入口：

1. 主 Workflow：`ContextPack -> ContextPrompt`；
2. read-agent：`build_investigation_session_brief()`；
3. 飞书 Hermes：`build_hermes_project_context()`。

如果 Composer 只接到其中一条，UI 的“预览上下文”就可能与模型实际看到的内容不同。MVP 的架构原则是：**所有可对外发送的 Agent 请求最终都必须接受同一个已冻结 Snapshot，并由共享 renderer 生成最终上下文。**

短期可允许各运行模式保留各自 adapter，但 adapter 只能把 Snapshot 转成模式需要的输入，不能再私自追加未出现在 Snapshot 中的会话、记忆或证据。

## 4. 四类信息与数据所有权

| 类型 | 例子 | 是否可勾选 | 取消勾选意味着什么 | 真实来源是否受影响 |
| --- | --- | --- | --- | --- |
| 对话历史 | 用户问题、Agent 回复、确认结论 | 是 | 本次不放入 Prompt | 否 |
| 外部状态 | 文件、Git diff、数据库查询、API 结果 | 通过“已读取结果引用”勾选 | 本次不带这次读取结果 | 否 |
| 派生信息 | 摘要、任务状态、ProjectMemory | 是 | 本次不注入该派生项 | 否 |
| 上下文快照 | 一次发送前冻结的组合 | 不可直接编辑 | 创建新 Snapshot | 否 |

外部状态需要额外强调：`src/login.vue` 存在于工作区，是现实状态；“本次读取到的 `src/login.vue` 内容”才是可被 Snapshot 引用的 `external_state_ref` 或 Evidence。取消它不会让文件消失。

## 5. MVP 用户体验

### 5.1 主流程

1. 用户在飞书私聊或项目群中发起“编排本次上下文”。
2. 服务端按可信身份和 `EffectiveAccessScope` 查询候选项。
3. 将候选分成三层：不可取消的运行锚点、默认推荐的状态摘要、用户自由选择的详细历史和证据。默认勾选与当前 AgentRun 兼容的推荐集合：当前问题、当前任务状态、最近摘要、最近一次已验证运行结果和可见的已审批记忆。
4. 用户勾选/取消、拖动排序，必要时搜索或按类型筛选。
5. 服务端根据 Draft 生成确定性预览、按类型统计、Token 预算和上下文完整性检查。
6. 如果用户取消了可能包含已完成事项的摘要或运行状态，卡片展示缺失背景及推荐补充项；用户可以补充，也可以明确选择“仍然发送”。
7. 用户点击发送。服务端重新授权候选项，创建不可变 Snapshot，创建 `AgentRun` 并绑定 Snapshot。
8. 所有模式使用 Snapshot 构建实际 Prompt；运行结果、Snapshot ID 和审计事件可回放。

### 5.2 MVP 飞书交互卡片

页面不是营销页，而是一个紧凑的工作台：

```text
本次上下文                                      9,840 / 16,000 tokens

[搜索候选项] [只看已选择] [按类型筛选]

对话历史
[x] 需求范围已确认               420 tokens     2026-09-05 10:12
[x] Vue 技术选型                 180 tokens     2026-09-05 10:20
[ ] 已废弃的 React 讨论          610 tokens     2026-09-05 10:31

固定上下文（不可取消）
[x] 当前项目、权限和本次问题

推荐状态（可取消，但取消会提示风险）
[x] 会话摘要                     330 tokens
[x] 当前任务：订单管理            260 tokens
[x] 最近一次运行摘要              320 tokens

证据与外部读取结果
[x] package.json                 190 tokens
[x] 当前 Git diff                2,900 tokens
[ ] 数据库查询：待支付订单        1,700 tokens

长期记忆
[x] 项目使用 Vue                 45 tokens       已审批

[预览] [保存为模板] [发送给 Agent]
```

交互规则：

- 复选框控制是否参与本次 Snapshot；
- 拖动仅改变同一 `kind` 内和全局渲染的稳定顺序，不改写原始时间线；
- 每项显示标题、短摘要、来源、时间、估算 Token 和状态；
- 固定上下文用于安全和任务定位，不允许取消；推荐状态用于告诉 Agent 已完成什么、当前做到哪里；详细历史用于补充决策过程和背景；
- 对没有权限、已撤销或已过期的项不显示正文，也不可被提交；
- Token 超预算时可预览，但“发送”禁用，并提供按 Token 降序的定位；
- 如果缺少“登录页已完成”“上次迁移已执行”等可能影响当前任务的状态，显示完整性警告和推荐补充项；不未经用户确认偷偷加入普通历史；
- 默认不弹出“取消历史会影响记忆吗”的复杂确认；MVP 中它不会影响记忆。

### 5.3 模板

模板只保存“选择规则”或固定 ID 的引用策略，例如“最近 10 轮 + 当前 Git diff + 已审批架构记忆”，而非复制敏感正文。应用模板后仍要按当前访问范围和有效性过滤。模板是便利功能，可放在 MVP 尾部或 v1.1；它不能绕过 ACL。

### 5.4 发送前上下文完整性检查

用户取消历史后，系统不能假设 Agent 仍然知道以前完成的事情。解决方案不是偷偷把被取消的内容塞回 Prompt，而是在发送前执行一次**确定性的完整性检查**，把“可能缺失的工作连续性”显式告诉用户。

检查器的职责是发现风险并推荐候选，不是替用户扩大选择范围。

```text
ContextSelectionDraft
  -> validate_refs_and_budget()
  -> check_context_completeness()
  -> ContextCompletenessReport
  -> 飞书卡片展示警告/推荐
  -> 用户选择补充或继续发送
```

建议新增领域模型：

```python
class CompletenessSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"

class CompletenessIssue(FrozenModel):
    code: str  # e.g. MISSING_TASK_STATE, OMITTED_RECENT_RUN, DUPLICATE_SUMMARY
    severity: CompletenessSeverity
    title: str
    explanation: str
    recommended_item_ids: tuple[str, ...] = ()

class ContextCompletenessReport(FrozenModel):
    draft_id: UUID
    selected_item_count: int
    known_signal_kinds: tuple[str, ...]
    omitted_kinds: tuple[str, ...]
    issues: tuple[CompletenessIssue, ...] = ()
    recommended_item_ids: tuple[str, ...] = ()
    can_send: bool = True
    checked_at: datetime
```

每个候选项需要有轻量元数据，不能只存标题和正文：

```text
coverage_tags = ["project_goal", "decision", "completed_work", "next_action", "current_state"]
importance = required | recommended | optional
freshness = current | stale | unknown
source_kind = conversation_turn | session_summary | task_state | evidence | memory | external_state_ref
```

MVP 规则建议如下：

| 检查 | 触发条件 | 默认结果 |
| --- | --- | --- |
| 必要锚点 | 项目、权限、当前问题未进入 Snapshot | 阻止发送；这些由系统自动加入，用户不能取消 |
| 任务连续性 | 问题明显是“继续实现/修复/下一步”，但未选 `task_state` 或最近已验证运行摘要 | 警告，推荐加入任务状态 |
| 已完成事项 | 候选中存在 `completed_work`/`last_run`，但均未选择 | 警告，显示“Agent 可能不知道之前做过什么” |
| 当前状态 | 需要判断代码/进度，但没有 Git/Evidence/外部读取结果 | 警告，建议读取当前状态；不自动读取 |
| 摘要覆盖 | 同时选择摘要和它已覆盖的原始轮次 | 信息提示，建议“仅摘要”或“保留原文” |
| 过期/撤销/越权 | 来源失效或不在当前 Scope | 阻止发送，不能用空内容替代 |
| Token 超预算 | 估算值超过预算 | 阻止发送 |

检查器首版应以规则为主，不让 LLM 决定是否“完整”，否则每次发送的门禁结果会不稳定。可以使用简单的问题意图标签（继续实现、复盘、解释、查询当前状态）辅助选择推荐项，但标签错误只能影响提示，不得扩大权限或阻止正常发送。

完整性检查默认只产生警告，不把“缺少历史”当成安全错误。用户可点击“仍然发送”；只有无权、失效引用和超预算才是硬阻断。这样保留用户对本次上下文的最终控制权。

### 5.5 飞书中的最重要 UX 提示

完整性报告通过更新当前互动卡片展示，不发送一条难以追踪的新消息。卡片操作值只包含 `draft_id`、`revision`、`action` 和短期签名；服务端从飞书事件重新解析 operator、tenant、chat 和项目权限。

推荐卡片结构：

```text
⚠️ 本次上下文可能不完整

取消了“登录页实现过程”和“上次运行记录”，
Agent 可能不知道登录页已经完成。

建议加入
[x] 当前任务状态       260 tokens
[x] 最近一次运行摘要   180 tokens

已选择：6 项   估算：4,812 / 16,000 tokens

[加入推荐项] [查看缺失详情]
[重新预览]   [仍然发送]
```

交互语义：

- **加入推荐项**：服务端以当前 Draft revision 为基准，将推荐 ID 合并进去，再返回新的预览；不是直接创建 Snapshot；
- **查看缺失详情**：展示 issue code、原因、推荐来源和 Token 成本，不展示用户无权访问的内容；
- **仍然发送**：只允许绕过 `WARNING`，不能绕过权限、来源失效和预算硬错误；
- **重新预览**：重新计算候选状态、hash、预算和完整性报告；
- 卡片更新使用 event_id 去重，操作人不能通过 payload 伪造 actor 或 draft owner；
- 卡片中只显示摘要和必要片段，完整历史通过分页卡片逐页查看。

服务端应保存 `ContextCompletenessReport` 的摘要引用（issue code、推荐 ID、用户最终选择），便于解释用户为什么在警告后继续发送。不要把完整卡片 JSON 当作业务事实存储。

### 5.6 Agent 的知识边界意识

完整性提示解决“用户是否意识到缺了什么”；知识边界解决“模型拿到不完整上下文后会不会假装知道”。最终 Snapshot 必须将边界写进共享 Prompt，而不是只靠飞书 UI。

建议在 `ContextPack` 增加 `selection_manifest` 或等价字段，并由 `context_prompt.py` 在 L0 固定渲染：

```text
KNOWLEDGE_BOUNDARY
- 本次运行只能把已提供的 Snapshot 内容当作已知上下文。
- 未选择的历史不等于事实为假，只表示本次没有提供。
- 没有 Evidence 或 Snapshot 内来源支持的内容，不得写成已确认事实。
- 无法确认时输出 UNKNOWN，并说明需要哪类来源；不要根据缺失历史猜测。
- 不得引用未进入 Snapshot 的历史、记忆或工具结果。
- 初始运行不得静默追加未选择的上下文；需要补充时先提出建议。
```

为了让模型真正遵守，运行链还需要三层约束：

1. **ContextPack 层**：只从 Snapshot 解析被选中的 `conversation_turn`、summary、task state、Evidence、Memory 和外部读取结果；Composer 模式下不能再默认合并 `ConversationSession.recent_turns`。
2. **工具网关层**：MVP 默认 `selected_only`。模型不能通过工具偷偷读取被取消的历史；如果未来允许读取当前 Git 或文件，必须作为显式 `context_extension` 事件记录，并重新预览/确认后才加入后续 Run。
3. **答案验证层**：复用 `verify_answer_draft()`；`FACT` 必须引用当前 Snapshot 携带的 Evidence，缺少引用只能降级为 `INFERENCE` 或 `UNKNOWN`。引用不在 Snapshot 中时拒绝或降级，不允许只靠模型自报。

飞书最终答案卡片建议增加两个小区块：

```text
本次依据：Snapshot #12，已选择 6 项
无法确认：登录页是否已完成（缺少最近运行摘要）
```

这让用户知道 Agent 的结论边界，也让后续追问可以直接生成“补充任务状态/读取当前 Git”的操作，而不是让模型继续猜。

## 6. 领域模型与数据设计

### 6.1 候选项模型

建议在 `src/project_lens/domain/context_composer.py` 新增以下领域契约。字段可根据现有 Pydantic FrozenModel 风格实现。

```python
ContextItemKind = Literal[
    "conversation_turn",
    "session_summary",
    "task_state",
    "evidence",
    "project_memory",
    "external_state_ref",
]

class ContextItemRef(FrozenModel):
    kind: ContextItemKind
    source_id: UUID | str
    ordinal: int
    token_estimate: int
    content_hash: str

class ContextSelectionDraft(FrozenModel):
    id: UUID
    project: ProjectRef
    actor_id: str
    chat_id: str | None
    base_session_id: UUID | None
    selection: tuple[ContextItemRef, ...]
    token_budget: int
    policy_version: str
    revision: int
    created_at: datetime
    updated_at: datetime

class ContextSnapshot(FrozenModel):
    id: UUID
    project: ProjectRef
    actor_id: str
    chat_id: str | None
    selection: tuple[ContextItemRef, ...]
    token_budget: int
    estimated_tokens: int
    renderer_version: str
    policy_version: str
    snapshot_hash: str
    created_at: datetime
```

设计原则：

- `ContextItemRef` 是稳定引用，不把正文复制进 Snapshot 表；
- `content_hash` 用于检测候选内容在 Draft 与冻结之间是否变化；
- `ordinal` 是用户指定的展示/渲染顺序，必须唯一且连续；
- Draft 可更新，Snapshot 只能新增、不能更新；
- `actor_id`、`project`、`chat_id` 由服务端身份解析得到，绝不接受客户端伪造；
- `snapshot_hash` 对规范化后的引用、顺序、预算、策略版本和 renderer 版本计算 SHA-256，便于审计和防篡改检测。

### 6.2 完整历史模型

建议新增 `ConversationHistoryEntry`，而不是把完整历史塞回 `ConversationSession`。

```python
class ConversationHistoryEntry(FrozenModel):
    id: UUID
    project: ProjectRef
    tenant_id: str
    chat_id: str | None
    author_actor_id: str
    role: Literal["user", "assistant", "system", "tool"]
    text: str
    run_id: UUID | None
    session_id: UUID | None
    created_at: datetime
    visibility_scope: str
    content_hash: str
```

`assistant`、`tool` 内容必须遵循现有脱敏规则。敏感工具原始输出不应以完整文本进入可选历史；应只保留已授权、裁剪后的可展示片段或 Evidence 引用。

### 6.3 SQLite 表设计

建议新增三张核心表和一张可选模板表。命名须与现有 SQLite migration 风格一致。

```sql
CREATE TABLE conversation_history_entries (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  chat_id TEXT,
  author_actor_id TEXT NOT NULL,
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  run_id TEXT,
  session_id TEXT,
  visibility_scope TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_history_scope_time
  ON conversation_history_entries(tenant_id, project_id, chat_id, created_at DESC);

CREATE TABLE context_selection_drafts (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  chat_id TEXT,
  base_session_id TEXT,
  selection_json TEXT NOT NULL,
  token_budget INTEGER NOT NULL,
  policy_version TEXT NOT NULL,
  revision INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE context_snapshots (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  chat_id TEXT,
  selection_json TEXT NOT NULL,
  token_budget INTEGER NOT NULL,
  estimated_tokens INTEGER NOT NULL,
  renderer_version TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  snapshot_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_snapshots_project_time
  ON context_snapshots(tenant_id, project_id, created_at DESC);

ALTER TABLE agent_runs ADD COLUMN context_snapshot_id TEXT;
```

`selection_json` 中只放 `kind`、`source_id`、`ordinal`、`token_estimate`、`content_hash` 等引用信息，不放内容正文。真实对象仍由所属 Store 持有。

### 6.4 Snapshot 与真实内容的关系

Snapshot 有两层含义：

1. **选择快照**：冻结选择的 ID、顺序、预算、版本和哈希；
2. **渲染快照**：运行创建时依据选择重新读取已授权内容，并在 `AgentEvent` 中记录每项最终状态（included / revoked / expired / missing / content_changed）。

默认策略是“内容变更即重新确认”：若 `content_hash` 不一致，服务端拒绝静默换入新文本，返回 `409 ContextChanged`，要求用户刷新预览。不可变不等于永远复制全文；它意味着一次运行有可解释的、确定的输入版本。

如果未来有合规或强回放要求，可为敏感数据建立加密、短保留期的 rendered artifact 存储，并设置严格访问审计。该能力不属于 MVP。

## 7. 权限、安全与隐私

### 7.1 两次授权检查

```text
查询候选项：
trusted identity -> ProjectSpace -> EffectiveAccessScope -> ACL-filtered candidates

冻结并发送：
trusted identity -> recompute EffectiveAccessScope -> validate every source ref
-> create ContextSnapshot -> create AgentRun
```

第一次是为了不展示不该展示的内容；第二次防止 Draft 长时间停留、权限变更、记忆过期、Evidence 撤销、群聊策略变化或攻击者伪造 ID。

### 7.2 每类对象的发送前校验

| 候选类型 | 必须校验 |
| --- | --- |
| `conversation_turn` | tenant/project/chat、可见范围、作者/会话策略、内容哈希 |
| `session_summary` | 所属 session 与 project、当前 chat 可见范围、未过期 |
| `task_state` | session/project、当前运行是否允许该 scratchpad |
| `evidence` | Evidence ACL、来源未撤销、数据分级、引用仍有效 |
| `project_memory` | project 相同、已审批、`valid_to` 未到期、当前 scope 可见 |
| `external_state_ref` | 资源白名单、读取权限、版本/哈希、结果未过期 |

无法访问的来源不能被“降级成空字符串后继续发送”。响应应明确列出不可用引用和原因码，但不泄露其内容。

### 7.3 Prompt、日志与审计

可记录：

- `snapshot_id`、`run_id`、`trace_id`；
- actor、tenant、project、chat、role、`policy_version`；
- 各 kind 数量、ID、顺序、Token 估算、`snapshot_hash`；
- 冻结与运行时的校验结果、失败原因、renderer 版本。

默认不记录：

- 完整 Prompt；
- 文件全文、数据库行全文、聊天原文副本；
- token、Cookie、凭据和机密工具输出；
- 未被授权给当前 actor 的候选标题或 ID。

## 8. Prompt 组装、上下文完整性与 Token 预算

### 8.1 上下文分层

为了防止用户精简历史后 Agent 不知道之前已经完成了什么，Composer 不把所有候选都当成同一种复选框：

| 层级 | 内容 | 是否可取消 | 作用 |
| --- | --- | --- | --- |
| 固定上下文 | tenant、project、actor、权限、当前问题、run 边界 | 否 | 保证安全和任务定位 |
| 推荐状态 | 当前任务状态、最近会话摘要、最近一次已验证运行结果 | 是，但取消需警告 | 告诉 Agent 之前做到哪里 |
| 详细历史 | 原始对话、Evidence、Git diff、外部读取结果、项目记忆 | 是 | 提供决策过程和具体证据 |

推荐状态不是对原始历史的删除或替代，而是一个低 Token 的“工作状态入口”。它仍然要标记来源、生成时间和有效性；如果状态没有证据或已过期，完整性检查只能给出警告，不能把它当成确定事实。

### 8.2 上下文完整性检查

完整性检查用于回答：“按当前选择，Agent 是否可能不知道继续工作所需的已完成事项？”它不负责推断新的权限，也不负责自动修改历史。

MVP 优先使用结构化状态和显式引用进行确定性检查：

```text
当前任务/动作
-> 需要的状态键或前置运行结果
-> 当前 Snapshot 是否包含对应 task_state / run_summary / evidence
-> 缺失则生成 warning + 推荐补充项
```

示例：

```text
当前任务依赖：登录页已完成
当前选择未包含：任务状态或最近运行摘要
建议：[加入当前任务状态] [加入最近运行摘要] [仍然发送]
```

“仍然发送”必须是显式操作，并在 Snapshot 审计中记录 `completeness_override=true`。Agent 收到上下文后，如果缺少事实，必须表达“不确定”并请求读取证据，不能根据未选择的历史猜测。

### 8.3 组装顺序

### 8.1 组装顺序

用户排序只适用于可编排内容；L0 安全锚点永远第一，当前问题永远最后。建议渲染顺序：

```text
L0：身份、项目、访问范围、策略、运行 ID（系统固定，不能取消）
L1：用户选择的 conversation_turn，按 ordinal
L2：用户选择的 session_summary
L3：用户选择的 task_state
L4：用户选择的 evidence / external_state_ref
L5：用户选择的 approved project_memory
最后：本次用户问题
```

不要让用户通过排序把普通历史放到 L0，也不要允许取消 tenant/project/access scope 等安全锚点。

### 8.4 当前 Token 算法的定位

现有 `estimate_prompt_tokens()` 通过 `len(prompt.as_text()) // 4` 进行近似计算。它足够做 MVP 的实时预算条和排序提示，但不是模型 tokenizer 的精确值，尤其会受中文、代码、URL、JSON 和模型版本影响。

MVP 规则：

- UI 标记为“估算 Token”；
- 发送前重新用同一算法计算；
- 设置预留空间：`context_budget <= model_context_limit - response_reserve - system_reserve`；
- Provider 返回实际 usage 时，记录 `estimated_tokens` 与 `actual_prompt_tokens` 的偏差；
- 超过硬预算不发送，不能依赖 provider 截断。

后续可注入 provider 对应 tokenizer，并为每个候选项缓存按模型版本计算的估算值。

### 8.5 截断规则

MVP 不做隐式截断。用户看到的选择与实际发送必须一致：

- 单项本身超过预算：提示该项不可单独放入，不自动截取；
- 总量超预算：禁止冻结，请用户取消、替换或提高已授权预算；
- Evidence 如已有安全 snippet 策略，则 item 的正文定义为该 snippet，预览和发送一致。

## 9. API 与服务边界

建议创建 `ContextComposerService`，它只编排引用，不直接访问飞书 SDK、模型 provider 或底层索引。

### 9.1 建议接口

```text
GET    /v1/projects/{project_id}/context-candidates
POST   /v1/context-drafts
PATCH  /v1/context-drafts/{draft_id}
GET    /v1/context-drafts/{draft_id}/preview
POST   /v1/context-drafts/{draft_id}/send
GET    /v1/context-snapshots/{snapshot_id}
GET    /v1/agent-runs/{run_id}/context
```

关键约束：

- `project_id` 只是路由定位，实际项目必须和服务端从 identity/scope 解析结果相等；
- 创建/更新 Draft 支持 `Idempotency-Key`；
- `PATCH` 使用 `revision` 乐观锁，冲突返回 `409 DraftRevisionConflict`；
- `send` 原子完成“二次校验 -> Snapshot INSERT -> AgentRun INSERT -> enqueue”，避免出现没有快照的运行；
- `send` 失败时不删除 Draft，用户可刷新后继续编辑；
- 群聊入口必须将 chat visibility policy 纳入 Scope；私聊不应借用群聊 Draft。

### 9.2 预览契约

预览响应至少包含：

```json
{
  "draft_id": "...",
  "revision": 4,
  "estimated_tokens": 9840,
  "token_budget": 16000,
  "items": [
    {"kind": "conversation_turn", "source_id": "...", "ordinal": 1, "status": "included"}
  ],
  "sections": [
    {"layer": "L1", "title": "recent_turns", "content": "..."}
  ],
  "warnings": []
}
```

预览正文只能包含 scope 已允许展示的内容。预览使用共享 `render_context_prompt()` 或其 Snapshot 专用调用路径，禁止独立拼字符串。

## 10. 实现落点

建议的模块归属：

```text
src/project_lens/domain/context_composer.py
  ContextItemRef / ContextSelectionDraft / ContextSnapshot / validation contracts

src/project_lens/context/conversation_history_store.py
  append-only full conversation history store

src/project_lens/context/context_snapshot_store.py
  Draft / Snapshot SQLite persistence

src/project_lens/application/context_composer_service.py
  list candidates / mutate draft / preview / freeze-and-start

src/project_lens/workflow/context_pack.py
  build ContextPack from resolved Snapshot rather than only ConversationSession

src/project_lens/workflow/context_prompt.py
  renderer versioning and snapshot audit reference

src/project_lens/application/run_service.py
  AgentRun.context_snapshot_id persistence and lifecycle association

src/project_lens/integrations/feishu/service.py
  entry adapter; resolve identity/scope, invoke application service, render cards

tests/test_context_composer_*.py
  domain, ACL, store, preview, freeze, compatibility and integration tests
```

`ContextComposerService` 可以依赖现有 Conversation、Evidence、Memory、ProjectSpace 和 Run service 的抽象接口；不要让飞书 adapter 绕过 Service 直接读取 SQLite 或 EvidenceIndex。

## 11. 分阶段实施计划

### Phase 0：基线与契约冻结

目标：先确定边界和测试基线。

- 梳理所有 Agent 运行入口及其 Prompt 组装点；
- 新增领域模型、错误码和 `ContextItemKind`；
- 为现有 ContextPack/ContextPrompt 渲染结果建立黄金测试；
- 确认所有现有 AgentRun 路径在未传 Snapshot 时维持当前行为；
- 写 ADR：选择不等于删除、Snapshot 不可变、二次 ACL、审计不存正文。

验收：不改业务行为的情况下，测试能锁定 renderer 输出和现有 run 生命周期。

### Phase 1：完整历史与候选读取

目标：可按项目、聊天和权限查询完整的可选对话历史。

- 新增 `conversation_history_entries` migration 和 Store；
- 在飞书消息/正式 AgentRun 完成时追加写入允许持久化的用户与 assistant 轮次；
- 对历史写入执行 content hash 和可见范围标记；
- 基于 EffectiveAccessScope 查询历史、摘要、task state、Evidence、Memory 候选；
- 默认最多返回分页窗口，支持时间范围与关键词搜索。

验收：跨项目、跨租户、群聊/私聊、未知用户的候选隔离测试全部通过；历史不会因 session 压缩丢失。

### Phase 2：Draft、排序与预览

目标：用户能编辑本次选择，且预览可复现。

- 新增 Draft store、revision、幂等和 owner 校验；
- 实现勾选、取消、重排、预算修改；
- 将候选引用解析为 Snapshot-ready `ContextPack`；
- 使用同一 renderer 产生预览、sections 和估算 Token；
- 建立超预算、重复引用、跨项目引用、无效 ordinal 的失败测试。

验收：任意 Draft 在未变更源内容的前提下，重复预览得到相同 sections、顺序和 Token 估算。

### Phase 3：冻结 Snapshot 与 AgentRun 绑定

目标：实际模型输入和用户预览统一、可审计。

- 新增 `context_snapshots` 表和 `AgentRun.context_snapshot_id`；
- 实现 `freeze_and_start()` 原子事务；
- 发送前逐项重算 Scope、状态、hash 和预算；
- 构建 Snapshot 专用 ContextPack，并由 `render_context_prompt()` 输出；
- 主 Workflow、read-agent、Hermes 均接收 Snapshot 或明确返回“不支持 Composer”；不允许悄悄退回默认上下文。

验收：从运行 ID 可查到 Snapshot；从 Snapshot 可查到输入引用及最终校验结果；模型实际输入与预览一致。

### Phase 4：飞书交互与模板

目标：在飞书中有可用的最小交互闭环。

- 先实现飞书互动卡片的摘要、候选分页、勾选、上移/下移和分步预览；
- 避免在单张飞书卡片中塞进全部历史正文；长内容通过“展开详情”或下一张分页卡片展示，仍在飞书内完成；
- 实现模板的规则化保存与应用；
- 卡片操作携带短期签名/nonce，服务端仍按可信 actor 重新授权。

验收：群聊中不会展示超出 ChatVisibilityPolicy 的候选项；用户能发起、预览、发送并回看一次运行的 Snapshot 摘要。

### Phase 5：质量、观测与上线门禁

目标：确保能力可回放、可度量、可安全灰度。

- 指标：Draft 创建率、预览率、发送率、超预算率、二次校验失败率、估算偏差、平均选择项数；
- 加入审计导出和管理员排查视图；
- 建立包含权限变更、来源撤销、群聊切换、并发编辑的回放集；
- 先对内部项目/白名单用户灰度，默认关闭模板共享和长保留原文。

验收：无跨 tenant/project 泄漏；不存在 Snapshot 缺失的 AgentRun；发送前失效可解释；重要路径 P95 延迟满足当前交互目标。

## 12. 测试矩阵

### 12.1 单元测试

- Draft 中 source ref 只能属于同一 tenant/project；
- `ordinal` 连续、唯一、稳定；
- Snapshot 不能 update/delete；
- 相同规范化内容得到相同 `snapshot_hash`；
- 上下文估算和预算校验正确；
- Content hash 变化返回 `ContextChanged`；
- 取消 `conversation_turn` 不调用任何 Memory、Git、文件或数据库的删除接口。
- 取消推荐状态项时生成确定性完整性警告；没有用户显式确认，不得静默发送缺少任务连续性的 Snapshot。

### 12.2 权限测试

- 不同 tenant 的 source ID 不可猜测/不可注入；
- private Draft 不可在群聊 send；
- 角色降权后 draft 内原先可见项在 send 时被拒绝；
- Evidence revoked、Memory expired 时不会进入 Snapshot；
- 不可信请求体中的 actor/project/chat/role 不影响服务端 scope。

### 12.3 集成测试

- preview 使用的 renderer 与真正 Provider 接收的 renderer 输出一致；
- Workflow、read-agent、Hermes 在 Composer 模式下都使用同一 `snapshot_id`；
- 缺少任务状态或最近运行摘要时，完整性 warning、推荐补充项和显式 override 行为一致；
- `freeze_and_start` 幂等重试只产生一个 Snapshot/Run；
- 运行失败、排队失败时 Snapshot 仍可审计，Draft 不丢失；
- 旧入口在 Composer feature flag 关闭时行为不变。

### 12.4 端到端验收场景

场景：用户针对“订单管理”重新开始调查。

1. 历史有 H1 需求、H2 Vue 选型、H3 已废弃 React 讨论、H4 登录页完成、H5 订单任务；
2. 用户选 H1、H2、H4、H5 和当前 Git diff，取消 H3；
3. Preview 显示对应 L1/L4/L5、总 Token 和“估算”标识；
4. 点击发送，生成 `Snapshot #12` 和 `AgentRun #...`；
5. ProjectMemory “项目使用 Vue”仍存在，除非用户主动取消它在本次 Snapshot 中的勾选；
6. H3 和 React 相关代码/Git 历史均未被修改；
7. 审计能证明本次模型没有得到 H3。

## 13. 失败处理与兼容策略

| 情况 | 行为 |
| --- | --- |
| Draft revision 冲突 | 返回最新 revision，提示用户刷新/合并，不覆盖对方选择 |
| 来源已过期、撤销或无权访问 | 返回按 ID 的 reason code，不展示正文，不创建 Snapshot |
| 源内容变化 | 返回 `409 ContextChanged`，要求重新预览 |
| Token 超预算 | 允许编辑，不允许冻结/发送 |
| Snapshot 已生成但队列提交失败 | Snapshot 与失败事件保留；可用 idempotency key 安全重试 |
| Provider 失败 | 运行失败，不修改 Snapshot；可以基于原 Snapshot 重试创建新 Run |
| Composer feature flag 关闭 | 使用既有默认 ContextPack，AgentRun 的 snapshot ID 为空且审计明确标注 |

回滚策略：先通过 feature flag 停止新 Draft/Snapshot 创建；历史和快照表追加写入、不会影响原 `ConversationSession` 或 `ProjectMemory`。不要通过删除 Snapshot 来“回滚”一次已发生的运行审计。

## 14. 后续版本：重建工作上下文与依赖检查

当 MVP 验证“选择本次上下文”有价值后，再增加操作 B：**从当前选择重建工作上下文**。

这时需要为摘要、计划、短期记忆、长期记忆增加可查询的来源关系：

```text
ConversationHistoryEntry H2 -> DerivedArtifact M1
Evidence E8 -> DerivedArtifact M1
```

建议新增：

```text
derived_artifacts
derived_artifact_sources(artifact_id, source_kind, source_id, source_hash)
```

只有在以下操作中才执行依赖检查：永久删除历史、从当前选择重新生成摘要/计划/短期记忆、创建独立上下文分支。发现 M1 依赖未选 H2 时，UI 提供“本次保留”“从本次移除”“重新生成”，而不是自动删除 M1。

ProjectMemory 的当前 `evidence_ids` 只记录 Evidence 来源，尚不支持 `conversation_turn` provenance。不要为了实现 Composer MVP 虚构这种依赖关系。

## 15. 完成定义

只有同时满足以下条件，才能宣称 Context Composer MVP 完成：

- 用户可查看已授权的历史/派生/外部读取候选，并能勾选、取消、排序；
- UI 显示标题、摘要、来源、估算 Token 和最终 Prompt 预览；
- 取消选择不会修改原始历史、ProjectMemory、Evidence 或外部状态；
- Draft、Snapshot、AgentRun 关系可查询，Snapshot 不可变；
- 所有 Agent 运行模式在 Composer 路径上共享相同的 Snapshot 渲染结果；
- 创建与发送前均执行服务器端 EffectiveAccessScope 校验；
- 超预算、撤销、过期、内容变化和并发编辑均有确定行为；
- 审计记录 ID、版本、预算、哈希和校验结果，但默认不记录完整敏感 Prompt；
- 权限隔离、渲染一致性、幂等、失败恢复和既有入口兼容测试通过。
