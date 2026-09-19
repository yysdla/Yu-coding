# ProjectLens 知识库向量检索与评测完整方案

**版本**：v1.0  
**日期**：2026-09-18  
**状态**：实施设计  
**适用范围**：SourceRecord、Evidence、ProjectMemory、MemoryObservation、Episode、Obsidian Wiki、Hermes 检索工具

## 1. 目标

当前 ProjectLens 已具备以下基础能力：

- Evidence 的项目隔离和 ACL 过滤；
- SourceRecord 的来源版本和不可变快照；
- Obsidian 派生 Wiki、Inbox proposal 和 Git 工作流；
- ProjectMemory / Episode 的 BM25、RRF、MMR 和可选 embedding scorer；
- SQLite embedding cache；
- Hermes 的搜索、详情、历史和答案验证链路；
- 基础 Recall@K、MRR、nDCG 离线评测。

当前缺口不是单纯“把 BM25 换成向量库”，而是：

1. SourceRecord/Evidence 主检索仍主要是 `exact + BM25`；
2. 向量能力只在 ProjectMemory/Episode 局部接入，没有统一覆盖整个知识库；
3. chunk、向量、版本、权限、索引状态和来源引用没有统一契约；
4. 评测已有指标，但缺少真实黄金集、答案引用正确性、时态/冲突/拒答/ACL 门禁；
5. 没有把 BM25、向量、重排、详情读取和最终回答质量拆开测量。

本方案的目标是建立一套统一的：

```text
来源同步
  -> 标准化文档对象
  -> 可追溯 chunk
  -> 授权候选集
  -> exact + BM25 + vector 混合召回
  -> 轻量重排
  -> Evidence / MemoryCard
  -> Hermes 按需读取
  -> Answer verifier
  -> 离线评测与生产门禁
```

## 2. 核心原则

### 2.1 向量是召回通道，不是事实源

向量相似度只能说明文本语义接近，不能决定：

- 是否属于当前 tenant/project；
- 当前 actor 和 chat 是否可读；
- 是否已经审批；
- 是否在有效时间内；
- 哪个来源版本更权威；
- 是否可以作为 confirmed fact。

### 2.2 ACL 必须先于 embedding

生产链路必须严格遵循：

```text
trusted actor
  -> ProjectSpace
  -> EffectiveAccessScope
  -> project / chat / source / status / validity 过滤
  -> exact / BM25 / vector
```

未授权候选不得进入 embedding scorer，也不得进入向量缓存。向量数据库或远程 embedding 服务不得成为权限旁路。

### 2.3 原始来源与派生知识分层

| 层 | 对象 | 是否事实源 | 默认检索用途 |
|---|---|---:|---|
| L5 | SourceRecord、Evidence | 是 | 当前事实、来源核验 |
| L3 | ProjectMemory | 经审批后是 | 稳定项目事实和约定 |
| L4 | Episode、MemoryObservation | 否 | 找历史调查和线索 |
| Wiki | Obsidian 20-wiki | 否，派生层 | 导航、整理、人工阅读 |
| Inbox | Obsidian proposal | 否，待审核 | 提案发现和 review |

Wiki、Episode、Observation 可以召回，但不能绕过 Evidence 和审批规则直接成为 confirmed fact。

### 2.4 默认混合检索，不删除 BM25

不同查询类型的优势不同：

- exact：错误码、文件路径、commit SHA、接口名、memory ID；
- BM25/FTS：代码标识符、中文关键词、版本号、专有名词；
- vector：同义表达、自然语言描述、跨措辞的历史问题；
- reranker：在小候选集内判断语义相关性、来源质量和问题意图。

因此生产默认是 `exact + lexical + vector + deterministic rerank`，而不是 vector-only。

## 3. 目标知识对象模型

### 3.1 KnowledgeDocument

统一检索对象不直接把所有业务对象塞进一个表，而是通过标准化投影进入索引：

