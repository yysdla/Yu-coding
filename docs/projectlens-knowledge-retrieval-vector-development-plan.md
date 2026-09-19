# ProjectLens 知识库向量检索分阶段开发文档

**版本**：v1.0  
**日期**：2026-09-18  
**状态**：可执行开发计划  
**关联方案**：[projectlens-knowledge-retrieval-vector-and-evaluation-plan.md](projectlens-knowledge-retrieval-vector-and-evaluation-plan.md)  
**关联规格**：[projectlens-long-term-memory-development-spec.md](projectlens-long-term-memory-development-spec.md)

## 1. 文档目的

本文档把知识库向量化、混合召回、知识治理和评测体系拆成可以逐阶段开发和验收的工程任务。

目标不是简单新增一个向量数据库，而是完成下面这条完整链路：

```text
外部来源同步
  -> SourceRecord / Evidence 标准化
  -> KnowledgeDocument / Chunk 投影
  -> FTS/BM25 + embedding 索引
  -> ACL 前置过滤
  -> exact + BM25 + vector 混合召回
  -> 确定性重排和去重
  -> Evidence / Memory / Wiki 结果
  -> Hermes 按需读取
  -> 答案引用验证
  -> 离线评测和生产质量门禁
```

本文档是工程实施顺序，不取代产品边界和安全规则。任何阶段都必须遵守：

1. ProjectLens 负责权限、来源、时间、审批和审计；
2. Hermes 负责意图理解、工具选择和答案表达；
3. 向量只负责语义召回，不负责判断事实是否成立；
4. Obsidian Wiki、Episode、Observation 都是派生或历史层，不能替代 Evidence；
5. 任何失败都必须保持 BM25/精确检索可用，不能因向量故障扩大权限或阻塞来源同步。

## 2. 当前基线

### 2.1 已有能力

| 能力 | 当前实现 |
|---|---|
| Evidence 检索 | `src/project_lens/context/engine.py` |
| 精确代码检索 | `src/project_lens/context/retrieval/exact.py` |
| BM25 | `src/project_lens/context/retrieval/bm25.py` |
| RRF | `src/project_lens/context/retrieval/fusion.py` |
| 中文和标识符分词 | `src/project_lens/context/retrieval/tokenize.py` |
| ProjectMemory 检索 | `src/project_lens/context/memory_retrieval.py` |
| embedding provider/cache | `src/project_lens/context/embeddings.py` |
| Episode 检索 | `src/project_lens/context/history_store.py` |
| SourceRecord | `src/project_lens/context/source_records.py` |
| Obsidian Wiki | `src/project_lens/obsidian/` |
| Hermes 工具门面 | `src/project_lens/application/project_agent_tools.py` |
| 基础检索评测 | `src/project_lens/evaluation/` |

### 2.2 当前主要缺口

1. `ContextEngine` 的 Evidence 主链路尚未接入 embedding；
2. ProjectMemory/Episode 的 embedding 实现没有和 Evidence/Wiki 共用索引契约；
3. 没有统一的 chunk、index job、embedding version 和 provenance 结构；
4. Obsidian Wiki 还没有进入统一混合检索链路；
5. 已有评测偏记忆排序，缺少统一的召回、回答、引用、时态、冲突、拒答和安全报告；
6. 没有完整的基线对比、灰度开关和发布阻断流程。

### 2.3 不在本计划范围内

本轮不做：

- GraphRAG 或通用图数据库；
- 让 Hermes 直接访问向量库、SQLite 或文件系统；
- 自动审批 MemoryProposal；
- 把聊天记录或 Episode 自动升级为正式事实；
- 以向量结果绕过 ACL、有效期、审批和 Evidence；
- 在没有黄金评测数据的情况下直接启用 vector-only；
- 为每一种来源建立完全独立的检索内核。

## 3. 总体阶段

