# ProjectLens 长期记忆架构设计

版本：v1.0  
日期：2026-09-15  
状态：架构设计稿  
适用范围：ProjectLens Kernel、Hermes Agent Runtime、Feishu 入口、Context Composer、Evidence 与评测体系

## 1. 文档定位

本文定义 ProjectLens 的长期记忆应该是什么、如何读写、如何失效、如何审计，以及如何逐步落地。

它不是某个第三方记忆框架的接入说明，也不是把所有历史保存起来的技术方案。ProjectLens 的长期记忆服务于一个更窄、也更重要的目标：

> 让不同角色在同一个项目问题上看到同一组有来源、带状态、受权限约束、可以回溯的共同上下文。

本文与现有文档的关系：

| 文档 | 责任 |
| --- | --- |
| `projectlens-product-document.md` | 产品边界和目标，冲突时优先级最高 |
| `projectlens-long-term-memory-development-plan.md` | 已实现能力、阶段计划和历史变更 |
| 本文 | 长期记忆的稳定架构、数据契约和治理原则 |
| `projectlens-context-composer-development-plan.md` | 一次运行如何选择、冻结和回放上下文 |
| `projectlens-obsidian-hermes-knowledge-base.md` | Obsidian/Wiki 导出与人工知识维护 |

## 2. 核心判断

ProjectLens 不应把长期记忆设计成“聊天记录数据库”，也不应一开始引入完整的图记忆平台。推荐采用：

```text
ProjectLens 治理内核
    + 分层记忆
    + 项目隔离
    + Evidence provenance
    + 时间版本化
    + 混合检索
    + 人工审批写入
    + 离线评测和审计
```

第三方系统可以借鉴机制，但不能替代以下边界：

- `ProjectSpace` 决定项目和租户隔离；
- `EffectiveAccessScope` 决定当前 actor 能看到什么；
- `Evidence` 决定事实是否可验证；
- `MemoryProposal` 和审批网关决定什么可以成为正式记忆；
- `valid_from` / `valid_to` 决定事实在什么时间有效；
- `AgentEvent` 决定运行过程如何回放和审计。

因此，长期记忆的正确抽象是：

> **带来源、带权限、可版本化、可撤销、可按时间查询的项目事实系统。**

## 3. 目标和非目标

### 3.1 目标

长期记忆应帮助 Agent：

1. 找到与当前问题相关的已审批项目事实；
2. 区分当前事实、历史观察、推断、提案、冲突和未知；
3. 在事实变化后保留旧版本并解释替换关系；
4. 在需要时追溯过去的 AgentRun、Episode 和工具过程；
5. 在不扩大权限的前提下，按需提供最小上下文；
6. 在资料不足时明确拒答并生成可处理的知识缺口；
7. 用可重复的评测集验证召回、引用、时间和安全质量。

### 3.2 非目标

长期记忆不负责：

- 代替原始文档、代码仓库、任务系统或监控系统；
- 把所有聊天内容自动升级为项目事实；
- 让 Hermes 直接访问 SQLite、向量索引或本地文件；
- 通过隐藏的全量历史注入提升回答“看起来完整”；
- 在没有人确认的情况下修改正式需求、任务、发布或代码状态；
- 首版建设 GraphRAG 或通用企业知识图谱。

## 4. 研究结论与设计启示

近年的长期记忆研究和工程系统可以归纳成几条对 ProjectLens 有用的结论：



### 4.2 时间和更新不是附加字段

Graphiti/Zep 一类时间知识系统的关键价值不是“用了图数据库”，而是保留事实的有效时间、记录时间、来源和替换关系。ProjectLens 可以借鉴这种时间模型，而不必引入完整 GraphRAG。

### 4.3 评测要覆盖更新、时间和拒答

LongMemEval 将长期记忆拆成信息抽取、多轮推理、知识更新、时间推理和 abstention 等能力。ProjectLens 的评测也不能只看 Recall@K，还必须测试过期事实、冲突事实、权限隔离和“当前资料不足”的正确拒答。

### 4.4 复杂存储不等于更好记忆

