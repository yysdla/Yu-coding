# ProjectLens 长期记忆开发规格

版本：v1.0  
日期：2026-09-15  
状态：可执行开发文档  
关联架构：`projectlens-long-term-memory-architecture.md`  
适用范围：ProjectLens Kernel、Hermes Runtime、Feishu、Context Composer、SQLite、离线评测

## 1. 开发目标

本规格把长期记忆架构拆成可实施的代码任务。完成后，ProjectLens 应满足：

1. Hermes 默认只接收安全上下文、短目录和当前任务状态，不再全量注入项目记忆；
2. ProjectMemory 具备稳定事实键、来源、时间窗口、可见范围和复核状态；
3. 记忆搜索在授权候选集内执行精确、词法、语义和时间排序；
4. 记忆详情、Evidence 详情和历史 Episode 都能按需读取；
5. 正式记忆只能由 MemoryProposal 经授权审批生成；
6. 记忆替换、失效、撤销和复核均保留可回放历史；
7. AgentEvent、MemoryObservation、Episode 与 ProjectMemory 职责分离；
8. Context Composer 能冻结一次运行实际使用的记忆引用；
9. 有 Recall、引用正确性、拒答、时间推理和 ACL 隔离的自动化门禁。

## 2. 现状基线

### 2.1 已存在并应复用（不代表目标契约已完成）

下表记录的是截至 2026-09-15 已经存在、可以增量改造的代码能力。后文的目标契约优先于这些当前实现；尤其是现有 Memory Proposal HTTP 路由仍从请求体接收项目和审批相关字段，尚未满足第 3.1、9 和 13 节的可信身份要求。

| 能力 | 代码位置 | 当前状态 |
| --- | --- | --- |
| ProjectMemory / MemoryProposal | `src/project_lens/domain/memory.py` | 已有基础字段和状态 |
| 审批网关 | `src/project_lens/runtime/memory_approval_gateway.py` | 已有提案创建、审批和审计 |
| SQLite MemoryStore | `src/project_lens/context/memory_store.py` | 已有 JSON payload、FTS、summary 缓存 |
| 记忆摘要和卡片 | `src/project_lens/context/memory_retrieval.py` | 已有 BM25、RRF、MMR、预算 |
| Episode / Observation | `src/project_lens/context/history_store.py` | 已有持久化、FTS、retention |
| embedding provider/cache | `src/project_lens/context/embeddings.py` | 已有可选 provider 和故障降级 |
| Hermes 记忆工具 | `src/project_lens/application/project_agent_tools.py` | 已有 search/detail/history 契约 |
| Evidence / 来源权限 | `src/project_lens/context/source_records.py`、`authority.py`、`engine.py` | 已有项目和 scope 过滤 |
| 检索评测 | `src/project_lens/evaluation/memory_retrieval.py` | 已有 Recall/MRR/nDCG 基础 |

### 2.2 需要改造

- ProjectMemory 增加事实身份、来源、时间和复核字段；
- MemoryStore 高频过滤字段下沉到 SQL；
- search/detail/history 统一经过 EffectiveAccessScope 授权服务；
- 更新逻辑从“文本相同冲突”升级为 fact_key 版本链；
- Evidence 撤销和 SourceRecord revision 变化触发复核；
- Episode 与正式记忆统一检索审计字段；
- Context Composer 保存 memory source hash 和 renderer 版本；
- 评测加入时间、拒答、引用正确性和泄漏门禁。

### 2.3 明确不做

本阶段不做 GraphRAG、通用图数据库、自动审批、跨项目全局记忆、正式记忆物理删除，也不让 LLM judge 替代确定性 ACL、状态和来源校验。

## 3. 代码结构

建议新增或调整：

```text
src/project_lens/
  domain/
    memory.py
    memory_identity.py
    memory_review.py
  context/
    memory_store.py
    memory_retrieval.py
    memory_authorization.py
    memory_review_queue.py
    history_store.py
    embeddings.py
  application/
    memory_service.py             # 已存在：收敛为写入编排入口
    memory_retrieval_service.py
    memory_review_service.py
  api/
    memory_routes.py              # 已存在：迁移旧 HTTP 契约
  runtime/
    memory_approval_gateway.py
    events.py                     # 已存在：补充 memory audit/outbox 事件
  evaluation/
    memory_retrieval.py
    memory_answer.py
```

Feishu adapter 和 Hermes plugin 不得直接访问 SQLiteMemoryStore；统一经 application service，再调用授权和 store。

### 3.1 可信身份边界

所有外部入口先解析一个不可由请求体覆盖的 `TrustedActorContext`：

```python
class TrustedActorContext(FrozenModel):
    actor_id: str
    project: ProjectRef
    chat_id: str
    scope: EffectiveAccessScope
    identity_source: str
    policy_version: str
```