```text
KnowledgeDocument
  id                       # 稳定对象 ID
  tenant_id
  project_id
  kind                     # evidence / source_record / memory / episode / wiki
  object_id
  source_system
  source_id
  revision
  title
  text
  metadata_json
  evidence_ids
  source_record_key
  fact_key
  subject
  memory_type
  status
  valid_from
  valid_to
  observed_at
  authority_scope
  access_scope
  visibility_scope
  content_hash
  chunk_index
  chunk_count
  chunk_version
  embedding_model
  embedding_dimensions
  index_status
  created_at
  updated_at
```

`KnowledgeDocument` 是索引投影，不取代 Evidence 或 ProjectMemory。正文必须能够通过 `object_id + content_hash + revision` 回到权威对象。

### 3.2 Chunk 契约

所有可向量化对象先经过统一 chunker：

```text
Chunk
  chunk_id
  document_id
  object_kind
  object_id
  tenant_id / project_id
  source_id / revision
  title_path
  text
  token_or_char_count
  overlap_from_previous
  content_hash
  chunker_version
  indexable
```

建议初始策略：

- 普通中文/英文文档：600～900 token，80～120 token overlap；
- 代码：按 module/class/function 分块，保留文件路径、symbol、行号；
- Bitable/task：一条记录一个 chunk，字段名和业务 ID 进入 metadata；
- 会议：按议题、决策、行动项切块；
- Wiki：按标题层级和段落切块，保留 source_keys；
- ProjectMemory：一条记忆一个主 chunk，不再二次切碎；
- Episode：标题 + 摘要一个 chunk，不能把完整审计事件向量化；
- 日志、指标和 trace：不进入普通知识库，使用时间窗工具或经过治理的 incident summary。

chunk 必须稳定。只要原文、chunker_version 和 metadata 不变，`content_hash` 和 `chunk_id` 应保持不变。

## 4. 统一索引架构

### 4.1 第一阶段存储选择

当前项目以 SQLite 为主，建议采用两步架构：

**本地/单机阶段**

```text
SQLite
  - knowledge_documents
  - knowledge_chunks
  - knowledge_embeddings
  - knowledge_index_jobs
  - FTS5 lexical index
```

向量可先存为 SQLite blob/JSON，并在应用层计算 cosine；知识规模较小时实现简单、可回放、易测试。

**生产规模阶段**

当满足以下任一条件再迁移到 pgvector/Qdrant：

- 单项目 chunk 超过 10 万；
- 多项目总 chunk 超过 100 万；
- P95 查询延迟超过目标；
- embedding 重建窗口无法满足同步 SLA；
- 需要高并发 ANN 检索。

迁移时保留相同的 `KnowledgeDocument`、metadata filter、content_hash、model_version 和 ACL 语义，不能让后端迁移改变权限逻辑。

### 4.2 不建议一开始拆多个向量库

不要为 Memory、Wiki、Feishu、GitHub 分别建设完全独立的向量数据库。推荐一个逻辑索引、按 `kind` 和 metadata 分区：

```text
knowledge_embeddings
  (tenant_id, project_id, kind, object_id, chunk_id, model_version, content_hash)
```

这样可以：

- 保持统一缓存和重建策略；
- 统一审计和评测；
- 统一项目隔离；
- 支持跨来源混合召回；
- 仍然可以按 kind、source_type、memory_type 做过滤。

### 4.3 索引状态机

```text
discovered
  -> normalized
  -> chunked
  -> embedding_pending
  -> embedded
  -> indexed
  -> stale / failed
```

失败任务必须可重试，不能影响 SourceRecord/Evidence 的可用性。向量索引失败时，lexical 检索仍然可用。

## 5. Embedding 策略

### 5.1 模型选择

模型选择不以“最大向量维度”为目标，而以以下指标综合选择：

- 中文同义问题 Recall@5；
- 中英文和代码混合查询；
- 代码路径、函数名和版本号不被语义泛化破坏；
- 成本、延迟和稳定性；
- 维度和存储体积；
- 是否支持本地/内网部署。

候选分三类：

1. OpenAI-compatible embedding provider：适合快速接入和统一云端配置；
2. 本地 multilingual embedding：适合敏感项目和离线评测；
3. 企业内部 embedding gateway：适合统一审计、限流和缓存。