Mem0、向量数据库、知识图谱和文件系统各有适用场景。对 ProjectLens，最先决定质量的不是存储品牌，而是：候选是否先授权、事实是否有 Evidence、更新是否可解释、结果是否受预算限制。

### 4.5 设计选择

| 需求 | 选择 | 原因 |
| --- | --- | --- |
| 项目事实 | SQLite/关系模型 + FTS | 可审计、易迁移、与现有代码一致 |
| 语义召回 | 可选 embedding cache | 没有外部向量服务时仍可运行 |
| 时间推理 | `valid_from`、`valid_to`、`observed_at`、`recorded_at` | 不覆盖历史，支持回看 |
| 关系表达 | 结构化 ID、`fact_key`、`source_refs`、`supersedes` | 满足首版关联需求，避免 GraphRAG 复杂度 |
| 过程历史 | Episode + Observation + AgentEvent 分层 | 检索摘要与完整审计分开 |
| 正式写入 | MemoryProposal + 人工审批 | 防止模型自我授权事实 |

## 5. 总体架构

```text
飞书消息
  -> trusted identity / project / chat scope
  -> 创建或恢复 AgentRun
  -> 注入 L0 安全上下文和短 MemorySummary
  -> Hermes 判断是否需要搜索记忆
  -> Tool Gateway 做授权后的混合检索
  -> 返回有限 MemoryCard
  -> 必要时读取记忆、Evidence 或历史详情
  -> Verifier 校验引用和事实状态
  -> 生成 ProjectAnswer
  -> 持久化 AgentEvent、Observation、Episode
  -> 对可沉淀事实生成 MemoryProposal
```

职责边界：

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| Hermes | 意图理解、工具循环、回答表达 | 决定权限、审批事实、直接读存储 |
| ProjectLens Kernel | 项目隔离、权限、检索、验证、记忆治理 | 替换外部系统原始内容 |
| Evidence/SourceRecord | 来源版本、引用和可验证证据 | 直接决定模型回答 |
| Context Composer | 本次运行的选择、顺序、冻结和回放 | 删除长期记忆 |
| Feishu adapter | 消息入口和结果呈现 | 绕过 Service 访问 SQLite |

## 6. 记忆分层

### 6.1 L0：安全上下文

L0 由 ProjectLens 固定注入，不能由模型或客户端覆盖：

```text
tenant_id
project_id
actor_id
真实角色
chat_id
聊天类型
EffectiveAccessScope
可读来源
可用工具
policy_version
allow_apply
run_id / trace_id
```

L0 不是知识记忆，而是所有记忆读取和写入的安全锚点。

### 6.2 L1：工作记忆

仅服务当前 session 或 AgentRun：

- 当前用户问题；
- 最近相关对话轮次；
- `TaskScratchpad`；
- 当前调查计划；
- 尚未验证的临时判断；
- 最近一次工具调用的短结果。

L1 可以在当前运行中直接使用，但不能自动变成正式项目事实。

### 6.3 L2：项目状态记忆

这是低 Token 的当前状态入口，例如：

- 当前开放风险；
- 最近一次已验证运行摘要；
- 当前待决策项；
- 最近项目状态；
- 最近更新的负责人和时间线。

L2 的作用是告诉 Agent“项目现在大概处于什么状态”，而不是提供全部证据正文。它必须带来源、更新时间和新鲜度标记。

### 6.4 L3：正式长期记忆

`ProjectMemory` 只保存经过审批、可跨运行复用的稳定事实，例如：

- 架构事实；
- 业务规则；
- 技术决策；
- 负责人；
- Runbook；
- 风险；
- 团队约定；
- 经确认的复盘结论。

默认不把全部 L3 注入模型，而是提供摘要目录和按需搜索。

### 6.5 L4：历史记忆

L4 用于回答“之前做过什么”：

- `AgentRun`；
- `AgentEvent`；
- `MemoryObservation`；
- `Episode`；
- 历史会话摘要。

L4 是过程历史，不是正式事实。它可以帮助 Agent 找到线索，但最终事实仍需回到 Evidence 或经审批的 ProjectMemory。

