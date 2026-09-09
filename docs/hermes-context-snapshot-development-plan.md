# Hermes 上下文快照编辑器开发文档

版本：v0.1  
日期：2026-09-05  
状态：待实施  
适用范围：ProjectLens MVP 的用户可控上下文快照编辑器

## 1. 决策与目标

本次新增的产品能力是“上下文快照编辑器”：用户在每次提问前，可以主动选择本次要交给 Hermes 的历史片段与已授权的信息，再由服务端保存一份不可变的 ContextSnapshot。本文假定生产运行时已经收敛为 Hermes；运行时迁移另见 [hermes-only-migration-plan.md](C:\Users\Administrator\Desktop\project-lens\docs\hermes-only-migration-plan.md)。

一句话定义：

> ProjectLens 不是聊天记录删除器，而是让用户编排“本次 Hermes 应该知道什么”的上下文快照编辑器。

### 1.1 本期要交付

1. 保存完整、可追溯的原始对话轮次，不再只依赖滚动摘要和最近六轮会话。
2. 为用户列出可选上下文候选项：对话轮次、会话摘要、任务状态、Evidence、项目记忆和外部状态引用。
3. 支持勾选、取消、排序、预算预览和服务端重算。
4. 将选择物化为不可变快照，并让 Hermes 只接收这个快照提供的上下文。
5. 对每个快照建立与 AgentRun 的关联，以便审计、回放、复现回答和排查权限问题。

### 1.2 本期明确不做

1. 不因取消勾选而删除、失效或回滚原始对话、文件、Git、数据库、Evidence 或项目记忆。
2. 不做“从当前选择重新生成摘要/计划/记忆”的 Operation B。
3. 不做历史轮次到项目记忆、摘要、任务计划的通用来源依赖图和自动重建。
4. 不开放 Apply、PR、deploy、rollback、restart 等写操作。
5. 不让 Hermes 直连 SQLite、文件系统、GitHub、飞书或 EvidenceIndex。

## 2. 产品与领域边界

### 2.1 四类对象

| 对象 | 定义 | 本期处理 |
| --- | --- | --- |
| 对话历史 | 用户和 Agent 的原始轮次 | 可切片、勾选、排序；原件追加保存 |
| 外部状态 | 文件、Git diff、数据库查询、API 结果等模型外事实 | 默认展示为稳定引用；仅在授权工具读取并纳入快照后进入模型上下文 |
| 派生信息 | 摘要、任务状态、短期工作记忆、长期项目记忆 | 可作为候选项；取消历史不会自动修改它们 |
| 上下文快照 | 本次请求所选择信息的不可变组合 | 发送前创建，运行与审计都绑定它 |

### 2.2 最重要的语义

取消 `H2` 的含义只有：

```text
本次发送给 Hermes 的 ContextSnapshot 不包含 H2。
```

它不表示：

```text
H2 被删除；Vue 代码被删除；Git 被回滚；数据库被修改；
所有提到 Vue 的记忆被自动删除；H2 中的事实被判定为失效。
```

### 2.3 本期产品操作

MVP 只实现操作 A：**仅调整本次上下文**。

```text
用户修改勾选和顺序
-> 服务端校验当前权限并重算 token
-> 用户预览将发送的内容
-> 创建不可变 ContextSnapshot
-> Hermes 使用该快照完成本次请求
```

后续 Operation B “从当前选择重建工作上下文”必须等待派生信息拥有 `source_refs`（例如 `turn_id`）后再做。届时依赖检查是数据库按 ID 查询关系，不是模型自行猜测。

## 3. 现状与改造点

### 3.1 现有可复用基础

- `ConversationSession` 已有最近轮次、滚动摘要和 `task_scratchpad`。
- `ProjectMemory` 已有审批与 Evidence 关联；长期记忆目录、搜索与详情工具已存在。
- `AgentRun`、AgentEvent、SQLite run/event 台账、EffectiveAccessScope 和 AudienceView 已存在。
- Hermes 已经通过 ProjectLens tool gateway 工作，并能接收 `build_hermes_project_context()` 的文本上下文。
- Evidence、项目范围、群聊可见范围、工具授权和引用验证已是 ProjectLens Kernel 的职责。

### 3.2 当前缺口

1. `ConversationSession` 会压缩旧轮次，不能充当完整原始历史。
2. `build_hermes_project_context()` 会自动写入 `session.recent_turns[-6:]`、会话摘要和 memory；这与显式选择冲突。
3. AgentRun 尚未关联 ContextSnapshot。
4. SQLite 尚无快照、快照条目、完整对话轮次三类持久化表。
5. HTTP / MCP / 飞书仍存在 `workflow`、`read_agent`、Hermes 等多种运行选择和开关。
6. `ProjectMemory`、会话摘要和任务状态尚无统一 `source_refs`，不能安全执行依赖检查或选择性再生成。