项目必须固定一个 `embedding_model_version`，不能混用不同模型产生的向量。模型升级必须走离线对比和全量/增量重建。

### 5.2 向量缓存键

缓存键至少包含：

```text
tenant_id
project_id
kind
object_id
chunk_id
model_version
content_hash
chunker_version
```

缓存只保存向量和元数据，不保存完整原文或完整 query。query 向量可以按 hash 短期缓存，但必须设置 TTL，并避免把敏感查询原文写入日志。

### 5.3 更新和重建

以下变化触发重建：

- 文本内容变化；
- metadata 中影响检索的字段变化；
- chunker_version 变化；
- embedding_model_version 变化；
- 向量维度变化；
- 对象从 revoked/expired 恢复或重新进入索引。

以下变化不必重算向量，只需更新 metadata：

- 当前用户权限变化；
- chat visibility 变化；
- retrieval freshness；
- authority 排序配置；
- 审计状态。

## 6. 混合召回与重排

### 6.1 查询解析

查询解析输出结构化信号：

```text
query_text
exact_terms                 # error code, path, SHA, ID, endpoint
entity_terms                # service, module, owner, task
source_kinds
memory_types
time_range / as_of
question_intent             # fact / history / decision / incident / navigation
```

不能把自然语言 query 改写成单一关键词后再检索；原始 query 必须保留用于 embedding。

### 6.2 召回规模

默认：

```text
exact: 20
BM25/FTS: 100
vector: 100
union: <= 200
rerank: <= 50
final: <= 8
```

所有数字都是服务端上限，Hermes 参数不能突破。

### 6.3 融合

首版使用加权 RRF，保持和现有实现兼容：

```text
exact       0.30
lexical     0.30
semantic    0.25
metadata    0.10
freshness   0.05
```

注意：这些权重只作用于已经授权的候选。没有向量时重新归一化：

```text
exact       0.40
lexical     0.40
metadata    0.15
freshness   0.05
```

不要把 `semantic=0` 当作负分，也不能让向量服务异常导致全量结果被过滤。

### 6.4 确定性重排

首版不强依赖 LLM reranker，先用可解释的 deterministic reranker：

```text
final_score =
  0.35 * fused_rank
  0.15 * exact/entity match
  0.15 * source authority
  0.10 * freshness
  0.10 * evidence completeness
  0.10 * source revision quality
  0.05 * diversity
```

其中：

- authority 由 `FactType` 和 `authority_scope` 决定；
- evidence completeness 检查是否有 source key、revision、Evidence ID；
- source revision quality 优先当前有效 revision，但历史问题按 `as_of` 选择；
- diversity 防止同一文档相邻 chunk 占满 Top-K。

当离线评测证明 deterministic rerank 已达到瓶颈，再评估 cross-encoder 或 LLM reranker。reranker 也只能看到授权候选，且必须保留原始召回排名用于解释。

### 6.5 结果契约

```json
{
  "ok": true,
  "retrieval_mode": "hybrid",
  "candidate_count": 128,
  "returned_count": 5,
  "omitted_count": 23,
  "degraded_reason": null,
  "results": [
    {
      "object_id": "...",
      "chunk_id": "...",
      "kind": "evidence",
      "score": 0.87,
      "channels": ["exact", "bm25", "vector"],
      "source_keys": ["demo:payment:doc-1:r3"],
      "evidence_ids": ["..."],
      "snippet": "...",
      "detail_available": true
    }
  ],
  "conflicts": [],
  "warnings": []
}
```

Hermes 默认收到短 snippet 和引用，不收到整篇正文。需要事实确认时再调用 detail 工具。

## 7. SourceRecord/Evidence/Memory/Wiki 接入顺序

### Phase 1：统一索引契约

交付：

- `KnowledgeDocument`、`KnowledgeChunk`、`EmbeddingRecord`；
- 统一 chunker 和 stable content hash；
- SQLite 表、索引状态、失败重试；
- SourceRecord/Evidence/Memory/Episode/Wiki 的标准化 adapter；
- 编译、迁移、幂等测试。

验收：