### 6.6 L5：来源证据

L5 是事实的可验证基础：

- 飞书文档和会议纪要；
- 飞书多维表格；
- GitHub Issue、PR、commit；
- 发布记录；
- 测试结果；
- 日志、指标和 CI 输出；
- 其他已接入的外部状态。

L5 不由记忆系统覆盖。记忆系统只保存引用、摘要和状态。

## 7. 领域对象和数据契约

### 7.1 ProjectMemory

现有模型已经具备 `project`、`text`、`memory_type`、`evidence_ids`、`approved_by`、`valid_from`、`valid_to` 和 `proposal_id`。建议演进为以下逻辑字段：

```text
id
tenant_id / project_id
fact_key                 # 同一事实的稳定键
subject                  # 事实作用于哪个实体，例如服务名
text
memory_type
claim_type               # fact / inference / rule 等
evidence_ids
source_refs              # SourceRecord 等非 Evidence 引用
authority_scope          # 对哪类事实具有权威性
visibility_scope         # 角色、群聊或成员范围
valid_from               # 事实生效时间
valid_to                 # 事实失效时间，空表示当前有效
observed_at              # 来源中声明的时间
recorded_at              # ProjectLens 记录时间
approved_by
proposal_id
supersedes_memory_id
review_due_at
status                   # active / needs_review / revoked
content_hash
created_at / updated_at
```

其中：

- `confidence` 可以作为排序信号，但不能代替审批；
- `fact_key` 用于去重和版本链，不应由模型随意拼接；
- `authority_scope` 描述“对什么事实权威”，不是对整份来源打一个总分；
- `visibility_scope` 是额外约束，最终仍要与当前 `EffectiveAccessScope` 求交集；
- `status` 是派生状态，不能绕过 `valid_to` 和 Evidence 撤销检查。

### 7.2 MemoryProposal

`MemoryProposal` 是正式长期记忆的唯一入口。它至少包含：

```text
id
project
proposed_by
claim_text
claim_type
memory_type
evidence_ids
reason
status                  # pending / approved / rejected / expired
replaces_memory_id
allowed_approvers
decided_by / decided_at
rollback_plan
expires_at
created_at
```

提案规则：

1. 没有 `evidence_ids` 不得创建事实提案；
2. 新提案必须是 `pending`；
3. 替换必须显式声明 `replaces_memory_id`；
4. 已存在同一 `fact_key` 的活动记忆时，不能静默重复创建；
5. 审批者必须由服务端根据项目策略判定，不能信任请求体中的 `decided_by`；
6. 提案过期后只能重新提出，不能直接批准旧提案。

### 7.3 MemoryObservation

观察是从 AgentEvent 派生出的、可检索但不可直接作为事实的记录：

```text
id
project
run_id
observed_at
kind
text
tool_name
event_type
evidence_ids
source_event_payload_keys
content_hash
```

观察内容必须脱敏、限长，不含完整 prompt、token、cookie、密钥或无关原文，并保留运行和 Evidence 引用。

### 7.4 Episode

Episode 是一次 AgentRun 的有界摘要：

```text
id / run_id
project
title
summary
started_at / ended_at
status
tool_names
evidence_ids
observation_ids
content_hash
```

Episode 适合历史导航和相似检索，不代表当前事实。Episode 生成的 Proposal 仍必须经过 Evidence 校验和人工审批。

### 7.5 MemorySummary

摘要目录只用于导航，不携带正文：

```text
project
topic
keywords
memory_ids
memory_types
active_count
last_updated
visibility_class
```

摘要生成前必须已经完成项目和 ACL 过滤。摘要不得泄露其他项目的主题数量、当前 actor 不可见的标题或关键词、Evidence 正文和 pending proposal 的敏感内容。

### 7.6 MemoryCard

记忆搜索返回短卡片，而不是完整对象：

```text
memory_id
memory_type
title
snippet
evidence_ids
valid_from
relevance_score
retrieval_channels
detail_available
```

服务端硬限制建议：