| 阶段 | 名称 | 主要结果 |
|---|---|---|
| 0 | 方案冻结与评测基线 | 固定契约、开关、基线和回滚边界 |
| 1 | 统一知识索引契约 | KnowledgeDocument、Chunk、EmbeddingRecord、IndexJob |
| 2 | 统一 chunk 与来源投影 | Evidence/SourceRecord/Memory/Episode/Wiki 可进入同一索引 |
| 3 | 向量索引与增量构建 | embedding 生成、缓存、重建、失败恢复 |
| 4 | Evidence 主链路混合检索 | ContextEngine 支持 exact + BM25 + vector |
| 5 | Memory/Episode/Wiki 统一检索 | Hermes 统一搜索协议和来源层级 |
| 6 | 详情、验证与 Snapshot 回放 | search -> detail -> citation -> replay |
| 7 | 评测平台与质量门禁 | 黄金集、指标、报告、安全阻断 |
| 8 | 灰度、运营与生产化 | feature flag、监控、回滚和规模升级 |

阶段必须按顺序推进。阶段 4 之前不允许改变生产 Evidence 默认排序；阶段 7 之前不允许把向量质量作为上线结论。

## 4. 阶段 0：方案冻结与评测基线

### 4.1 目标

在修改检索代码前，固定数据边界、检索模式、评测指标、配置项和回滚方式。这个阶段不接入真实向量服务。

### 4.2 主要工作

1. 固定三种检索模式：
   - `lexical`：exact + BM25；
   - `vector`：仅用于诊断，不作为生产默认；
   - `hybrid`：exact + BM25 + vector。
2. 增加配置：

```text
PROJECT_LENS_KNOWLEDGE_RETRIEVAL_MODE=lexical
PROJECT_LENS_KNOWLEDGE_VECTOR_ENABLED=false
PROJECT_LENS_KNOWLEDGE_EMBEDDING_MODEL=
PROJECT_LENS_KNOWLEDGE_EMBEDDING_VERSION=
PROJECT_LENS_KNOWLEDGE_CHUNKER_VERSION=v1
PROJECT_LENS_KNOWLEDGE_MAX_CANDIDATES=200
PROJECT_LENS_KNOWLEDGE_MAX_RESULTS=8
PROJECT_LENS_KNOWLEDGE_VECTOR_TIMEOUT_SECONDS=10
PROJECT_LENS_KNOWLEDGE_VECTOR_FALLBACK=true
```

3. 固定指标和门禁初值；
4. 把现有 context replay 数据纳入 BM25 基线；
5. 建立一份 30～50 条开发期评测集，包含中文、英文、代码、错误码、项目隔离和空结果；
6. 写 ADR：
   - ACL 先于 embedding；
   - vector 不是事实源；
   - 默认保留 BM25；
   - 向量失败安全降级；
   - Wiki/History 不得直接充当 confirmed fact。

### 4.3 预计代码和文档

- `src/project_lens/config.py`
- `src/project_lens/context/retrieval/types.py`
- `src/project_lens/evaluation/`
- `evaluation/retrieval/`
- `docs/adr/knowledge-retrieval-*.md`
- `tests/test_retrieval_config.py`

### 4.4 测试

- 默认配置仍使用 lexical；
- vector flag 关闭时不创建外部 embedding client；
- 测试模式禁止真实网络调用；
- 评测 case schema 校验；
- 基线报告可生成；
- 回滚到 lexical 后结果结构兼容。

### 4.5 完成标准

- 没有任何生产请求默认调用 embedding；
- 评测集可运行并生成 JSON 报告；
- 所有新配置有安全默认值；
- 现有 BM25 测试全部保持通过。

### 4.6 不做

- 不改变 `ContextEngine.search()` 排序；
- 不新增向量数据库；
- 不修改 Hermes 工具结果语义。

## 5. 阶段 1：统一知识索引契约

### 5.1 目标

定义所有可检索知识对象的统一索引投影，但暂不改变实际召回结果。

### 5.2 新增数据结构

建议新增：

```text
src/project_lens/context/knowledge_index/
  __init__.py
  models.py
  adapters.py
  chunking.py
  store.py
  jobs.py
```

核心模型：

```python
class KnowledgeDocument:
    id: str
    tenant_id: str
    project_id: str
    kind: str
    object_id: str
    source_system: str
    source_id: str
    revision: str | None
    title: str
    text: str
    evidence_ids: tuple[str, ...]
    source_record_key: str | None
    fact_key: str | None
    subject: str | None
    status: str
    valid_from: datetime | None
    valid_to: datetime | None
    observed_at: datetime | None
    access_scope: str
    visibility_scope: tuple[str, ...]
    content_hash: str
    indexable: bool
```