这不是把现有 `ActorContext` 重命名。实现应先由飞书签名事件、已认证 HTTP 会话或受信任内部任务生成 `ActorContext`，再通过 ProjectSpace resolver 得到 `EffectiveAccessScope`，最后组装本 DTO。`project`、`actor_id`、`chat_id`、`role`、`allowed_approvers` 和 `decided_by` 均不得从普通 JSON body 读取。

为兼容当前内部测试和管理脚本，允许保留一个显式的 `InternalTrustedActor` 适配器；它必须在应用配置中开启，并且调用方必须提供服务端可验证的内部身份，不得把旧请求字段直接当成可信身份。

## 4. 数据模型实施

### 4.1 枚举和状态

在 `src/project_lens/domain/memory.py` 增加：

```python
class MemoryStatus(StrEnum):
    ACTIVE = "active"
    NEEDS_REVIEW = "needs_review"
    REVOKED = "revoked"
    SUPERSEDED = "superseded"

class ReviewReason(StrEnum):
    SOURCE_REVISED = "source_revised"
    EVIDENCE_REVOKED = "evidence_revoked"
    VALIDITY_EXPIRED = "validity_expired"
    CONFLICT = "conflict"
    REVIEW_DUE = "review_due"
    USER_FEEDBACK = "user_feedback"
    MANUAL = "manual"
```

默认读取条件必须同时满足：

```text
status == active
AND valid_from <= now
AND (valid_to IS NULL OR valid_to > now)
AND evidence/source authorization passes
```

状态不变量：

- `ACTIVE`：可以进入当前检索；
- `NEEDS_REVIEW`：保留正文和历史，但不进入默认当前检索；
- `SUPERSEDED`：必须有 replacement audit 或后继版本引用，不进入默认当前检索；
- `REVOKED`：必须有撤销原因和 `valid_to`，只允许按历史权限读取；
- `valid_to` 到期本身不会删除 payload，但维护任务应创建 `VALIDITY_EXPIRED` review。

### 4.2 ProjectMemory 字段

第一阶段保持 Pydantic JSON 兼容，增加：

```python
class ProjectMemory(FrozenModel):
    id: UUID
    project: ProjectRef
    text: str
    memory_type: MemoryType
    claim_type: ClaimType = ClaimType.FACT

    fact_key: str | None = None
    subject: str | None = None
    evidence_ids: tuple[UUID, ...] = ()
    source_refs: tuple["MemorySourceRef", ...] = ()
    authority_scope: tuple[str, ...] = ()
    visibility_scope: tuple[str, ...] = ()

    valid_from: datetime
    valid_to: datetime | None = None
    observed_at: datetime | None = None
    recorded_at: datetime = Field(default_factory=utc_now)
    review_due_at: datetime | None = None

    status: MemoryStatus = MemoryStatus.ACTIVE
    approved_by: str
    proposal_id: UUID | None = None
    supersedes_memory_id: UUID | None = None
    content_hash: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
```

`source_refs` 不能只保存不可解析的字符串。新增结构化引用：

```python
class MemorySourceRef(FrozenModel):
    tenant_id: str
    project_id: str
    source_id: str
    revision: str
    content_hash: str | None = None
    source_type: str | None = None
```

`MemorySourceRef` 必须能定位到 `SourceRecord(tenant_id, project_id, source_id, revision)`。如果只引用 Evidence，则仍保留 `evidence_ids`；两者都存在时，Evidence 是当前回答的直接依据，SourceRecord 用于版本和复核。

兼容规则：

- 旧 payload 缺少新字段时使用安全默认值；
- content_hash 缺失时按规范化文本、来源和 Evidence 计算；
- 不因迁移自动把旧记录标记为 needs_review；
- 新写入记录必须生成新字段；
- status 不能绕过 valid_to 和 Evidence 撤销检查。

### 4.3 MemoryProposal 字段

在保留现有审批字段的基础上，新增的最小契约如下。`source_refs` 与 `ProjectMemory.source_refs` 使用同一个 `MemorySourceRef` 类型，避免 Proposal 和批准后的记录对来源采用不同编码。

```python
class MemoryProposal(FrozenModel):
    project: ProjectRef
    proposed_by: str
    claim_text: str
    claim_type: ClaimType = ClaimType.FACT
    memory_type: MemoryType
    evidence_ids: tuple[UUID, ...] = ()
    source_refs: tuple[MemorySourceRef, ...] = ()
    subject: str | None = None
    claim_slot: str | None = None
    authority_scope: tuple[str, ...] = ()
    visibility_scope: tuple[str, ...] = ()
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    review_due_at: datetime | None = None
    content_hash: str | None = None
    replaces_memory_id: UUID | None = None
```