- 单次最多 8 条；
- 默认总预算 4,000 字符；
- 单条摘要最多 420 字符；
- `limit` 参数不能突破服务端上限；
- 不在卡片中返回 Evidence 正文。

## 8. 长期记忆生命周期

### 8.1 写入状态机

```text
observed
  -> candidate
  -> pending proposal
  -> approved / rejected / expired
  -> active ProjectMemory
  -> needs_review / superseded / revoked
```

### 8.2 提取

提取可以来自：

- 经 Verifier 校验的答案事实；
- 用户明确确认的群聊结论；
- 已授权来源中的稳定事实；
- Episode 的候选摘要；
- 人工维护的 Obsidian Inbox。

提取结果必须先分类：

```text
confirmed fact
observed signal
inference
proposal
conflicted
unknown
```

只有 `confirmed fact` 或经过明确验证、且满足项目策略的候选，才可以创建 `MemoryProposal`。

### 8.3 审批

审批网关负责：

- 检查 actor 是否具有审批权限；
- 检查提案是否过期；
- 检查 Evidence 是否仍存在且可访问；
- 检查替换目标是否属于同一项目且仍活动；
- 检查是否存在未解决冲突；
- 写入不可变的审批审计事件。

### 8.4 替换

替换不是删除：

```text
旧记忆.valid_to = 新记忆.valid_from
新记忆.supersedes_memory_id = 旧记忆.id
```

这样可以回答当前有效事实、某个日期有效事实、替换链和审批记录。

### 8.5 失效和复核

以下情况应把记忆标记为 `needs_review` 或 `revoked`：

- 关联 Evidence 被撤销；
- 来源产生新 revision；
- `valid_to` 到期；
- 新提案与当前事实冲突；
- 到达 `review_due_at`；
- 多次回答反馈引用不正确；
- 长期没有任何有效来源支持。

失效默认采用“软失效”：不物理删除，而是在正常检索中排除，并保留审计和时间查询能力。

## 9. 读取协议：摘要导航 + 按需深入

### 9.1 初始上下文

Hermes 默认只得到：

```text
L0 安全上下文
当前问题
短 MemorySummary
必要的 L1 工作状态
```

不再默认把当前项目所有有效 `ProjectMemory` 全量注入。

### 9.2 记忆搜索

建议工具契约：

```json
{
  "name": "projectlens_search_project_memory",
  "arguments": {
    "query": "支付回调为什么失败",
    "memory_types": ["risk", "decision"],
    "service": "payment-callback-service",
    "limit": 5
  }
}
```

服务端强制补充 tenant、project、actor、chat、policy version 和 EffectiveAccessScope。返回候选数、命中数、省略数、检索模式、短卡片、冲突和警告。

### 9.3 详情和历史

按需提供：

```text
projectlens_get_memory_detail
projectlens_get_evidence_detail
projectlens_search_project_history
projectlens_get_run_detail
```

详情读取必须再次执行 ACL 和有效期校验。历史搜索默认返回 run ID、时间、标题、摘要、工具名、Evidence ID、状态和是否还有可读取详情。不得默认返回无界的原始事件、完整 prompt 或工具参数。

## 10. 混合检索设计

### 10.1 检索管线

```text
查询解析
  -> 项目 / actor / chat / approval / validity 过滤
  -> 精确匹配
  -> BM25 / FTS 候选
  -> 可选向量候选
  -> 时间、类型、实体、authority 加权
  -> RRF 或加权融合
  -> 冲突检测
  -> 去重与 MMR
  -> 字符预算截断
  -> MemoryCard
```

**授权过滤必须先于 embedding 评分。** 向量服务永远不能成为 ACL 的旁路。

### 10.2 召回通道

精确匹配适合 memory/run/evidence ID、服务名、接口名、错误码、文件路径、commit SHA、`fact_key`、标签和主题。

BM25/FTS 适合关键词明确的自然语言问题，以及中英文和代码标识混合查询。

向量检索适合用户措辞与记忆措辞不同、同义表达、故障描述和历史调查。它不能代替权限、审批、有效期或 Evidence 验证。

### 10.3 建议排序信号