```python
class KnowledgeChunk:
    chunk_id: str
    document_id: str
    object_kind: str
    object_id: str
    tenant_id: str
    project_id: str
    source_id: str
    revision: str | None
    title_path: tuple[str, ...]
    text: str
    content_hash: str
    chunker_version: str
    chunk_index: int
    chunk_count: int
    indexable: bool
```

```python
class EmbeddingRecord:
    chunk_id: str
    tenant_id: str
    project_id: str
    model_version: str
    dimensions: int
    content_hash: str
    vector: tuple[float, ...]
    status: str
    created_at: datetime
```

```python
class KnowledgeIndexJob:
    id: str
    tenant_id: str
    project_id: str
    object_kind: str
    object_id: str
    content_hash: str
    model_version: str
    status: str
    attempt_count: int
    last_error: str | None
```

### 5.3 SQLite 表

```sql
knowledge_documents
knowledge_chunks
knowledge_embeddings
knowledge_index_jobs
knowledge_chunks_fts
```

所有表必须带 `tenant_id` 和 `project_id`。向量缓存主键至少包含：

```text
tenant_id, project_id, object_kind, object_id,
chunk_id, model_version, content_hash, chunker_version
```

### 5.4 Adapter 设计

每种来源实现只负责把业务对象转换为 `KnowledgeDocument`：

```text
EvidenceAdapter
SourceRecordAdapter
ProjectMemoryAdapter
EpisodeAdapter
ObsidianWikiAdapter
```

Adapter 不执行 ACL 决策。它只写入对象原本的 tenant/project/status/revision/access metadata，授权在查询服务端完成。

### 5.5 测试

新增：

- `tests/test_knowledge_index_contracts.py`
- `tests/test_knowledge_index_store.py`
- `tests/test_knowledge_index_migrations.py`

覆盖：

- JSON round-trip；
- 稳定 ID；
- content hash；
- tenant/project 隔离；
- migration 幂等；
- duplicate upsert；
- revision 保留；
- revoked/expired 的 indexable 状态；
- 坏 payload 不阻塞其他对象。

### 5.6 完成标准

- 五种对象都可以生成标准投影；
- 重复同步不产生重复 document/chunk；
- revision 更新会保留旧投影；
- 可以从 chunk 返回原始对象引用；
- 现有检索行为没有改变。

## 6. 阶段 2：统一 chunk 与来源投影

### 6.1 目标

把 Evidence、SourceRecord、ProjectMemory、Episode、Obsidian Wiki 统一转换为稳定、可回链的 chunk。

### 6.2 Chunk 策略

| 对象 | Chunk 策略 | 必须保留的 metadata |
|---|---|---|
| Code Evidence | module/class/function | file、symbol、start_line、end_line |
| Document Evidence | 段落窗口 | title、source_id、revision |
| SourceRecord | 按正文段落 | source_type、authority、revision |
| Bitable/Task | 一条记录一个 chunk | task_id、status、owner、version |
| Meeting | 议题/决策/行动项 | meeting_id、date、participants |
| ProjectMemory | 一条记忆一个 chunk | fact_key、memory_type、validity |
| Episode | 标题 + 摘要 | run_id、status、observed_at |
| Obsidian Wiki | 标题层级 + 段落 | page_type、source_keys、derived |

### 6.3 代码工作

扩展：

- `src/project_lens/context/chunking.py`
- `src/project_lens/context/indexing/common.py`
- `src/project_lens/context/source_records.py`
- `src/project_lens/obsidian/repository.py`

新增：

- `KnowledgeChunkBuilder`
- `stable_chunk_id()`
- `build_document_text()`
- `build_provenance_metadata()`

建议 chunk ID：

```text
sha256(
  tenant_id + project_id + kind + object_id +
  revision + chunker_version + chunk_index + content_hash
)
```

### 6.4 安全要求