## 4. 目标架构

### 4.1 职责分层

```text
domain/
  ContextSnapshot、ContextItemRef、ConversationTurnRecord 等不可变契约

application/
  ContextCandidateService、ContextSnapshotService、ContextAssembler
  HermesRuntimeService（统一创建 run、执行、验证）

context/
  FullConversationHistoryStore、候选内容解析、外部状态稳定引用

persistence/
  SQLiteContextSnapshotStore、SQLiteConversationHistoryStore

integrations/feishu/、api/、integrations/hermes_plugin/
  只负责身份可信入口、候选/选择交互、展示结果；不得直读索引

runtime/
  Hermes 允许调用的 ProjectLens 工具、ACL、审计、审批门禁
```

### 4.2 信任边界

客户端只能提交：

```text
candidate_id、显示顺序、预算偏好、问题文本
```

客户端不得提交或覆盖：

```text
tenant_id、project_id、actor_id、chat_id、role、权限、Evidence 正文、
外部状态正文、token 数、scope fingerprint。
```

服务端在候选列表、预览、创建快照、实际发送四个时点都必须重新解析并校验 `EffectiveAccessScope`。候选 ID 只是请求意图，不是授权凭证。

### 4.3 Hermes 的输入边界

改造后 Hermes 获得：

```text
system safety profile
+ runtime_access 的安全摘要
+ ContextSnapshot 的已物化文本与引用
+ 当前用户问题
+ ProjectLens 工具目录
```

Hermes 不得再自行从 `ConversationSession`、`MemoryStore` 或数据库抓取“所有可用历史”。新函数建议为：

```python
build_hermes_project_context(snapshot: MaterializedContextSnapshot) -> HermesProjectContext
```

旧的 `session`、`memories`、`memory_summary` 参数在迁移完成后移除，防止隐式注入重新出现。

## 5. 数据模型

### 5.1 完整对话轮次

`ConversationTurnRecord` 是新增的追加式原始记录，不替代 `ConversationSession`。

```text
turn_id: UUID
tenant_id / project_id / chat_id / actor_id
session_id: UUID
run_id: UUID | null
sequence: int
speaker: user | assistant | system
text: str
content_hash: str
created_at: datetime
visibility_scope: safe metadata
```

规则：

- 用户消息在进入问题改写之前保存原始文本；如发生 rewrite，另存 `rewritten_question` 审计字段。
- Agent 成功或失败后，按需追加 assistant 轮次或受控失败摘要。
- 会话压缩只影响 `ConversationSession` 的工作内存，不得删除完整原始轮次。
- 所有读取按 tenant/project/chat/actor/scope 做过滤；群聊候选不跨越群聊可见策略。

### 5.2 候选项

```text
ContextCandidate
candidate_id: str                 # 类型前缀 + 可信对象 ID
kind: conversation_turn | session_summary | task_state |
      evidence | project_memory | external_state_ref
title: str
summary: str
estimated_tokens: int
created_at: datetime | null
source_refs: tuple[str, ...]      # 审计/未来依赖使用，MVP 不作自动重建
metadata: safe metadata only
```

其中 `external_state_ref` 默认仅存稳定定位信息：

```text
system、source_id、path 或 URI、commit/version、content_hash、observed_at
```

不可将可变文件或 API 正文简单塞入候选缓存。若用户选择它，服务端在创建快照时在当前权限下重新读取，校验 hash/version；变化则提示刷新候选，或创建标记为 `changed_since_preview` 的新预览。

### 5.3 选择草稿

```text
ContextSelectionDraft
draft_id: UUID
tenant_id / project_id / chat_id / actor_id / session_id
question: str
ordered_candidate_ids: tuple[str, ...]
token_budget: int
scope_fingerprint: str
created_at / updated_at / expires_at
```

选择草稿是可变、短生命周期的交互状态。初期可采用内存或 SQLite，生产建议 SQLite 并设置 TTL；它不是审计事实源。

### 5.4 不可变上下文快照

```text
ContextSnapshot
snapshot_id: UUID
tenant_id / project_id / chat_id / actor_id / session_id
question: str
ordered_items: tuple[ContextSnapshotItem, ...]
estimated_tokens: int
token_budget: int
scope_fingerprint: str
context_hash: str
created_at: datetime
sent_at: datetime | null
sent_run_id: UUID | null
```

```text
ContextSnapshotItem
position: int
kind: ContextCandidateKind
source_id: str
title: str
rendered_text: str | null          # 仅保存允许审计/回放的内容
content_hash: str
estimated_tokens: int
source_refs: tuple[str, ...]
metadata: safe metadata
```