- 同一对象重复同步不重复产生 chunk；
- source revision 变化保留历史并产生新索引对象；
- revoked/expired/pending 不进入默认索引；
- 每个 chunk 可以回到 source key、revision 或 evidence ID。

### Phase 2：Evidence 主检索向量化

交付：

- `SemanticEvidenceRetriever`；
- ContextEngine 接入 exact + BM25 + vector；
- embedding 仅对 ACL-filtered candidates 执行；
- retrieval trace 记录 channel、模型版本、降级原因；
- 向量故障安全降级测试。

验收：

- 关键词/错误码结果不被向量误排；
- 中文同义问题 Recall@5 提升；
- cross-project candidate 永不进入 scorer；
- provider timeout、invalid vector、维度错误均降级到 BM25。

### Phase 3：Memory/Episode/Wiki 统一检索

交付：

- ProjectMemory、Episode、MemoryObservation、Obsidian Wiki 接入同一索引协议；
- `search_project_memory` 和 `search_context` 使用统一融合器；
- Wiki 结果明确 `derived=true`；
- Episode 结果明确 `historical=true`；
- 结果按 kind/source/authority 分组，避免互相污染。

验收：

- 正式事实优先于历史观察；
- Wiki 不能替代 Evidence；
- Episode 不能作为 confirmed fact；
- Obsidian ACL 和 manifest 边界保持有效。

### Phase 4：详情、验证和快照

交付：

- search -> detail -> Evidence detail 的完整链路；
- Context Snapshot 记录 object/chunk/content hash；
- 发送前二次授权和 hash 校验；
- stale/revoked/conflicted 结果阻断或明确降级。

验收：

- 权限变化后旧结果不能静默发送；
- 内容 hash 变化返回 `CONTEXT_CHANGED`；
- 回放可重建实际使用的结果集合；
- 每条 confirmed claim 都能找到 Evidence。

### Phase 5：评测平台和灰度

交付：

- 黄金问题集；
- 召回回放 CLI；
- 答案/引用评测；
- 安全和降级测试；
- 基线比较、报告和发布门禁；
- 生产采样和人工复核流程。

验收：

- 至少 100 条匿名真实问题；
- BM25、vector、hybrid 三组基线；
- 质量和安全门禁通过；
- 失败可定位到 query、channel、source、模型版本或权限过滤阶段。

## 8. 评测体系

### 8.1 数据集分层

不要只维护一份 JSON。建议拆成：

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

每条 case 至少包含：

```json
{
  "id": "payment-owner-001",
  "tenant_id": "demo",
  "project_id": "payment",
  "chat_id": "chat-1",
  "query": "支付回调现在由谁负责？",
  "as_of": null,
  "expected_intent": "fact",
  "relevant_object_ids": ["memory-1", "source-12"],
  "relevant_source_keys": ["demo:payment:owner:r4"],
  "forbidden_object_ids": ["secret-9", "crm-2"],
  "expected_citations": ["evidence-1"],
  "expected_conflict": false,
  "expected_abstention": false,
  "difficulty": "medium",
  "tags": ["owner", "chinese", "hybrid"]
}
```

### 8.2 召回层指标

必须同时报告 overall 和分层结果：

- Recall@1、Recall@5、Recall@10；
- MRR；
- nDCG@5；
- HitRate@K；
- relevant-first-rank；
- irrelevant rate；
- duplicate chunk rate；
- source diversity；
- conflict precision/recall；
- stale result rate；
- average/P50/P95 latency；
- embedding cache hit rate；
- vector fallback success rate。

分层维度：

- 中文 / 英文 / 中英混合；
- 关键词明确 / 同义表达；
- 代码 / 文档 / Bitable / 会议；
- fact / incident / decision / history；
- simple / medium / hard / adversarial；
- BM25 miss / vector rescue；
- 当前事实 / 历史时间点 / 冲突。

### 8.3 答案层指标

检索命中不等于回答正确，答案评测单独做：