- pending proposal 不可进入默认检索索引；
- revoked/expired memory 默认 `indexable=false`；
- Wiki 必须保存 `derived=true`；
- Episode 必须保存 `historical=true`；
- chunk 不得保存 secret、token、cookie 或完整审计参数；
- source key、revision、Evidence ID 必须可回链。

### 6.5 测试

- 中文无空格文本；
- 英文和中英混合；
- Python 代码；
- markdown 标题；
- 长文 overlap；
- 同一对象重建 ID 不变；
- 原文变化只影响相应 chunk；
- source revision 变化不覆盖旧 chunk；
- Obsidian manifest 外文件不进入索引；
- pending/revoked/expired 内容被排除。

### 6.6 完成标准

所有知识对象都有稳定 chunk，且每个 chunk 都能回到：

```text
tenant -> project -> object -> source/revision -> evidence/detail
```

## 7. 阶段 3：向量索引与增量构建

### 7.1 目标

在统一 chunk 基础上实现 embedding 生成、缓存、增量更新、模型升级和故障恢复。

### 7.2 Provider 接口

复用并扩展 `src/project_lens/context/embeddings.py`：

```python
class EmbeddingProvider(Protocol):
    @property
    def model_version(self) -> str: ...
    @property
    def dimensions(self) -> int | None: ...
    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...
```

Provider 必须：

- 校验返回数量；
- 校验向量非空；
- 校验维度一致；
- 限制 batch 大小；
- 支持 timeout；
- 不打印正文和 query；
- 抛出可分类的 provider error。

### 7.3 缓存和任务

实现：

```text
upsert_chunks()
enqueue_missing_embeddings()
embed_pending_batch()
mark_embedding_failed()
rebuild_for_model_version()
activate_index_version()
```

以下变化重新 embedding：

- text/content_hash 改变；
- chunker_version 改变；
- model_version 改变；
- dimensions 改变。

以下变化只更新 metadata：

- actor 权限变化；
- chat 可见性变化；
- authority 排序；
- freshness。

### 7.4 模型升级策略

新模型不能直接覆盖旧模型：

```text
old model active
  -> build new model in background
  -> run offline evaluation
  -> activate new model
  -> retain old model for rollback window
```

### 7.5 测试

- provider 正常返回；
- provider timeout；
- provider 维度错误；
- provider 返回数量错误；
- cache hit；
- content hash 变化重算；
- model version 变化重算；
- 项目和租户隔离；
- 部分 batch 失败后可重试；
- embedding 失败不影响 BM25；
- 旧模型回滚。

### 7.6 完成标准

- 向量索引可以增量生成；
- 同一内容和模型不会重复调用 provider；
- 模型升级可并行构建和原子切换；
- 向量失败时对象仍可由 lexical 检索；
- 未授权内容不会进入 embedding scorer。

## 8. 阶段 4：Evidence 主链路混合检索

### 8.1 目标

把向量能力接入 `ContextEngine.search()`，但保持精确检索、BM25、ACL 和原有答案契约兼容。

### 8.2 召回顺序

```text
ContextQuery
  -> resolve project / access
  -> ACL filter
  -> query parser
  -> exact
  -> BM25
  -> vector over authorized candidates
  -> RRF
  -> deterministic rerank
  -> deduplicate/MMR
  -> EvidenceBundle
```

### 8.3 新增模块

- `src/project_lens/context/retrieval/vector.py`
- `src/project_lens/context/retrieval/query_parser.py`
- `src/project_lens/context/retrieval/rerank.py`
- `src/project_lens/context/retrieval/pipeline.py`

扩展：

- `src/project_lens/context/engine.py`
- `src/project_lens/context/retrieval/types.py`
- `src/project_lens/context/models.py`

### 8.4 结果字段

`ChannelHit` / `RetrievalHit` 增加：

```text
channels
channel_scores
channel_ranks
model_version
source_revision
provenance
degraded_reason
```

`EvidenceBundle.retrieval_trace` 增加：

```text
retrieval_mode
vector_enabled
embedding_model
candidate_count
authorized_candidate_count
channel_counts
vector_failure
fallback_reason
rerank_version
```

### 8.5 评分建议

首版采用加权 RRF：