```text
lexical_score     0.35
semantic_score    0.25
entity/type match 0.15
freshness         0.10
authority         0.10
evidence quality  0.05
```

权重只能影响已授权候选的排序。建议保留 `retrieval_channels`，使回答和审计可以解释结果来自关键词、向量还是两者。

### 10.4 时间衰减

- 当前有效且近期更新的事实优先；
- 过期事实从默认当前检索中排除；
- 用户明确询问历史日期时，使用时间窗口查询；
- 低新鲜度只能作为警告或排序信号，不能单独判定事实错误。

### 10.5 冲突处理

检索结果出现同一 `fact_key` 多个活动版本、相反约束、authority 不一致、时间窗口重叠或 Evidence 指向不同 revision 时生成 `conflicts`。

处理原则：

1. 不静默选择一方；
2. 优先展示当前有效且 authority 更高的候选；
3. 同时保留冲突 ID 和来源；
4. 要求 Verifier 或人工确认；
5. 必要时生成 KnowledgeGap。

## 11. 权限、隐私和审计

### 11.1 两次授权检查

```text
搜索阶段：
trusted identity -> ProjectSpace -> EffectiveAccessScope -> 候选过滤

详情/发送阶段：
重新解析 identity/scope -> 逐条校验 source ref、status、hash 和有效期
```

第二次检查用于处理用户被降权、群聊切换、项目绑定变更、Evidence 被撤销、Memory 过期和 Draft 停留期间内容变化。

### 11.2 不可信输入

以下字段只能由服务端推导：

```text
tenant_id
project_id
actor_id
role
chat_id
decided_by
allowed_tools
effective_access_scope
```

### 11.3 记录什么

建议记录 run、trace、snapshot、actor、tenant、project、chat、role、policy version、候选数、命中数、省略数、返回的对象 ID、retrieval mode、预算、冲突数、降级原因、校验结果和 renderer 版本。

默认不记录完整 prompt、未授权内容的标题/ID/正文、secret/token/cookie/凭据，以及无必要的文件全文和聊天全文副本。

### 11.4 向量缓存

向量缓存至少按以下键隔离：

```text
kind
object_id
tenant_id / project_id
model_version
content_hash
```

缓存不应保存原始查询和完整正文。embedding provider 不可用时，检索自动降级到 BM25/关键词，不能放宽权限或审批条件。

## 12. 记忆巩固与知识缺口

### 12.1 Consolidation 原则

历史层可以定期压缩和归并，但只能生成候选：

```text
AgentEvent
  -> MemoryObservation
  -> Episode
  -> 候选事实
  -> MemoryProposal
  -> Evidence 校验
  -> 人工审批
  -> ProjectMemory
```

禁止 Episode、session summary 自动升级为 ProjectMemory，禁止未经审核覆盖旧事实。

### 12.2 知识缺口

当搜索不到授权记忆、只有低 authority 或过期来源、来源冲突无法消解、事实没有 Evidence，或记忆因内容变化进入 `needs_review` 时，生成或累计 KnowledgeGap。

KnowledgeGap 至少包含：

```text
question
project
fact_type
missing_or_conflicting_sources
impact
suggested_source_type
detected_at
status
```

它不是错误日志，而是项目治理事项，应进入每日摘要和人工处理队列。

## 13. Context Composer 的关系

长期记忆和 Context Composer 的职责必须分开：

```text
长期记忆：哪些事实长期存在、如何验证、何时失效
Context Composer：本次运行选择哪些内容、按什么顺序、如何冻结
```

Composer 的规则：

- 默认只推荐少量相关 ProjectMemory；
- 取消一次 Snapshot 中的记忆不删除 ProjectMemory；
- Snapshot 冻结后，实际模型输入只能来自 materialized snapshot；
- 发送前重新检查 project、scope、status、validity 和 content hash；
- 预览和实际发送必须使用同一个 renderer；
- 内容变化、Evidence 撤销或权限变化时拒绝静默替换。

推荐上下文顺序：

```text
L0 安全锚点
L1 用户选择的会话历史
L2 session summary
L3 task state
L4 Evidence / external state
L5 approved project_memory
最后：当前问题
```