1. **Citation correctness**：引用是否真正支持对应 claim；
2. **Citation completeness**：可验证事实中有多少有引用；
3. **Factual accuracy**：答案是否与标注事实一致；
4. **Completeness**：是否覆盖问题要求的关键点；
5. **Abstention precision**：资料不足时是否正确说不知道；
6. **Conflict handling**：冲突时是否同时呈现冲突，而非擅自选边；
7. **Temporal correctness**：是否使用了问题日期有效的版本；
8. **Unsupported claim rate**：没有 Evidence 的事实比例；
9. **Answer latency / token cost**。

其中 citation correctness、ACL、过期/撤销误用不能只交给 LLM judge，必须先用确定性检查：

```text
claim evidence_id exists
evidence belongs to same tenant/project
evidence is readable by actor
evidence revision is valid
evidence content supports cited source
```

LLM judge 只用于 completeness、表达清晰度和复杂语义支持判断，并且要定期与人工样本校准。

### 8.4 安全层指标

以下指标必须为零：

- cross-tenant leakage；
- cross-project leakage；
- forbidden source returned；
- revoked/expired memory returned；
- pending proposal returned as active fact；
- unauthorized embedding scorer input；
- unauthorized detail read；
- snapshot hash mismatch 被静默接受；
- 未经审批的 ProjectMemory 写入。

### 8.5 成本和稳定性指标

记录：

- 每次查询候选数；
- embedding 请求数和 batch 大小；
- cache hit/miss；
- embedding latency；
- vector store latency；
- reranker latency；
- 总检索 P50/P95；
- 返回字符数；
- Hermes tool call 次数；
- 答案输入/输出 token；
- 降级次数和原因。

## 9. 基线与实验设计

每次改动必须在同一黄金集上比较：

```text
Baseline A: exact + BM25
Baseline B: vector only
Candidate C: exact + BM25 + vector + deterministic rerank
Candidate D: Candidate C + cross-encoder/LLM rerank（后续）
```

至少记录：

```text
quality_delta
latency_delta
cost_delta
security_delta
fallback_delta
```

不能因为 Recall 提升就接受延迟、成本或安全回退。推荐使用门禁：

```text
Recall@5 >= BM25 baseline
MRR >= BM25 baseline
citation correctness >= 0.95
cross-scope leakage == 0
revoked/expired misuse == 0
fallback success == 100%
P95 latency <= agreed SLA
```

对于“向量救回 BM25 miss”的价值，单独报告：

```text
BM25 miss cases rescued by vector
vector-only false positive rate
hybrid top-5 gain over BM25
```

## 10. 评测执行流程

```text
1. 固定代码、索引快照、embedding model 和配置版本
2. 运行 deterministic schema/ACL/validity checks
3. 运行 BM25 baseline
4. 运行 vector-only diagnostic
5. 运行 hybrid candidate
6. 计算 Recall/MRR/nDCG/latency/cost
7. 回放答案并检查 citation
8. 运行 security/adversarial cases
9. 生成 JSON + Markdown 报告
10. 按 gates.json 判定 pass/fail
```

所有报告必须包含：

- git revision；
- dataset version；
- index snapshot；
- chunker version；
- embedding model version；
- retrieval configuration；
- per-case result；
- failed cases；
- degradation reasons；
- gate decision。

## 11. 黄金集建设方法

### 11.1 来源

- 真实项目群问题去标识化；
- Hermes AgentRun 中高频问题；
- 现有测试和 fixture 的自然语言改写；
- 知识缺口和冲突记录；
- 管理员、产品、研发、测试各角色补充问题。

### 11.2 标注

每条问题由两人独立标注：

- relevant source/object；
- acceptable alternative source；
- forbidden source；
- expected answer facts；
- expected citation；
- expected abstention；
- expected time/version；
- expected conflict；
- difficulty。

分歧由第三人仲裁，记录标注版本。不能只标“唯一正确文档”，否则会错误惩罚多个同样有效的证据。

### 11.3 数据切分

```text
dev: 20%
validation: 20%
test: 60%
```

按项目、主题和时间切分，避免同一个事实的近重复问题同时出现在 dev/test。生产上线只看锁定的 test 集，调参只使用 dev/validation。

## 12. LLM Judge 使用边界

LLM judge 不是安全裁判，也不能覆盖确定性门禁。