```text
exact       0.30
lexical     0.30
semantic    0.25
metadata    0.10
freshness   0.05
```

向量不可用时重新归一化，不把 semantic=0 作为负分。

### 8.6 关键安全测试

- restricted Evidence 不进入 scorer；
- 其他 project 的 Evidence 不进入 scorer；
- revoked Evidence 不返回；
- vector provider 失败仍返回 BM25；
- vector 返回高分的 forbidden item 仍不可见；
- error code/path 查询 exact/BM25 结果不被语义结果挤掉；
- traceback 仍优先走 exact code channel。

### 8.7 完成标准

- lexical 模式行为完全兼容；
- hybrid 模式可通过 feature flag 开启；
- vector provider 不可用时 100% 降级；
- retrieval trace 可解释；
- 初步评测中 hybrid Recall@5 不低于 BM25 baseline。

## 9. 阶段 5：Memory、Episode、Wiki 统一检索

### 9.1 目标

让 ProjectMemory、Episode、MemoryObservation 和 Obsidian Wiki 使用统一检索基础设施，同时保留不同的事实等级和读取权限。

### 9.2 来源优先级

默认优先级：

```text
Evidence / SourceRecord
  > approved ProjectMemory
  > derived Obsidian Wiki
  > historical Episode / Observation
```

这不是简单的固定分数。最终排序还要考虑：

- 查询意图；
- 时间；
- authority；
- source revision；
- Evidence completeness；
- 是否为历史问题。

### 9.3 工作内容

扩展：

- `src/project_lens/context/memory_retrieval.py`
- `src/project_lens/context/history_store.py`
- `src/project_lens/obsidian/repository.py`
- `src/project_lens/application/memory_retrieval_service.py`
- `src/project_lens/application/project_agent_tools.py`

新增或统一：

- `UnifiedKnowledgeRetrievalService`
- `KnowledgeResult`
- `KnowledgeRetrievalPolicy`
- `SourceLayer`

### 9.4 工具行为

`projectlens_search_context`：

- 默认返回 Evidence；
- 可按 source kind 过滤；
- Wiki 结果标记 `derived=true`；
- Episode 结果标记 `historical=true`；
- 不把 pending proposal 当成 approved memory。

`projectlens_search_project_memory`：

- 只返回已授权、已审批、当前有效的 ProjectMemory；
- 支持 vector/hybrid；
- 保留现有 limit、budget、conflict、MMR 约束。

`projectlens_search_project_history`：

- 只返回安全 Episode 摘要；
- 不返回完整 prompt、工具参数和原始事件；
- 结果只作为线索，不直接成为 FACT。

`projectlens_search_wiki`：

- 只读取 manifest 登记页面；
- 返回 derived/source_keys/status；
- 重要事实必须继续读取 Evidence。

### 9.5 测试

- 正式 Evidence 优先；
- Wiki 不能覆盖正式 Evidence；
- Episode 不能成为 confirmed fact；
- pending/rejected/revoked/expired 不出现在默认结果；
- 结果包含 source layer；
- kind/source/status/time filters；
- 跨项目、跨 tenant、Obsidian manifest 和 chat ACL；
- vector scorer 只接收已授权候选。

### 9.6 完成标准

Hermes 无需知道底层是哪个存储或向量后端，只通过工具得到：

```text
结果对象
  + source layer
  + provenance
  + retrieval channels
  + status
  + detail_available
```

## 10. 阶段 6：详情、答案验证与 Snapshot 回放

### 10.1 目标

保证召回结果在真正进入模型答案前再次经过权限、状态、来源和内容 hash 校验，并能够回放一次运行实际使用了哪些对象。

### 10.2 详情链路

```text
search result
  -> object detail
  -> Evidence/source revision validation
  -> claim construction
  -> verifier
  -> answer
```

详情读取必须重新检查：

- tenant/project；
- actor/chat scope；
- status；
- valid_from/valid_to；
- Evidence revoked 状态；
- SourceRecord revision；
- content_hash；
- source authority；
- 当前 policy version。

### 10.3 Snapshot 字段

Context Snapshot 至少保存：