## 14. 工具边界

Hermes 可调用：

```text
projectlens_search_project_memory
projectlens_get_memory_detail
projectlens_search_project_history
projectlens_get_run_detail
projectlens_get_evidence_detail
projectlens_search_context
projectlens_list_knowledge_gaps
```

Hermes 不可直接调用 SQLite、向量数据库、EvidenceIndex、GraphStore、任意本地文件系统，以及跨项目/跨租户搜索；不能直接写入 ProjectMemory 或绕过审批更新和删除。

工具失败时可以降级到 BM25、空目录、摘要、明确的无结果提示或保留 AgentEvent，但不能扩大权限。

## 15. 评测体系

### 15.1 评测集结构

项目版黄金集至少覆盖：

| 能力 | 例子 |
| --- | --- |
| Fact retrieval | 找到已审批架构事实 |
| Multi-session reasoning | 综合多次会议和 AgentRun |
| Knowledge update | 新负责人替换旧负责人 |
| Temporal reasoning | 查询某个日期有效的状态 |
| Conflict handling | 标出需求文档与会议冲突 |
| Abstention | 授权资料不足时明确说不知道 |
| Citation validity | 每条事实回到有效 Evidence |
| ACL isolation | 不能泄露其他项目和私聊内容 |
| Memory pollution | Observation 不得冒充正式记忆 |

### 15.2 指标

检索指标：

- Recall@5 / Recall@10；
- MRR；
- nDCG@K；
- irrelevant memory rate；
- duplicate card rate；
- conflict detection precision/recall；
- stale fact rate；
- 平均返回字符数；
- P50/P95 检索延迟。

回答和安全指标：

- citation correctness；
- evidence coverage；
- abstention precision；
- unknown 漏报率；
- 过期事实误用率；
- 未审批记忆进入回答的比例；
- 跨 tenant/project 泄漏数，必须为 0；
- 预览与实际模型输入不一致数，必须为 0。

### 15.3 评测门禁

以下任一项失败，不允许把版本标为可上线：

- 跨项目或跨租户泄漏；
- 无 Evidence 的 FACT 被当作确认事实；
- 过期或撤销记忆进入默认回答；
- 权限变化后旧 Snapshot 静默继续发送；
- 向量故障导致权限过滤失效；
- 记忆更新覆盖旧版本且无法回放。

## 16. 可观测性和运营

建议建立三类看板：

### 使用情况

记忆搜索调用率、详情打开率、历史搜索调用率、Proposal 创建/审批/拒绝率、KnowledgeGap 新增和关闭率。

### 质量情况

Recall@K 趋势、引用正确率、冲突命中率、stale/needs_review 数量、低质量记忆反馈、平均注入字符数。

### 成本和容量

embedding 调用量和缓存命中率、每次 AgentRun 的检索和模型 token、索引大小、retention 删除量、详情读取 P95 延迟。

## 17. 分阶段实施路线

### Phase 0：契约冻结

确认记忆类型、状态和权限边界；固定 ProjectMemory、MemoryProposal、Episode 职责；为 renderer、工具和 AgentRun 建立黄金测试；写 ADR：不全量注入、不静默覆盖、不物理删除。

### Phase 1：收口读取协议

完成 MemorySummary 缓存和 ACL 过滤；Hermes 默认只注入安全上下文和目录；完成 memory search/detail；固定 Top-K、字符预算、去重、冲突和审计。

### Phase 2：补事实身份和时间

增加 `fact_key`、`subject`、`source_refs`、`observed_at`、`recorded_at`、`review_due_at`；实现按时间查询和替换链；将 Evidence 撤销和来源 revision 接入复核队列。

### Phase 3：完善历史和巩固

统一 AgentEvent、Observation、Episode 生成和 retention；完成 history search/detail；consolidation 只生成 Proposal，支持幂等和批量去重；将 KnowledgeGap 接入每日摘要。

### Phase 4：混合检索和评测

在授权候选集内接入 embedding；增加模型版本和内容哈希缓存；运行 BM25 + vector + RRF/MMR；建立黄金集、回放报告和发布门禁。