推荐三层：

1. deterministic：schema、ACL、引用存在、时间窗口、状态；
2. reference-based judge：事实覆盖、冲突解释、摘要是否支持；
3. human review：低置信度、争议样本、真实生产抽样。

使用 LLM judge 时：

- generation model 与 judge model 尽量不同；
- 采用结构化 JSON 输出；
- 先要求列证据，再给分；
- 对 pairwise 比较交换 A/B 顺序；
- 记录 judge model、prompt version、confidence；
- 每 50～100 条抽样做人审校准；
- 追踪 judge 与人工的 Spearman/Kappa 或一致率。

## 13. 生产知识库运营

### 13.1 同步任务

每天或按 connector 增量执行：

```text
sync source
  -> normalize
  -> source revision compare
  -> chunk diff
  -> enqueue embedding
  -> update FTS/vector metadata
  -> lint
  -> export Obsidian
  -> update metrics
```

### 13.2 失败恢复

- connector 成功、embedding 失败：保留 SourceRecord/Evidence，标记向量 stale；
- embedding 成功、写索引失败：重试索引，不重取来源；
- 模型更换：旧 index 保留，后台构建新版本，完成后原子切换；
- 部分项目失败：不影响其他项目；
- 向量后端不可用：切换 lexical-only，并在 retrieval trace 标记 degraded；
- 权限收紧：不需要重算向量，只需要新请求重新过滤。

### 13.3 知识治理

每周检查：

- 无来源 chunk；
- 无 Evidence 引用的正式记忆；
- stale/revoked 向量；
- 冲突 fact_key；
- Wiki source key 缺失；
- 重复或高度相似记忆；
- 评测集新失败问题；
- citation correctness 下降。

## 14. 建议新增代码模块

```text
src/project_lens/knowledge/
  contracts.py
  chunking.py
  adapters.py
  index_store.py
  embedding_index.py
  retrieval_service.py
  reranker.py
  audit.py

src/project_lens/evaluation/
  retrieval_cases.py
  retrieval_runner.py
  answer_evaluator.py
  security_evaluator.py
  release_report.py
```

但第一轮不建议新建平行 knowledge kernel。优先把以下现有模块抽成共享接口：

- `context/chunking.py`
- `context/retrieval/*`
- `context/embeddings.py`
- `context/engine.py`
- `context/memory_retrieval.py`
- `application/memory_retrieval_service.py`
- `obsidian/repository.py`

只有当当前 `context` 模块边界无法承载 SourceRecord/Evidence/Memory/Wiki 统一协议时，才新增 `knowledge/` facade。

## 15. 完成定义

只有满足以下条件，才称为“知识库向量化完成”：

- SourceRecord、Evidence、ProjectMemory、Episode、Wiki 都能投影为可追溯 chunk；
- 所有候选在 exact/BM25/vector 前完成 tenant/project/actor/chat/status/validity 过滤；
- BM25、vector、hybrid 三种模式可独立运行并有结果对比；
- 向量模型、chunker、索引版本和 content hash 可回放；
- embedding 失败 100% 安全降级；
- 详情读取再次授权并校验 hash；
- Wiki/History 明确是派生/历史层；
- 至少 100 条匿名真实问题进入黄金集；
- Recall/MRR/nDCG、citation、时态、冲突、拒答、ACL 和成本指标均有报告；
- 跨项目/跨租户泄漏、撤销/过期误用、未经审批事实进入回答为 0；
- 发布门禁可以阻断低质量或不安全版本；
- Obsidian 和 Hermes 仍然通过 ProjectLens 的统一知识服务访问，不直接访问向量存储。

## 16. 推荐开发顺序

```text
先统一 chunk 和索引契约
  -> 再让 Evidence 主链路支持 vector
  -> 再统一 Memory/Episode/Wiki
  -> 再做详情与 Snapshot 回放
  -> 最后建立黄金集、报告和灰度门禁
```

最重要的工程判断是：向量化不是一次性替换检索算法，而是把“知识对象、来源版本、权限、时间、召回、回答和评测”收敛到同一条可回放链路。