```text
object_id
chunk_id
kind
content_hash
source_record_key
evidence_ids
valid_from
valid_to
authorization_result
retrieval_mode
retrieval_channels
renderer_version
included/rejected/changed
```

### 10.4 失败行为

- hash 变化：返回 `CONTEXT_CHANGED`；
- 权限变化：拒绝继续使用；
- Evidence 撤销：事实降级或拒答；
- SourceRecord revision 不匹配：标记 stale/conflict；
- Wiki 内容变化：重新读取 manifest 和页面；
- vector index stale：可以使用 lexical 结果并记录 warning。

### 10.5 测试

- 搜索后撤销权限；
- 搜索后撤销 Evidence；
- 搜索后 SourceRecord 产生新 revision；
- 搜索后正文 hash 变化；
- replay 使用同一 index snapshot；
- preview/send 使用同一 renderer；
- confirmed claim 缺 Evidence 时验证失败；
- 冲突内容不能被自动选边。

### 10.6 完成标准

- 任何过期或撤销对象都不能静默进入最终答案；
- 每条 FACT claim 都能回到 Evidence；
- AgentRun 可以回看实际使用对象和版本；
- preview 与 send 的上下文一致。

## 11. 阶段 7：评测平台与质量门禁

### 11.1 目标

建立可重复、可分层、可阻断发布的知识检索评测系统。

### 11.2 评测数据目录

```text
evaluation/
  retrieval/
    golden_cases.jsonl
    hard_negatives.jsonl
    temporal_cases.jsonl
    conflict_cases.jsonl
  answer/
    answer_cases.jsonl
  security/
    acl_cases.jsonl
    revoked_expired_cases.jsonl
    snapshot_cases.jsonl
  configs/
    retrieval_baseline.json
    gates.json
```

### 11.3 Case 必备字段

```json
{
  "id": "case-001",
  "tenant_id": "demo",
  "project_id": "payment",
  "chat_id": "chat-1",
  "query": "支付回调为什么失败？",
  "expected_intent": "incident",
  "as_of": null,
  "relevant_object_ids": ["..."],
  "relevant_source_keys": ["..."],
  "forbidden_object_ids": ["..."],
  "expected_evidence_ids": ["..."],
  "expected_abstention": false,
  "expected_conflict": false,
  "difficulty": "hard",
  "tags": ["zh", "vector-rescue"]
}
```

### 11.4 召回指标

必须报告 overall 和分层指标：

- Recall@1、Recall@5、Recall@10；
- MRR；
- nDCG@5；
- HitRate@K；
- irrelevant rate；
- duplicate chunk rate；
- source diversity；
- conflict precision/recall；
- stale result rate；
- vector rescue rate；
- vector false positive rate；
- P50/P95 latency；
- embedding cache hit rate；
- fallback success rate。

### 11.5 答案指标

- factual accuracy；
- citation correctness；
- citation completeness；
- evidence coverage；
- completeness；
- temporal correctness；
- conflict handling；
- abstention precision；
- unsupported claim rate；
- answer latency；
- token cost。

确定性检查优先于 LLM judge：

```text
claim 引用存在
引用 Evidence 属于同一项目
当前 actor 可读取 Evidence
revision/validity 正确
Evidence 未 revoked
```

LLM judge 仅用于复杂语义支持、完整性和表达质量；低置信度样本交人工复核。

### 11.6 安全门禁

以下任一项非零则失败：

- cross-tenant leakage；
- cross-project leakage；
- forbidden source returned；
- revoked/expired result used；
- pending proposal rendered as current fact；
- unauthorized scorer input；
- unauthorized detail read；
- snapshot hash mismatch silently accepted；
- unapproved ProjectMemory write。

### 11.7 质量门禁初值

上线前建议：

```text
golden cases >= 100
hybrid Recall@5 >= BM25 Recall@5
hybrid MRR >= BM25 MRR
citation correctness >= 0.95
temporal correctness >= 0.95
cross-scope leakage == 0
revoked/expired misuse == 0
fallback success == 1.00
```

P95 latency 和成本阈值必须结合真实部署环境确定，先记录 baseline，再设置硬门禁。

### 11.8 评测执行顺序