保留 `rendered_text` 的前提是符合当前项目的数据保留与敏感信息策略。若正文不可持久化，改存加密受控 blob 指针或仅保存 hash/reference；但每个快照必须仍可说明“模型当时看到了哪一版授权内容”。

### 5.5 与现有模型的关系

| 已有对象 | 新对象关系 |
| --- | --- |
| `ConversationSession` | 短期工作状态和压缩摘要来源；不再是完整历史库 |
| `ProjectMemory` | 候选类型之一；不因历史取消而失效 |
| Evidence | 候选类型之一；创建快照与工具读取时再次 ACL 校验 |
| `AgentRun` | 新增 `context_snapshot_id`、`context_hash`、`context_token_estimate` 审计字段 |
| AgentEvent / Episode | 保留现有语义；以后可作为可选候选，不放入首个 MVP |

## 6. 持久化与迁移

SQLite 初版新增：

```sql
CREATE TABLE conversation_turns (...);
CREATE INDEX idx_conversation_turns_scope_time
  ON conversation_turns (tenant_id, project_id, chat_id, actor_id, created_at);

CREATE TABLE context_selection_drafts (...);
CREATE INDEX idx_context_drafts_scope_expiry
  ON context_selection_drafts (tenant_id, project_id, chat_id, actor_id, expires_at);

CREATE TABLE context_snapshots (...);
CREATE INDEX idx_context_snapshots_scope_created
  ON context_snapshots (tenant_id, project_id, chat_id, actor_id, created_at);

CREATE TABLE context_snapshot_items (...);
CREATE INDEX idx_context_snapshot_items_snapshot_position
  ON context_snapshot_items (snapshot_id, position);
```

同时对 `runs` 进行向后兼容迁移：

```text
context_snapshot_id TEXT NULL
context_hash TEXT NULL
context_token_estimate INTEGER NULL
```

要求：

1. 使用 `CREATE TABLE IF NOT EXISTS` 与显式列检测，兼容已存在本地数据库。
2. 旧 run 的 `context_snapshot_id` 为 `NULL`，不能伪造历史快照。
3. 快照和项目/租户筛选必须在 SQL 层完成，不能全量加载后再过滤。
4. 删除、撤销或权限变化只影响将来的候选和发送；旧快照的审计记录不被改写。

## 7. 服务与接口设计

### 7.1 应用服务

```text
ContextCandidateService.list_candidates(...)
ContextSnapshotService.create_or_update_draft(...)
ContextSnapshotService.preview(...)
ContextSnapshotService.freeze_snapshot(...)
ContextSnapshotService.materialize_for_send(...)
ContextSnapshotService.bind_run(snapshot_id, run_id)
```

关键流程：

```text
list_candidates
-> 依据可信 runtime scope 过滤候选

preview
-> 重新解析 candidate_id
-> 校验 ACL、撤销、过期、版本/hash
-> 在服务端确定顺序与 token estimate
-> 返回安全预览

freeze_snapshot
-> 重做 preview 校验
-> 物化本次可发送内容
-> 计算 context_hash
-> 原子写入 snapshot + items

materialize_for_send
-> 验证 snapshot 绑定的 actor/project/chat/scope
-> 检查发送时 Evidence/memory/外部状态仍可用
-> 返回 HermesProjectContext
```

### 7.2 HTTP API

API 为 Hermes 入口和未来 Web/飞书卡片共享，不直接暴露底层存储：

```text
GET  /api/v1/context/candidates
POST /api/v1/context/drafts
POST /api/v1/context/drafts/{draft_id}/preview
POST /api/v1/context/drafts/{draft_id}/snapshots
GET  /api/v1/context/snapshots/{snapshot_id}
POST /api/v1/project-agent/ask
```

建议请求模型：

```json
{
  "question": "订单管理现在应该先做什么？",
  "ordered_candidate_ids": ["turn:...", "memory:...", "evidence:..."],
  "token_budget": 16000
}
```

`/project-agent/ask` 可以接受 `snapshot_id`，但必须验证它归属当前可信身份、项目、群聊与有效范围；不能由客户端传 `project_id`、`actor_id` 或 scope。

### 7.3 飞书交互 MVP

第一版不要求在飞书卡片里做复杂拖拽。建议分两级：

1. 默认自动选中：当前问题、最近相关轮次、会话摘要、少量已授权 memory；用户可以点“编辑本次上下文”。
2. 编辑视图按类型显示勾选框、标题、摘要和 token，提供“上移/下移”“预览”“发送”。

首屏遵循：