`fact_key` 默认由服务端生成；客户端只能提交可选的 `claim_slot` 或 subject，不能直接指定跨项目或任意版本键。服务端必须以可信项目逐条解析 `source_refs`，按 `(tenant_id, project_id, source_id, revision)` 读取权威 `SourceRecord`，并以该记录覆盖客户端传来的 `content_hash`、`source_type` 和项目字段；不匹配、不可读或已撤销的引用返回 `MEMORY_INVALID_PROVENANCE`。

客户端不得指定 approved_by、recorded_at、status、decided_at 或最终 memory id。

### 4.4 事实身份规范化

新增 `src/project_lens/domain/memory_identity.py`：

```python
def normalize_subject(value: str | None) -> str:
    # casefold、去空格、统一连接符和路径分隔符

def build_fact_key(
    *,
    project: ProjectRef,
    memory_type: MemoryType,
    subject: str | None,
    claim_text: str,
    claim_slot: str | None = None,
) -> str:
    ...
```

推荐格式：

```text
{tenant_id}:{project_id}:{memory_type}:{subject}:{claim_slot}
```

首批 `claim_slot` 必须由服务端根据受控字段生成，或由受信任的提案来源提供并经过白名单校验，例如：

```text
demo:payment:owner:payment-service:owner
demo:payment:decision:callback-retry:max_attempts
demo:payment:architecture_fact:callback-service:transport
```

无法稳定生成 `fact_key` 时保留 None，使用旧规范化文本冲突检查，并生成 `FACT_KEY_MISSING` 告警。缺少 fact key 的记忆不得参与自动替换，只能通过人工明确指定 replacement target。

## 5. SQLite 迁移设计

当前表大量使用 payload TEXT。本阶段采用“payload 保持兼容 + 高频过滤字段增加索引列”。

### 5.1 Schema migration

在 `src/project_lens/persistence/sqlite.py` 增加 schema migration runner，但不要在 `SQLiteDatabase.__init__()` 中直接执行依赖 `project_memories` 的迁移。基础表由 `SQLiteMemoryStore` 创建后，再调用 memory-specific migration。

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    component TEXT NOT NULL,
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (component, version)
);
```

迁移按 `(component, version)` 升序、单版本幂等。使用 `component = 'core'` 登记通用迁移，使用 `component = 'memory'` 登记本规格的 memory 迁移；不要让其他持久化模块与 memory 共用一个无命名空间的整数版本。数据库层提供两种边界：

1. `SQLiteDatabase` 负责通用表和 `schema_migrations`；
2. `SQLiteMemoryStore` 在创建 `memory_proposals`、`project_memories`、`memory_summaries` 后，负责 memory schema 版本 2 及之后的迁移。

每个 migration 必须在同一连接、同一锁和显式事务中执行。迁移成功后才写入 `schema_migrations`；失败必须 rollback，不能留下半个版本。

| 版本 | 内容 |
| --- | --- |
| 1 | 基础表创建完成后登记为基线 |
| 2 | ProjectMemory 索引列和 FTS |
| 3 | memory_reviews |
| 4 | memory_audit_events |
| 5 | Context Snapshot 的 memory source hash |
| 6 | 评测运行和结果表，可选 |

### 5.2 ProjectMemory 索引列

```sql
ALTER TABLE project_memories ADD COLUMN tenant_id TEXT;
ALTER TABLE project_memories ADD COLUMN project_id TEXT;
ALTER TABLE project_memories ADD COLUMN fact_key TEXT;
ALTER TABLE project_memories ADD COLUMN memory_status TEXT;
ALTER TABLE project_memories ADD COLUMN valid_from TEXT;
ALTER TABLE project_memories ADD COLUMN valid_to TEXT;
ALTER TABLE project_memories ADD COLUMN content_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_project_memories_scope
  ON project_memories (tenant_id, project_id, memory_status);
CREATE INDEX IF NOT EXISTS idx_project_memories_fact_key
  ON project_memories (tenant_id, project_id, fact_key);
CREATE INDEX IF NOT EXISTS idx_project_memories_validity
  ON project_memories (tenant_id, project_id, valid_from, valid_to);