```text
schema validation
  -> ACL/status/validity deterministic checks
  -> lexical baseline
  -> vector diagnostic
  -> hybrid candidate
  -> answer replay
  -> citation checks
  -> security cases
  -> report
  -> gate decision
```

每份报告必须记录：

- git revision；
- dataset version；
- index snapshot；
- chunker version；
- embedding model version；
- retrieval config；
- per-case results；
- failed cases；
- degraded reasons；
- gate decision。

### 11.9 预计代码

- `src/project_lens/evaluation/retrieval_cases.py`
- `src/project_lens/evaluation/retrieval_runner.py`
- `src/project_lens/evaluation/answer_evaluator.py`
- `src/project_lens/evaluation/temporal_evaluator.py`
- `src/project_lens/evaluation/security_evaluator.py`
- `src/project_lens/evaluation/release_report.py`
- `tests/test_retrieval_evaluation.py`
- `tests/test_answer_evaluation.py`
- `tests/test_temporal_evaluation.py`
- `tests/test_security_evaluation.py`

Answer evaluation is input-driven: the runner accepts an optional JSON/JSONL
answer replay file and never fabricates answer quality from retrieval results.
Without that file, citation, abstention, and conflict metrics remain explicitly
unmeasured.

Temporal validity is evaluated separately from security. A stale result is a
retrieval-quality failure when it is relevant to an `as_of` case; it is not
automatically an ACL/security leak when it is an unrelated authorized
candidate. Revoked, expired, pending, rejected, cross-scope, and unauthorized
scorer inputs remain security blockers.

## 12. 阶段 8：灰度、运营与生产化

### 12.1 目标

在不影响现有 BM25 和 Evidence 问答的前提下，逐步启用 hybrid，并建立日常运营和回滚机制。

### 12.2 灰度顺序

```text
开发环境：fake embedding + hybrid diagnostic
  -> 内部测试项目：shadow retrieval
  -> 只读管理员：hybrid 可见
  -> 小范围真实群：hybrid 默认
  -> 全项目灰度
```

shadow retrieval 只记录 vector/hybrid 排名，不改变用户答案，用于比较：

- BM25 top-k；
- vector top-k；
- hybrid top-k；
- 结果差异；
- 延迟和成本；
- 是否召回 forbidden/stale 项目。

### 12.3 生产指标

```text
knowledge.search.count
knowledge.search.latency_ms
knowledge.search.candidate_count
knowledge.search.returned_count
knowledge.search.omitted_count
knowledge.search.degraded_count
knowledge.embedding.cache_hit
knowledge.embedding.failure
knowledge.index.stale_count
knowledge.index.failed_jobs
knowledge.citation.correctness
knowledge.security.denied
knowledge.security.leakage
knowledge.snapshot.changed
knowledge.eval.gate_failure
```

日志不得保存完整 query、正文、token、cookie、secret 或未经授权对象的标题/ID。

### 12.4 运营任务

每日：

- 处理 failed/stale index jobs；
- 统计 embedding 失败和降级；
- 检查未索引 SourceRecord；
- 检查新产生的 conflict/knowledge gap。

每周：

- 运行黄金集回归；
- 抽样人工检查引用；
- 检查 duplicate/high-similarity memory；
- 检查 Wiki source key 和 revision；
- 复核评测失败 case。

模型升级时：

1. 新模型建 parallel index；
2. 跑完整评测；
3. 通过门禁后切换；
4. 保留旧模型回滚窗口；
5. 记录 model version 和索引版本。

### 12.5 规模升级

初期使用 SQLite + FTS + 应用层 cosine。只有在规模或延迟证明必要时才迁移 pgvector/Qdrant，迁移必须保持：

- 相同的 tenant/project filter；
- 相同的 object/chunk/provenance；
- 相同的 model/content/chunker version；
- 相同的 fallback 行为；
- 相同的安全评测结果。

### 12.6 回滚

任何以下情况立即关闭 hybrid：

- 跨项目或跨租户泄漏；
- revoked/expired 内容进入答案；
- embedding provider 大面积失败；
- P95 延迟超出 SLA；
- citation correctness 明显下降；
- hybrid Recall@5 低于 lexical baseline。

回滚动作：