```text
本次上下文

对话历史
[x] 项目需求                  420 tokens
[x] 登录页进度                290 tokens
[ ] 已废弃的 React 讨论       610 tokens

项目资料
[x] 当前任务状态              240 tokens
[x] 已批准项目记忆            180 tokens
[ ] 当前 Git diff             980 tokens

总计：4,812 / 16,000 tokens
```

默认操作仍是快速提问；上下文编辑器是显式控制面，不应让每一条普通问答都被复杂设置阻断。

## 8. 测试与验收

### 8.1 必须新增的单元测试

1. 完整对话记录追加、排序、分页与会话压缩后的可读性。
2. 候选按 tenant/project/chat/actor/role/scope 过滤。
3. 同一 candidate ID 在不同 scope 下不能被重放。
4. 取消历史只改变 snapshot items，不改变原始 turn、memory、Evidence 或外部状态。
5. 顺序影响 materialized context 的顺序，token estimate 始终由服务端计算。
6. budget 超限时返回确定性截断/错误，不静默丢重要项。
7. 外部状态 hash/version 在预览和发送间变化时的刷新策略。
8. Evidence revoked、Memory expired、scope policy 变更时无法发送旧草稿。
9. snapshot 与 run 原子绑定；失败 run 也保留快照审计关联。
10. Hermes 输入不含未选择的 session turns/memories。

### 8.2 集成与安全测试

1. Feishu、HTTP、MCP 三个入口均创建 Hermes run 和 ContextSnapshot。
2. 群聊不能使用私聊才能看到的候选，也不能重放私聊 snapshot。
3. 用户伪造 `tenant_id`、`project_id`、`role`、token 数或 Evidence 正文全部无效。
4. Hermes 只通过 ProjectLens 工具获得新资料；工具调用仍具备 EffectiveAccessScope、Evidence 和审计。
5. Citation verifier、AudienceView、risk feedback、审批门禁在 Hermes-only 后保持原有安全行为。
6. 旧 workflow/read_agent fixture 在 Hermes 路径下回归，事实核心、拒答和引用质量不退化。

### 8.3 最小验收场景

```text
给定：H1 项目需求、H2 Vue 决策、H3 React 已废弃讨论、H4 登录页完成
当：用户只选择 H1、H2、H4，并发送“下一步做什么？”
则：Hermes 初始上下文只包含选择项及用户显式选择的派生/外部项目；
    H3 不会进入初始上下文；
    H3 原始记录仍可在后续候选中找到；
    ProjectMemory 不会被自动删除；
    AgentRun 关联一个可审计、不可变的 ContextSnapshot。
```

## 9. 风险与防护

| 风险 | 防护 |
| --- | --- |
| 预览和实际模型输入不一致 | 冻结后只使用 materialized snapshot；保存 context hash；禁止 Hermes 隐式读 session/memory |
| 用户借 candidate ID 越权 | 每次候选解析、预览、冻结、发送都重新计算可信 scope |
| 外部文件在发送前已变 | snapshot 保存 stable ref/hash；发送时重校验；变化必须刷新 |
| snapshot 存储敏感正文 | 采用项目数据保留策略、最小化正文、加密 blob/受控指针；审计默认只显示 ID、hash、计数 |
| 迁移期间仍走旧路径 | 所有生产入口以 `runtime=hermes` 断言；监控非 Hermes run 为错误 |
| 过早做记忆依赖清理 | Operation B 延后；MVP 绝不把取消勾选解释成事实撤销 |

## 10. 建议实施顺序

1. 新增完整对话轮次持久化和测试。
2. 新增 ContextSnapshot / Item / Draft 模型和 SQLite store。
3. 实现候选服务、token 预估、草稿和预览 API。
4. 把 Hermes context builder 改为只吃 materialized snapshot。
5. 将物化快照交给 Hermes context builder，并验证模型输入和预览一致。

这份顺序的核心是先建立“用户选择可被真实执行和审计”的底座，再做交互界面。不要先在 UI 上做漂亮的勾选界面，否则很容易出现用户以为控制了上下文、实际 Hermes 仍在读取隐式历史的问题。

## 11. 未来阶段：来源追踪与工作上下文重建

当 MVP 稳定后，再为 `ProjectMemory`、`ConversationSummary`、`TaskScratchpad` 增加统一来源字段：

```text
source_refs: ["turn:<uuid>", "evidence:<uuid>", "run:<uuid>"]
```

届时可以支持：

```text
用户选择一组历史
-> 请求“从当前选择重建工作上下文”
-> 系统找出引用了未选 turn 的派生项
-> 提供 本次保留 / 本次移除 / 重新生成 的明确选择
```

这是一项独立的派生信息生命周期能力，不应混入本期的“发送前选择上下文”。