### Phase 5：Context Composer 集成

记忆候选进入 Draft/Snapshot；冻结和发送前二次 ACL；记录 source hash、renderer version 和 completeness warning；支持回看一次运行实际使用的记忆。

### Phase 6：按证据扩展关系能力

只有当真实问题反复要求多跳关系查询时，才增加关系索引。首选结构化关系表和来源链接，只有它们无法满足召回和解释要求时，才评估图数据库。

## 18. 当前代码映射

| 架构职责 | 当前代码/文档落点 |
| --- | --- |
| 记忆领域模型 | `src/project_lens/domain/memory.py` |
| 记忆持久化和治理 | `src/project_lens/context/memory_store.py` |
| 记忆摘要和卡片 | `src/project_lens/context/memory_retrieval.py` |
| 历史 Episode/Observation | `src/project_lens/context/history_store.py`、`history_memory.py` |
| embedding provider/cache | `src/project_lens/context/embeddings.py` |
| 记忆审批 | `src/project_lens/runtime/memory_approval_gateway.py` |
| 上下文选择和冻结 | `src/project_lens/workflow/context_pack.py`、Context Composer 相关模块 |
| 来源和权限 | `src/project_lens/context/source_records.py`、`authority.py`、`engine.py` |
| 知识缺口 | `src/project_lens/domain/knowledge_gap.py` 及对应 service/store |
| 检索评测 | `src/project_lens/evaluation/memory_retrieval.py` |
| 现有阶段记录 | `docs/projectlens-long-term-memory-development-plan.md` |

## 19. 验收清单

### 数据和治理

- [ ] 所有正式长期事实都有 Evidence 或明确的来源引用；
- [ ] ProjectMemory 写入只能经过 Proposal 和审批网关；
- [ ] 替换保留旧版本、有效期和审批审计；
- [ ] 过期、撤销和冲突事实不会默认进入回答；
- [ ] Observation、Episode 不会自动升级为正式事实。

### 检索和上下文

- [ ] 初始上下文不随项目记忆数量线性增长；
- [ ] 所有候选先经过 project、tenant、chat 和 actor scope 过滤；
- [ ] 返回结果有 Top-K、字符预算、去重和冲突标记；
- [ ] 详情读取再次检查 ACL、有效期和内容哈希；
- [ ] embedding 故障可以安全降级到 BM25。

### 时间和历史

- [ ] 可以查询某个日期有效的事实；
- [ ] 可以查看某条记忆被谁、何时、以什么提案替换；
- [ ] 可以通过自然语言找到历史 AgentRun；
- [ ] retention 不触碰正式 ProjectMemory 和必要审计记录。

### 评测和安全

- [ ] 有真实问题黄金集；
- [ ] 有 Recall/MRR/nDCG 和引用正确率报告；
- [ ] 有冲突、过期、拒答和更新样例；
- [ ] 跨项目、跨租户泄漏为零；
- [ ] Snapshot 预览与实际输入可回放且一致。

## 20. 最终设计原则

```text
摘要负责导航，不负责证明事实。
工具负责检索，ProjectLens 负责权限。
Evidence 负责可验证，审批负责长期写入。
时间字段负责解释变化，软失效负责保留历史。
Observation 和 Episode 负责过程记忆，不负责事实授权。
检索结果必须有边界，模型输入必须可回放。
复杂图结构只有在真实需求证明必要时才引入。
```

ProjectLens 的长期记忆不是让 Agent“记住更多”，而是让项目在持续变化中仍然拥有一份可验证、可解释、可治理的共同认知。

## 21. 研究参考

本文的设计启示来自以下方向，具体实现仍以 ProjectLens 的权限、Evidence 和产品边界为准：

- LongMemEval：长期记忆 Agent 的信息抽取、多轮推理、知识更新、时间推理和 abstention 评测方向；
- Letta Memory：核心记忆块与归档记忆的分层、按需访问思路；
- Zep / Graphiti：时间化事实、episode、来源和混合检索思路；
- Mem0：面向 Agent 的记忆提取、更新和多租户存储思路。