```text
knowledge retrieval mode -> lexical
vector index 保留但停止读取
SourceRecord/Evidence 继续正常同步
失败任务保留待重试
AgentRun/检索审计保留
```

## 13. 跨阶段验收清单

### 数据和索引

- [ ] 所有来源可生成稳定 document/chunk；
- [ ] source revision 不覆盖历史；
- [ ] chunk 可回到 Evidence/SourceRecord/Wiki source key；
- [ ] embedding model/chunker/content hash 可回放；
- [ ] index job 幂等且可重试；
- [ ] SQLite migration 可重复执行。

### 权限和事实

- [ ] embedding 前完成 tenant/project/actor/chat/status/validity 过滤；
- [ ] unauthorized candidate 不进入 scorer；
- [ ] revoked/expired/pending 不进入默认当前检索；
- [ ] Wiki/Episode 不可直接成为 confirmed fact；
- [ ] detail 和 send 前再次授权；
- [ ] source revision/hash 不一致会阻断或降级。

### 检索质量

- [ ] exact/BM25/vector/hybrid 可分别运行；
- [ ] hybrid 不损害错误码、路径和 traceback 查询；
- [ ] 中文同义问题有单独评测；
- [ ] Recall/MRR/nDCG 有 baseline 和 delta；
- [ ] vector rescue 和 false positive 有统计；
- [ ] 去重、MMR、source diversity 有测试。

### 回答和评测

- [ ] 每条 FACT 有 Evidence 引用；
- [ ] citation correctness 有确定性检查；
- [ ] 时间、冲突、拒答有评测；
- [ ] 跨租户/项目泄漏为零；
- [ ] embedding 失败安全降级；
- [ ] 评测报告可重现；
- [ ] 发布门禁可以阻断版本。

### 生产运营

- [ ] feature flag 可关闭 hybrid；
- [ ] index failure/stale/fallback 有指标；
- [ ] 模型升级支持 parallel build 和 rollback；
- [ ] 生产抽样进入人工复核；
- [ ] SQLite 到向量后端迁移不改变权限语义。

## 14. 推荐开发批次

为了控制变更风险，建议按以下批次提交：

```text
Batch 0: config + evaluation baseline
Batch 1: knowledge index contracts + SQLite schema
Batch 2: chunk adapters + provenance
Batch 3: embedding index + cache + jobs
Batch 4: Evidence hybrid retrieval
Batch 5: Memory/Episode/Wiki unified retrieval
Batch 6: detail + verifier + snapshot replay
Batch 7: evaluation runner + release gates
Batch 8: shadow mode + production rollout
```

每个批次必须包含：

1. 生产代码；
2. focused tests；
3. migration/rollback 说明；
4. retrieval trace 或审计字段；
5. `python -m compileall -q src`；
6. `git diff --check`；
7. 相关评测报告。

## 15. 最终完成定义

只有同时满足以下条件，才可称为“ProjectLens 知识库向量检索完成”：

1. SourceRecord、Evidence、ProjectMemory、Episode、Wiki 都能投影为稳定 chunk；
2. 所有候选先经过权限、项目、状态和时间过滤；
3. exact、BM25、vector、hybrid 可独立运行并对比；
4. embedding 失败 100% 安全降级；
5. 详情读取和最终发送前会二次校验；
6. Source revision、content hash、chunker/model version 可回放；
7. Hermes 只通过 ProjectLens 工具访问知识；
8. 至少 100 条匿名真实问题进入锁定黄金集；
9. Recall/MRR/nDCG、引用、时态、冲突、拒答、ACL 和成本都有报告；
10. 跨项目/跨租户泄漏、撤销/过期误用和未审批事实进入回答均为 0；
11. hybrid 通过灰度验证且可随时回滚到 lexical；
12. 运行指标、失败任务、降级原因和评测失败可定位。

## 16. 最终原则

```text
先固定知识对象，再构建索引。
先授权过滤，再执行 embedding。
先保留 BM25，再增加向量。
先统一来源和版本，再做重排。
先验证引用，再追求回答流畅。
先建立黄金集，再调权重和模型。
先 shadow，再灰度。
任何时候都能回滚到 lexical。
```