```

`ALTER TABLE` 前必须通过 `PRAGMA table_info(project_memories)` 检查列是否存在，以兼容已经部分升级的数据库。回填完成后才创建索引；不能把上述 SQL 作为每次启动都直接执行的脚本。

回填流程：

1. 解析每条 payload；
2. 写入索引列；
3. 单条坏记录只记录 migration warning，不删除原始行；
4. 应用启动不因坏记录阻塞全库；
5. 健康检查暴露 migration warning。

### 5.3 FTS

FTS 的 search_text 拼接 text、memory_type、subject、fact_key、source_refs 和 evidence_ids。FTS 只能生成候选，最终仍从主表读取并执行状态、时间和 scope 校验。

### 5.4 Review Queue

```sql
CREATE TABLE IF NOT EXISTS memory_reviews (
    review_id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger_key TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    due_at TEXT,
    resolved_at TEXT,
    resolved_by TEXT,
    resolution TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_reviews_project_status
  ON memory_reviews (tenant_id, project_id, status, due_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_reviews_trigger
  ON memory_reviews (memory_id, reason, trigger_key);
```

状态：

```text
open -> acknowledged -> resolved
open -> dismissed
open -> superseded
```

### 5.5 审计表

```sql
CREATE TABLE IF NOT EXISTS memory_audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    memory_id TEXT,
    proposal_id TEXT,
    run_id TEXT,
    event_type TEXT NOT NULL,
    actor_id TEXT,
    occurred_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_audit_project
  ON memory_audit_events (tenant_id, project_id, occurred_at);
```

审计只保存 ID、状态、原因、hash、计数和安全摘要，不保存完整 prompt、Evidence 正文或凭据。

## 6. Store 接口

在 MemoryStore 增加以下 repository 接口。Store 只负责项目、时间、状态和索引查询，不依赖 `EffectiveAccessScope`；授权由 `MemoryAuthorizationService` 在 application 层统一执行：

```python
def list_memories(
    self,
    project: ProjectRef,
    *,
    include_inactive: bool = False,
    as_of: datetime | None = None,
    fact_key: str | None = None,
) -> tuple[ProjectMemory, ...]

def get_memory(
    self,
    memory_id: UUID,
    *,
    project: ProjectRef,
    include_inactive: bool = False,
    as_of: datetime | None = None,
) -> ProjectMemory | None

def search_memories(
    self,
    project: ProjectRef,
    query: str,
    *,
    memory_types: tuple[MemoryType, ...] = (),
    subject: str | None = None,
    as_of: datetime | None = None,
    limit: int = 128,
) -> tuple[ProjectMemory, ...]

def list_memory_versions(
    self,
    project: ProjectRef,
    fact_key: str,
) -> tuple[ProjectMemory, ...]

def mark_needs_review(
    self,
    memory_id: UUID,
    *,
    reason: ReviewReason,
) -> ProjectMemory

def revoke_memory(
    self,
    memory_id: UUID,
    *,
    revoked_by: str,
    reason: str,
    at: datetime | None = None,
) -> ProjectMemory
```

所有实现必须提供 InMemory 和 SQLite 版本，行为保持一致。

### 6.1 原子替换

新增：

```python
def approve_replacement(
    self,
    proposal_id: UUID,
    *,
    decided_by: str,
) -> tuple[MemoryProposal, ProjectMemory, ProjectMemory | None]
```

`approve_replacement()` 必须在事务内按 `proposal_id` 重新读取当前 Proposal，不能接受调用方传入的可变 Proposal 对象。事务内完成 Proposal 锁定、过期检查、replacement target 校验、新记忆创建、旧记忆 `valid_to/status` 更新和审计写入。FTS 更新和 embedding invalidation 可以在 commit 后通过 outbox/重试任务执行，但不能影响事实事务的原子性。

`SQLiteDatabase` 需要新增：

```python
@contextmanager
def transaction(self) -> Iterator[sqlite3.Connection]:
    # BEGIN IMMEDIATE; commit on success; rollback on exception
    ...
```

事务内部不能调用当前“每次自动 commit”的 `execute()`；应提供 connection-local execute，或让 `execute()` 支持 `commit=False`。新数据库的启动顺序必须是：通用 migration runner -> `SQLiteMemoryStore` 创建 memory 基础表 -> memory migration runner -> FTS backfill -> summary backfill。

## 7. 授权服务

新增 `src/project_lens/context/memory_authorization.py`，集中处理搜索、详情和 Snapshot freeze 的授权。

### 7.1 候选授权

```python
def authorize_memory_candidates(
    memories: Sequence[ProjectMemory],
    *,
    project: ProjectRef,
    access_scope: EffectiveAccessScope,
    as_of: datetime | None = None,
) -> tuple[ProjectMemory, ...]
```

检查顺序：

```text
tenant/project
  -> status
  -> valid_from / valid_to
  -> visibility_scope
 -> evidence/source access
```

`visibility_scope` 必须先定义为可解析的受控标记。第一版只支持：

```text
role:{role_name}
chat:{chat_id}
actor:{actor_id}
source:{source_id}
```

空的 `visibility_scope` 表示继承项目默认可见范围，不表示公开。`source:{source_id}` 只能做精确匹配，不能用字符串前缀匹配，以免 `repo` 意外匹配 `repo-private`。授权实现使用 `EffectiveAccessScope` 的 actor、chat、role、readable_sources 和 forbidden_sources 做交集。

过滤失败不返回标题、ID 或正文，只在内部审计中记录 reason code。

### 7.2 详情授权

详情模式区分当前读取和历史读取。无历史权限时，过期记忆只返回 reason code；有历史权限时才返回完整旧版本和 Evidence 引用。

### 7.3 Evidence 检查

详情和发送前重新确认 Evidence 存在、未 revoked、revision 一致、actor 可读且 authority scope 匹配。没有有效 Evidence 的 FACT 只能降级为 `unverified`，不得进入 confirmed FACT 的回答渲染。SourceRecord 引用必须按 `(tenant_id, project_id, source_id, revision)` 精确查询，不能只按 source_id 查询。

## 8. 检索实现

### 8.1 Service 入口

新增 `src/project_lens/application/memory_retrieval_service.py`：

```python
class MemoryRetrievalService:
    def search_project_memory(
        self, *, project, access_scope, actor_id, chat_id,
        query, memory_types=(), subject=None, as_of=None, limit=5,
    ) -> MemoryRecallResult: ...

    def get_memory_detail(
        self, *, memory_id, project, access_scope,
        allow_historical=False, as_of=None,
    ) -> AuthorizedMemoryDetail: ...
```

service 负责 scope、授权、候选召回、排序和审计；memory_retrieval.py 只负责纯排序和渲染。

### 8.2 召回顺序

```text
1. 锁定 project / actor / chat scope
2. SQL 过滤 `status=active` + validity + project
3. 精确匹配 fact_key / subject / service / error code
4. FTS/BM25 生成最多 128 个候选
5. 在授权候选上执行 embedding
6. RRF 融合 lexical 和 semantic rank
7. 加入 freshness、authority、evidence 分数
8. 冲突检测
9. MMR 去重
10. 字符预算截断
11. 返回 MemoryCard
```

默认常量：

```python
MAX_MEMORY_CANDIDATES = 128
MAX_MEMORY_CARDS = 8
MAX_CARD_SNIPPET_CHARS = 420
MAX_MEMORY_BUDGET_CHARS = 4000
MAX_DETAIL_ITEMS = 2
MAX_DETAIL_CHARS = 6000
```

### 8.3 排序

```text
lexical       0.35
semantic      0.25
entity/type   0.15
freshness     0.10
authority     0.10
evidence      0.05
```

没有 embedding 时，重新归一化可用信号；不要因为 semantic=0 让所有结果整体降级。

### 8.4 时间查询

- 当前模式：as_of=None，只返回当前有效版本；
- 历史模式：返回 valid_from <= as_of 且 valid_to 为空或大于 as_of 的版本；
- 无法解析时间表达时保留当前模式并返回 time_filter_not_applied 警告；
- 时间区间使用半开区间 [valid_from, valid_to)。

### 8.5 冲突输出

新增：

```python
class MemoryConflict(FrozenModel):
    left_memory_id: UUID
    right_memory_id: UUID
    fact_key: str | None
    reason: str
    severity: Literal["warning", "blocking"]
```

同 fact_key 的 active 版本冲突时，工具结果必须包含 `active_fact_conflict`；Verifier 不能把任一版本直接标为确定事实。若两个版本的时间区间不重叠，则按 `as_of` 选择，不算当前冲突。

## 9. Hermes 工具契约

### 9.1 search_project_memory

输入：

```json
{
  "query": "支付回调为什么失败",
  "memory_types": ["risk", "decision"],
  "subject": "payment-callback-service",
  "as_of": null,
  "limit": 5
}
```

服务端从 `TrustedActorContext` 获取 tenant、project、actor、role 和 chat scope；请求体出现这些字段时直接以 `422 UNTRUSTED_SCOPE_FIELD` 拒绝，而不是“忽略后继续”。输出至少包含 ok、retrieval_mode、candidate_count、returned_count、omitted_count、budget_chars、memories、conflicts 和 warnings。

### 9.2 get_memory_detail

只接受 memory_id 和 include_history。服务端重新校验 scope。响应拆成 memory、evidence_refs、version、authorization 和 warnings。Evidence 正文由独立工具读取。`include_history=true` 仅在 TrustedActorContext 具有历史详情权限时生效；没有权限返回 `MEMORY_FORBIDDEN`，不返回“过期记忆存在”的侧信道信息。

### 9.3 search_project_history

支持 query、from、to、limit，限制最大时间范围和结果数。只返回 Episode 安全摘要，不返回完整事件 payload。

### 9.4 工具审计

每次调用记录 tool_name、run_id/trace_id、scope、query_hash、候选数、返回 ID、omitted_count、retrieval_mode、conflict_count、degraded_reason 和 policy_version。

## 10. 写入、审批和复核

### 10.1 Proposal 创建

统一入口：

```text
extract claim
  -> classify claim_type
  -> resolve fact_key
  -> validate evidence
  -> detect duplicate/conflict
  -> create pending MemoryProposal
```

来源包括 Verifier verified FACT、用户明确确认、Episode consolidation、Obsidian Inbox 和管理员手动创建。

HTTP 和飞书入口创建 Proposal 时，`project` 与 `proposed_by` 一律从 `TrustedActorContext` 推导。内部维护任务使用受信任的 system actor，并在审计中标记 `identity_source=maintenance_job`。原有可携带 `project/proposed_by` 的请求模型只保留给测试 fixture，生产路由不得绑定它。

### 10.2 审批校验

MemoryApprovalGateway 增加：

1. 服务端解析真实审批者；
2. 检查 allowed_approvers；
3. 检查 TTL；
4. 重新授权 evidence/source refs；
5. 检查 replacement target；
6. 原子 approve/reject；
7. 写入审计；
8. 发出 MEMORY_APPROVED、MEMORY_REJECTED 或 MEMORY_REPLACED 事件。

### 10.3 复核服务

新增 `src/project_lens/application/memory_review_service.py`：

```python
def open_review_for_source_revision(source_id, old_revision, new_revision):
    ...

def open_review_for_revoked_evidence(evidence_id):
    ...

def open_due_reviews(*, now, project=None):
    ...
```

重复触发必须幂等，使用 `memory_id + reason + trigger_key`。`trigger_key` 固定为 `source:{source_id}:{revision}`、`evidence:{evidence_id}`、`due:{YYYY-MM-DD}` 或 `manual:{request_id}`，并由 `uq_memory_reviews_trigger` 约束。

### 10.4 管理 API

```text
POST /api/v1/memories/{memory_id}/revoke
POST /api/v1/memories/{memory_id}/reviews/{review_id}/resolve
GET  /api/v1/projects/{tenant_id}/{project_id}/memory-reviews
GET  /api/v1/memories/{memory_id}/versions
```

撤销不删除 payload，只设置 status=revoked、valid_to 和审计字段。`revoke` 与 `supersede` 是不同状态转换：前者表示事实或依据失效；后者表示出现了经批准的新版本。不得使用同一个 `_revoke_memory()` 辅助函数同时表达二者。

## 11. Consolidation 和后台任务

### 11.1 Episode Consolidation

```text
Episode
  -> 去占位摘要
  -> 需要 Evidence
  -> 相似摘要聚类/去重
  -> 提取候选 claim
  -> resolve fact_key
  -> create pending proposal
```

后台任务禁止调用 decide_proposal，只能创建 Proposal。任务写入 Proposal、review 或 outbox 时必须使用 idempotency key；若任务在 commit 后、派发事件前失败，outbox 重试负责补发，不重新创建事实或 review。

### 11.2 Maintenance

提供应用层函数：

```python
def run_memory_maintenance(
    *, project: ProjectRef, now: datetime, max_episodes: int = 100
) -> MemoryMaintenanceReport
```

报告至少包含 episodes_scanned、proposals_created、duplicates_skipped、conflicts_skipped、reviews_opened、embedding_invalidated 和 errors。

幂等 key：

```text
memory-maintenance:{tenant_id}:{project_id}:{date}:{batch}
```

### 11.3 Retention

- Episode / Observation 按项目 retention 清理；
- AgentEvent 按审计策略保留；
- ProjectMemory 软失效，不参与普通 retention 删除；
- review/approval 审计保留时间不短于关联记忆；
- 清理前写入删除计数和 policy version。

## 12. Context Composer 集成

### 12.1 Candidate

Memory candidate 只保存引用：

```text
kind = project_memory
source_id = memory_id
source_hash = content_hash
fact_key
valid_from
valid_to
status_at_selection
```

### 12.2 Freeze

freeze_and_start() 逐项验证 project、actor/chat scope、status、as_of validity、Evidence、content_hash 和 token budget。失败返回 reason code，不静默替换新正文。

### 12.3 Replay

Snapshot 保存 memory_id、fact_key、content_hash、source_refs、validity、authorization_result、renderer_version 和 included/rejected/changed 状态。

## 13. API 设计

### 13.1 Proposal

当前已存在的旧契约（仅用于兼容迁移，不符合本规格的可信身份要求）：

```text
POST /api/v1/projects/memory-proposals
POST /api/v1/memory-proposals/{proposal_id}/decision
GET  /api/v1/projects/{tenant_id}/{project_id}/memories
```

目标契约中计划新增或替换：

```text
GET  /api/v1/memory-proposals/{proposal_id}
```

创建请求只允许 claim_text、memory_type、claim_type、evidence_ids、source_refs、subject、claim_slot、valid_from、review_due_at、replaces_memory_id 和 reason。`project`、`proposed_by`、`allowed_approvers` 和 `decided_by` 由 TrustedActorContext 和项目审批策略推导；客户端传入任一字段应返回 `422 UNTRUSTED_SCOPE_FIELD`。

### 13.2 读取（目标契约）

```text
GET /api/v1/projects/{tenant_id}/{project_id}/memories/search
GET /api/v1/projects/{tenant_id}/{project_id}/memories/{memory_id}
GET /api/v1/projects/{tenant_id}/{project_id}/memories/{memory_id}/versions
GET /api/v1/projects/{tenant_id}/{project_id}/memory-summary
```

错误码：

| 错误码 | HTTP | 含义 |
| --- | ---: | --- |
| MEMORY_NOT_FOUND | 404 | ID 不存在或不属于当前项目 |
| MEMORY_FORBIDDEN | 403 | 当前 actor 无权读取 |
| MEMORY_EXPIRED | 409 | 记忆当前不可用 |
| MEMORY_REVOKED | 409 | 记忆被撤销 |
| MEMORY_CONFLICT | 409 | 版本或事实冲突 |
| CONTEXT_CHANGED | 409 | Snapshot hash 已变化 |
| MEMORY_BUDGET_EXCEEDED | 413 | 超出详情或卡片预算 |
| MEMORY_INVALID_PROVENANCE | 422 | 来源引用无效 |
| MEMORY_APPROVAL_EXPIRED | 409 | Proposal 已过期 |
| UNTRUSTED_SCOPE_FIELD | 422 | 请求体试图覆盖服务端身份或范围 |

## 14. 测试规格

### 14.1 Domain

- fact_key 对大小写、空格、路径和中英文稳定；
- 相同 fact_key 无 replacement 时 Proposal 失败；
- replacement 只能指向同项目 active memory；
- approval 产生新版本并关闭旧版本；
- valid_from/valid_to 按半开区间判断；
- status 与 validity 不一致时默认读取排除；
- 无 Evidence 的 FACT Proposal 被拒绝。
- `MemorySourceRef` 只能引用同租户、同项目、指定 revision 的 SourceRecord；
- 客户端伪造 `MemorySourceRef.content_hash` 或 `source_type` 不会覆盖服务端 SourceRecord。

### 14.2 Store

- SQLite 迁移可重复执行；
- 旧 payload 缺少新字段仍可读取；
- 坏 payload 不阻塞其他记忆读取；
- SQL project/tenant 过滤生效；
- FTS miss fallback 生效；
- replacement 事务失败时无部分提交；
- review queue 幂等；
- revoke 保留 payload 和 audit；
- source_refs 的 JSON 兼容读取、索引回填和精确 revision 查询；
- InMemory 与 SQLite 行为一致。

### 14.3 Retrieval

- 未授权候选不会进入 embedding scorer；
- expired/revoked/pending 不返回；
- limit=999 仍最多返回 8；
- 单条和总字符预算生效；
- 重复文本去重；
- 同 fact_key 冲突标记；
- as_of 返回正确历史版本；
- embedding 异常降级 BM25；
- 中文无空格查询在 FTS miss 后进入应用层 CJK tokenizer/BM25，并验证相关记忆位于 Top-K；
- 结果包含 channel 和 omitted_count。

### 14.4 权限

- 伪造 tenant/project/actor 不生效；
- memory_id 不能跨项目读取；
- chat visibility 改变后详情被拒绝；
- Evidence revoke 后详情和 Snapshot freeze 被拒绝；
- SourceRecord revision/content hash 不一致时拒绝确认事实并打开 review；
- 角色降权后 Draft 不能静默发送；
- summary 不泄露不可见主题或数量；
- 向量缓存隔离 tenant/project/model/content hash。

### 14.5 生命周期和 Agent

- SourceRecord revision 变化打开 review；
- Evidence revoke 打开 review；
- review due 打开 review；
- 相同触发器不重复建 review；
- revoke 后默认搜索排除；
- retention 不删除 ProjectMemory；
- Hermes 初始上下文不含全量记忆正文；
- search -> detail 顺序可回放；
- 工具调用带 run_id/trace_id；
- memory hash 变化返回 CONTEXT_CHANGED；
- 未命中回答 unknown；
- 冲突回答同时展示冲突和依据；
- 未审批 Proposal 不出现在 approved 搜索。

### 14.6 评测数据

建议新增：

```text
evaluation/memory_cases.json
evaluation/memory_security_cases.json
evaluation/memory_temporal_cases.json
```

case 至少包含 id、project、query、as_of、relevant_memory_ids、forbidden_memory_ids、expected_abstention、expected_conflict 和 expected_evidence_ids。

发布阻断：

- cross-tenant/project leakage > 0；
- revoked/expired memory usage > 0；
- FACT without valid provenance > 0；
- Snapshot mismatch > 0；
- temporal accuracy < 95%；
- citation correctness < 95%；
- Recall@5 低于基线；
- embedding 调用失败后检索降级失败率 > 0；
- 降级路径出现越权结果、空结果误报或服务端异常 > 0。

## 15. 观测指标

```text
memory.search.count
memory.search.latency_ms
memory.search.returned_count
memory.search.omitted_count
memory.search.conflict_count
memory.search.degraded_count
memory.detail.count
memory.proposal.created
memory.proposal.approved
memory.proposal.rejected
memory.review.opened
memory.review.resolved
memory.embedding.cache_hit
memory.embedding.failure
memory.snapshot.rejected
memory.auth.denied
memory.auth.cross_scope_attempt
memory.invalid_provenance
memory.expired_read_attempt
memory.revoked_read_attempt
memory.audit.write_failure
```

日志中不打印正文、Evidence 内容、完整 prompt、token、cookie 或凭据。

## 16. 分阶段任务拆解

### Sprint 1：契约和迁移基础

交付新枚举、兼容字段、schema migration、索引列、fact identity helper、旧 payload 测试和三条 ADR。

完成标准：旧数据库启动成功，新旧数据可读，迁移可重复执行。

### Sprint 2：授权读取服务

交付 memory_authorization、MemoryRetrievalService、统一 scope、错误码、工具审计和跨项目/失效记忆测试。

完成标准：所有读取先授权，再检索和渲染。

### Sprint 3：时间、替换和复核

交付 fact_key 版本链、原子 replacement、MemoryStatus、review queue/store/service、revision/revoke trigger、revoke/versions/review API。

完成标准：可查询历史版本，解释替换和失效原因，review 幂等。

### Sprint 4：混合检索和历史闭环

交付 subject/memory_type/as_of 查询、SQL+FTS 候选、embedding 版本隔离、Episode/Observation 审计、maintenance job 和 retention 报告。

完成标准：向量故障安全降级，历史检索不污染正式事实。

### Sprint 5：Context Composer

交付 source hash、freeze 二次授权、renderer version、CONTEXT_CHANGED 和 Snapshot replay detail。

完成标准：AgentRun 可回看实际注入的 memory ID、版本和授权结果。

### Sprint 6：评测和灰度

交付 100 条以上匿名真实问题、temporal/conflict/abstention/security cases、报告、dashboard、feature flag 和回滚说明。

完成标准：发布阻断条件通过，内部项目灰度稳定。

## 17. 回滚策略

- feature flag 关闭新 search/detail/review；
- 保留旧 list_memories 和 Proposal API；
- 不删除新表、审计或旧 payload；
- embedding 关闭后退回 BM25；
- replacement 错误通过 revoke 新记忆并恢复旧版本有效期；
- Snapshot 和 AgentRun 审计不可删除。

## 18. 完成定义

### 功能

- [ ] ProjectMemory 支持 fact_key、subject、source_refs、authority、validity 和 review；
- [ ] Proposal、审批、替换、撤销和复核闭环可用；
- [ ] search/detail/history 走统一授权服务；
- [ ] 时间查询返回正确版本；
- [ ] consolidation 只生成 Proposal；
- [ ] Context Composer 可冻结和回放 memory 引用。

### 安全

- [ ] 候选先做 tenant/project/chat/actor scope 过滤；
- [ ] Evidence 撤销和权限变化在详情/发送前被阻断；
- [ ] 向量索引无 ACL 旁路；
- [ ] 未授权 ID、标题和正文不进入响应或日志；
- [ ] 跨租户、跨项目泄漏为零。

### 质量和运维

- [ ] Recall/MRR/nDCG 有基线和回归报告；
- [ ] 时间、冲突、拒答和引用正确性有评测；
- [ ] stale/revoked/needs_review 不会默认作为当前事实；
- [ ] 预览与实际输入可复现；
- [ ] embedding 失败可安全降级；
- [ ] migration、review、retention 和 consolidation 支持幂等；
- [ ] 搜索、审批、复核、降级和拒绝都有指标。

## 19. 开发顺序原则

```text
先固定契约，再改存储。
先授权过滤，再做召回。
先保证时间和来源，再优化向量。
先 Proposal，再做自动巩固。
先完成可回放，再扩大上下文。
先用真实评测证明需要，再考虑关系图。
```
