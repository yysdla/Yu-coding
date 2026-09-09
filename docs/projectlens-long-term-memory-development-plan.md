# ProjectLens Agent 长期记忆体系开发方案

版本：v1.0  
日期：2026-09-02  
状态：阶段 A～E 核心链路已实现；阶段 F 离线评测能力已接入，生产数据集和门禁仍待完成  
适用范围：ProjectLens Kernel、Hermes Agent Runtime、Feishu 入口、AgentRun 历史层

## 1. 文档目的

本文档定义 ProjectLens Agent 的长期记忆体系，以及 Hermes 如何在不扩大权限边界和上下文长度的前提下使用这些记忆。

本方案借鉴两类思路：

- 借鉴 Codex 的渐进式披露：先给极短的记忆目录，再按需搜索条目，最后打开详情和历史。
- 借鉴 Claude-Mem 的事件记录：保存 Agent 读取、调用工具、验证、修改提案和失败尝试等过程，但这些过程记录只属于历史层，不自动成为正式项目事实。

ProjectLens 不直接复制 Hermes、Claude-Mem 或 Codex 的存储和权限模型。正式项目记忆必须继续服从 ProjectLens 的项目隔离、Evidence、ACL、审批、有效期和审计规则。

核心原则：

```text
摘要负责导航
模型负责选择查询
工具负责受控检索
ProjectLens 负责权限和治理
Evidence 负责事实可验证
人工审批负责长期写入
```

## 2. 要解决的问题

当前 Hermes 路径在调用前读取当前项目全部有效 `ProjectMemory`，再直接注入 Hermes 上下文。该方式在记忆数量增加后会产生三个问题：

1. 上下文长度随项目规模增长；
2. 无关记忆降低模型对当前问题的注意力；
3. 模型难以区分稳定项目事实、临时调查过程和历史审计记录。

目标不是让 Agent “记住一切”，而是让 Agent 能够：

- 找到和当前问题相关的已审批项目事实；
- 必要时追溯过去的 AgentRun、对话和工具过程；
- 保持 Evidence 可追溯；
- 不把未经验证的观察自动升级为长期事实；
- 在记忆规模扩大时保持有限、可预测的上下文成本。

## 3. 当前基线

当前已有以下能力：

- `ProjectMemory`、`MemoryProposal` 和审批网关；
- `ConversationSession` 的近期对话、滚动摘要和 task scratchpad；
- `AgentRun`、`AgentEvent` 和 SQLite 持久化；
- `BM25Retriever`、精确检索和结果融合能力；
- Hermes 的 `HermesProjectContext` 注入；
- Hermes 工具经过 ProjectLens Tool Gateway、ProjectSpace、RolePolicy 和 ChatVisibilityPolicy；
- Hermes 答案已经可以进入 Evidence 验证和 MemoryProposal 流程。

当前不足：

- `ProjectMemory` 没有独立的摘要目录和主题索引；
- Hermes 调用前仍倾向于按项目读取并注入全部有效记忆；
- 没有 `projectlens_search_project_memory` 等按需记忆工具；
- 没有长期记忆与 AgentRun 历史之间的统一检索接口；
- 工具事件已经形成独立的可检索 `MemoryObservation` / `Episode` 历史层；历史 retention 和 proposal-only consolidation 已具备，生产调度与审核队列仍需完善。
- 当前 Hermes 工具循环和正式 AgentRun 的统一接入仍需继续收口。

## 4. 目标架构

```text
Feishu message
  -> trusted identity / project / chat scope
  -> create or resume AgentRun
  -> inject L0 security context
  -> inject short MemorySummary
  -> Hermes reads summary and chooses a query
  -> projectlens_search_project_memory
       -> ACL and project filtering
       -> exact match + BM25 + optional vector retrieval
       -> fusion + deduplication + budget limit
  -> Hermes receives compact memory cards
  -> optional projectlens_get_memory_detail
  -> optional projectlens_search_project_history
  -> optional projectlens_get_run_detail / evidence detail
  -> verified ProjectAnswer
  -> MemoryProposal after human-governed policy
  -> persist AgentRun, AgentEvents, observations and session state
```

Hermes 不直接访问：

- SQLite；
- 向量数据库；
- `EvidenceIndex`；
- `GraphStore`；
- 任意本地文件系统；
- 其他项目或租户的记忆；
- 未经 Tool Gateway 的外部 API。

## 5. 记忆类型和职责边界

| 对象 | 含义 | 是否可直接作为事实 | 默认是否注入 |
| --- | --- | --- | --- |
| `MemorySummary` | 项目记忆主题、关键词和 ID 目录 | 否 | 是，极短 |
| `ProjectMemory` | 已审批、可复用的项目事实 | 是，但仍需 Evidence 引用 | 否，按需搜索 |
| `MemoryObservation` | Agent 工具调用或调查产生的过程观察 | 否 | 否 |
| `Episode` | 一次 AgentRun 的压缩过程摘要 | 否 | 否 |
| `ConversationSession` | 当前聊天的近期消息、摘要和 scratchpad | 否 | 当前会话按范围注入 |
| `AgentRun` | 一次完整任务运行 | 否 | 按需查历史 |
| `AgentEvent` | 原始运行和工具审计事件 | 否 | 默认不注入 |
| `Evidence` | 支撑事实的代码、文档、日志或同步记录 | 作为事实来源 | 按查询结果提供 |

关键规则：

```text
ProjectMemory = 稳定知识
MemoryObservation / Episode = 过程历史
AgentEvent = 审计原始记录
Evidence = 可验证来源
```

任何过程记录都不能因为“出现过一次”就自动变成 `ProjectMemory`。

## 6. 渐进式披露四层

这四层是 ProjectLens 的记忆读取层级，不等同于现有 `ContextPack` 的 L0-L5 上下文层。

### 6.1 第一层：安全上下文

由 ProjectLens 系统固定注入，不通过 Hermes 工具获取：

```text
tenant_id
project_id
chat_id
actor_id
真实角色
聊天类型
可读来源
可用工具
policy_version
allow_apply=False
```

这一层用于约束后续所有检索，模型不能修改这些字段。

### 6.2 第二层：`MemorySummary` 记忆目录

系统在 Hermes 调用前注入极短目录，只包含导航信息：

```text
项目：支付平台

主题：支付回调
关键词：Kafka、callback、retry、consumer lag
记忆数量：12
最近更新：2026-08-30

主题：服务负责人
关键词：owner、oncall、支付服务
记忆数量：6

主题：近期风险
关键词：lag、timeout、积压、重试
记忆数量：9
```

这一层不包含完整记忆正文，也不包含 Evidence 正文。它的作用是让 Hermes 知道“项目有什么知识可查”。

`MemorySummary` 可以由系统定期生成，也可以在记忆新增、替换或撤销后增量更新。摘要本身必须经过当前项目 ACL 过滤，不能暴露敏感主题名称或其他项目数据。

### 6.3 第三层：相关记忆卡片

Hermes 阅读摘要后，根据当前问题生成记忆搜索工具调用：

```json
{
  "name": "projectlens_search_project_memory",
  "arguments": {
    "query": "昨天支付回调失败 Kafka consumer lag",
    "memory_types": ["architecture_fact", "risk", "decision"],
    "limit": 5
  }
}
```

ProjectLens 在工具内部执行：

```text
项目过滤
-> actor/chat ACL 过滤
-> 已审批过滤
-> valid_to 过滤
-> 精确字段匹配
-> BM25 关键词召回
-> 可选向量召回
-> 结果融合
-> 主题去重 / 冲突标记
-> 字符预算截断
```

工具只返回短卡片：

```text
memory_id=m-309
type=risk
title=Kafka consumer lag 风险
snippet=Kafka consumer lag 增大时，支付回调可能延迟或失败。
evidence_ids=e-77
valid_from=2026-08-20
```

返回限制必须由服务端强制执行：

- 默认 5～8 条；
- 总长度 3,000～5,000 字符；
- 单条摘要最多 300～500 字符；
- 不返回完整 Evidence 正文；
- `limit` 参数不能突破服务端上限。

### 6.4 第四层：详情和历史

Hermes 只有在记忆卡片不足以回答时，才继续调用详情工具：

```text
projectlens_get_memory_detail
projectlens_get_evidence_detail
projectlens_search_project_history
projectlens_get_run_detail
```

详情读取规则：

- 通过 `memory_id`、`evidence_id` 或 `run_id` 精确定位；
- 每次最多打开少量详情；
- 详情仍然重新执行 ACL 校验；
- 历史工具默认返回摘要和引用，不返回无界的原始事件；
- 需要原文时必须再次按 ID 和权限读取。

这层对应“从目录进入具体条目，再进入具体历史记录”，而不是一开始扫描整个数据库。

## 7. 检索策略

### 7.1 检索候选集和模型上下文必须分离

数据库中可以有大量记忆，但模型看到的结果必须有硬上限：

```text
数据库记忆：100,000 条
检索候选：最多 Top 50 + Top 50
融合重排：Top 10
返回 Hermes：Top 5～8 短卡片
详情读取：最多 1～2 条
```

“检索全部候选”不等于“把全部候选放进上下文”。

### 7.2 混合检索

推荐采用：

```text
精确匹配 + BM25 + 向量检索 + 重排
```

精确匹配用于：

- `memory_id`、`run_id`、`evidence_id`；
- 服务名、接口名、错误码；
- 文件路径、commit SHA；
- 明确的标签和主题。

BM25 用于：

- 关键词相同或接近的自然语言问题；
- 中文、英文和代码标识混合查询；
- 可解释的词项匹配。

向量检索用于：

- 用户和记忆措辞不同但语义相近；
- 故障描述、决策描述和历史调查过程；
- `MemoryObservation`、`Episode` 和会话历史。

向量检索不能代替：

- 权限判断；
- 项目隔离；
- 审批状态判断；
- `valid_to` 失效判断；
- Evidence 事实验证。

### 7.3 结果融合和重排

第一版可以复用 ProjectLens 已有 BM25 和 RRF 思路：

```text
candidate_score =
    0.40 * lexical_score
  + 0.35 * vector_score
  + 0.10 * type_match_score
  + 0.10 * recency_score
  + 0.05 * evidence_score
```

若没有向量服务，`vector_score` 置零并重新归一化，不阻塞关键词检索。

重排必须加入：

- 相似记忆去重；
- 同一主题数量上限；
- 被替换记忆排除；
- 冲突事实显式标记；
- 过旧内容降权；
- Evidence 数量和来源质量加权。

最终输出使用 MMR 或等价多样性策略，避免 8 条卡片都表达同一个事实。

## 8. Hermes 工具契约

### 8.1 `projectlens_search_project_memory`

用途：按当前问题搜索当前项目的已审批长期记忆。

输入：

```json
{
  "query": "支付回调为什么失败",
  "memory_types": ["risk", "decision"],
  "service": "payment-callback-service",
  "limit": 5
}
```

服务端强制补充和校验：

```text
tenant_id
project_id
actor_id
chat_id
policy_version
effective_access_scope
```

输出：

```json
{
  "ok": true,
  "query": "支付回调为什么失败",
  "returned_count": 2,
  "omitted_count": 118,
  "memories": [
    {
      "memory_id": "m-309",
      "type": "risk",
      "title": "Kafka consumer lag 风险",
      "snippet": "Kafka consumer lag 增大时，支付回调可能延迟或失败。",
      "evidence_ids": ["e-77"]
    }
  ]
}
```

### 8.2 `projectlens_get_memory_detail`

用途：打开一条已授权记忆的详细信息。

输入：

```json
{
  "memory_id": "m-309"
}
```

输出可包括：

- 完整记忆正文；
- `memory_type`；
- `approved_by`；
- `valid_from` / `valid_to`；
- `proposal_id`；
- `evidence_ids`；
- 替换关系；
- 安全来源标签。

通过 ID 读取不需要向量检索，必须使用精确查找并重新执行权限检查。

### 8.3 `projectlens_search_project_history`

用途：搜索历史 `AgentRun`、会话、Episode 和 MemoryObservation。

输入：

```json
{
  "query": "支付积压 Kafka consumer lag",
  "from": "2026-08-01",
  "to": "2026-09-02",
  "limit": 5
}
```

输出只返回：

- `run_id`；
- 时间；
- 标题；
- 摘要；
- 工具名；
- Evidence ID；
- 状态；
- 是否还有可读取详情。

原始工具参数和敏感正文不应默认返回。

## 9. 自动事件采集和历史层

### 9.1 事件来源

以下事件进入历史层：

- `RUN_STARTED` / `RUN_COMPLETED` / `RUN_FAILED`；
- `TOOL_STARTED` / `TOOL_COMPLETED` / `TOOL_DENIED`；
- `MODEL_REQUESTED` / `MODEL_RESPONDED`；
- 证据检索、验证失败、审批请求；
- patch/test 提案和验证结果；
- 会话 turn 和 scratchpad 变化。

现有 `AgentEvent` 是原始审计记录，应继续作为事实来源，不直接暴露给模型。

### 9.2 `MemoryObservation`

每个重要工具完成事件可以异步规范化为观察：

```json
{
  "observation_id": "o-901",
  "tenant_id": "tenant-a",
  "project_id": "payment",
  "run_id": "run-1001",
  "trace_id": "trace-1001",
  "event_type": "tool_completed",
  "category": "discovery",
  "tool_name": "projectlens_search_context",
  "title": "发现支付回调消费者积压",
  "narrative": "搜索结果显示 payment-consumer 在 14:32 出现积压。",
  "evidence_ids": ["e-551"],
  "status": "success",
  "content_hash": "..."
}
```

观察生成要求：

- 脱敏；
- 限长；
- 保留 `run_id`、`tool_name`、`evidence_ids`；
- 去除 secret、完整 prompt 和无必要原文；
- 相同事件通过 `content_hash` 去重。

### 9.3 `Episode`

一个 `AgentRun` 可能产生很多工具事件，不应全部作为模型上下文。后台任务将它们压缩成 Episode：

```text
标题：支付回调积压排查
摘要：2026-09-01 14:32 payment-consumer 出现 Kafka lag。
已检查：consumer 指标、最近部署记录。
当前判断：可能与重启后的分区再平衡有关，根因尚未确认。
关联运行：run-801
关联证据：e-551、e-552
```

Episode 是历史导航材料，不是正式长期事实。

### 9.4 原始事件和搜索观察分离

```text
AgentEvent      = 完整审计，访问最严格
MemoryObservation = 脱敏后的可检索观察
Episode         = 一次运行的压缩摘要
```

审计需要完整性，检索需要简洁，模型上下文需要更简洁，三者不能共用一份无界文本。

## 10. 长期记忆写入治理

长期记忆写入继续使用现有流程：

```text
Hermes 结果
  -> ProjectAnswer
  -> Evidence 验证
  -> verified FACT
  -> MemoryProposal(pending)
  -> 人工审批
  -> ProjectMemory(approved)
```

禁止以下路径：

- Hermes 直接写 `ProjectMemory`；
- `MemoryObservation` 自动升级为长期事实；
- 会话 summary 自动升级为长期事实；
- 无 `evidence_ids` 的 FACT 进入提案；
- 展示层过滤代替生成前 ACL；
- 通过工具参数伪造 `tenant_id`、`role` 或 `decided_by`。

记忆更新规则：

- 重复事实不得重复创建活动记忆；
- 替换必须指定 `replaces_memory_id`；
- 旧记忆设置 `valid_to`，不物理删除；
- 审批、拒绝、过期和撤销都写入审计；
- 事实冲突不能静默覆盖，必须标记并要求明确替换。

## 11. 数据结构建议

### 11.1 `MemorySummaryEntry`

```text
project
topic
keywords
memory_ids
memory_types
service_tags
active_count
last_updated
visibility_class
```

### 11.2 `MemoryRecallResult`

```text
query
retrieval_mode
candidate_count
returned_count
omitted_count
core_summary_refs
cards
conflicts
warnings
budget_chars
```

### 11.3 `MemoryCard`

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

这些结构都不应携带未经裁剪的 Evidence 正文或审计 secret。

## 12. 实施阶段

### 阶段 A：摘要目录和卡片契约

目标：建立 `MemorySummary`、`MemoryCard` 和固定上下文预算。

任务：

- 增加摘要目录数据结构和生成器；
- 为 `ProjectMemory` 增加主题、关键词或可派生标签；
- 增加卡片渲染和长度预算；
- 让 Hermes 上下文区分 `memory_summary` 与完整记忆；
- 增加摘要 ACL 测试。

验收：

- 摘要不包含跨项目数据；
- 摘要不包含 Evidence 正文；
- 摘要长度有硬上限；
- 当前记忆数量增加不会线性增加初始上下文。

### 阶段 B：记忆搜索工具和 BM25 召回

目标：Hermes 可以根据摘要生成查询，ProjectLens 返回 Top-K 记忆卡片。

任务：

- 新增 `projectlens_search_project_memory`；
- 复用现有 BM25 和 RRF；
- 在检索前执行 project/actor/chat/approval/validity 过滤；
- 增加去重、冲突标记和字符预算；
- 将查询、命中 ID 和省略数量写入 AgentEvent 审计。

验收：

- Hermes 只能看到当前项目授权记忆；
- `limit` 不能突破服务端上限；
- 返回 5～8 条短卡片；
- 未审批和失效记忆不会出现；
- 结果可回放、可解释。

### 阶段 C：向量索引和混合召回

目标：提升中文同义表达和自然语言历史问题的召回准确度。

任务：

- 为 ProjectMemory、MemoryObservation 和 Episode 建立向量索引；
- 定义 embedding 版本和重建策略；
- 实现 BM25 + vector + RRF/加权融合；
- 增加离线 Recall@K、MRR、nDCG 评测；
- 向量索引只接受已经过项目和可见性分区的数据。

当前实现：

- `EmbeddingProvider`、OpenAI-compatible provider 和配置开关已实现；默认 provider 为 `none`，不会产生外部请求；
- ProjectMemory 与 Episode 使用 SQLite embedding cache，按项目、对象、内容哈希和模型版本隔离；
- 向量评分异常时自动降级为 BM25/关键词检索；
- ProjectMemory 使用 RRF、时间衰减、MMR 和冲突提示，且这些排序步骤都发生在授权候选集内。

验收：

- 关键词、服务名和错误码查询不被向量结果破坏；
- 同义中文问题的 Recall@5 提升；
- 向量服务不可用时自动降级为 BM25；
- 向量检索不改变 ACL 和审批结果。

### 阶段 D：详情和历史工具

目标：实现真正的按需深入。

任务：

- 新增 `projectlens_get_memory_detail`；
- 新增 `projectlens_get_evidence_detail`；
- 新增 `projectlens_search_project_history`；
- 新增 `projectlens_get_run_detail`；
- 限制详情数量、字符数和时间范围；
- 将历史搜索结果接入 Hermes 工具目录。

验收：

- Hermes 可以从卡片进入具体记忆；
- Hermes 可以从历史摘要进入 AgentRun；
- 详情读取再次执行 ACL；
- 默认不返回无界原始事件。

### 阶段 E：事件观察和 Episode

目标：让 Agent 能找回过去做过什么，同时不污染正式长期记忆。

任务：

- 将 `AgentEvent` 规范化为 `MemoryObservation`；
- 异步生成 Episode；
- 增加内容哈希、去重和保留期限；
- 建立历史层关键词和向量索引；
- 在 Feishu audit summary 中展示安全引用，不展示 secret。

当前实现：

- AgentEvent 可派生为脱敏的 `MemoryObservation`；
- 一个 `AgentRun` 对应一个稳定 `Episode`，并持久化到 SQLite；
- Episode 支持项目级 FTS 和可插拔语义评分；
- 新增 `HistoryRetentionPolicy` / `apply_history_retention`，按租户和项目分区清理过期 Episode 及其 Observation，不触碰 ProjectMemory。
- 历史工具默认只返回安全摘要、工具名、Evidence ID 和 run/trace 引用，不返回原始 prompt 或工具参数。

验收：

- 可以通过自然语言找到历史调查；
- 历史结果可追溯到 `run_id` 和 Evidence；
- Observation 不会自动写入 ProjectMemory；
- 原始审计与搜索摘要分离。

### 阶段 F：评测、调参和生产门禁

目标：用真实问题验证召回质量、成本和安全边界。

指标：

- Memory Recall@5 / Recall@10；
- History Recall@5；
- irrelevant memory rate；
- duplicate card rate；
- 平均记忆上下文字符数；
- 详情工具调用率；
- Evidence citation correctness；
- 跨项目泄漏数；
- 未授权记忆读取数；
 - 向量降级成功率。

离线评测入口：`project_lens.evaluation.memory_retrieval.evaluate_memory_retrieval`。调用者提供已经完成项目、租户、聊天可见性、审批和有效期过滤的候选记忆，评测模块只复用真实 `retrieve_memories()` 排序，不会绕过 ACL，也不会写入记忆。

指标定义：

- `Recall@K`：返回卡片中命中的相关记忆数除以该 case 标注的相关记忆数；
- `MRR`：第一条相关卡片排名的倒数，按 case 取平均；
- `nDCG@K`：按排名折损后的相关性得分，当前 case 使用二值相关性；
- `duplicate_card_rate`：卡片 snippet 去空白并大小写归一化后的重复比例；
- `average_card_chars`：卡片 snippet 字符数加固定元数据预算（180 字符）的平均值，用于估算上下文成本；
- `retrieval_mode`：每条 case 标记 `bm25` 或 `hybrid`，便于比较向量启用前后的效果；
- `conflict_count`：返回卡片中被检测为相互约束冲突的记忆对数量。
- 历史层 `History Recall@K` / `MRR`：通过 `evaluate_history_retrieval(store, cases, k=...)` 对已项目隔离的 Episode 搜索结果进行离线回放。

当前评测模块是离线 replay 基础设施，尚未内置 100 条匿名真实问题、Evidence citation correctness 和跨项目泄漏自动门禁；这些属于上线前必须补齐的验收工作。

发布前必须通过：

- 至少 100 条匿名项目问题回放；
- 跨租户、跨项目、群聊和失效记忆测试；
- 向量服务不可用的降级测试；
- 记忆替换、撤销和审批回放测试；
- Hermes 正式 `AgentRun`、工具事件和答案验证关联测试。

## 13. 端到端示例

用户问题：

```text
昨天支付回调为什么失败？
```

系统先注入：

```text
主题：支付回调
关键词：Kafka、callback、retry、consumer lag
相关记忆数量：12
```

Hermes 生成工具调用：

```json
{
  "name": "projectlens_search_project_memory",
  "arguments": {
    "query": "昨天支付回调失败 Kafka consumer lag",
    "limit": 5
  }
}
```

ProjectLens 返回：

```text
m-309：Kafka consumer lag 增大时，支付回调可能延迟或失败。
m-205：支付回调失败最多自动重试 3 次。
```

Hermes 判断还缺少昨天的实际情况，于是调用：

```text
projectlens_search_context("2026-09-01 payment callback failure")
```

如果需要确认风险记忆来源，再调用：

```text
projectlens_get_memory_detail("m-309")
```

最终答案必须经过：

```text
Evidence 收集
  -> AnswerDraft
  -> verify_answer_draft
  -> ProjectAnswer
  -> Feishu AudienceView
```

本次调查中产生的工具事件进入：

```text
AgentEvent
  -> MemoryObservation
  -> Episode
```

只有经验证的事实候选经过 `MemoryProposal` 和人工审批后，才进入 `ProjectMemory`。

## 14. 安全和失败策略

### 必须拒绝

- 摘要或工具参数指定其他租户/项目；
- 读取 pending、rejected、expired proposal；
- 读取 `valid_to` 已失效的记忆；
- 通过 `memory_id` 绕过项目范围；
- 向量索引返回未经过 ACL 分区的数据；
- Hermes 请求直接写入 ProjectMemory；
- 工具返回无法追溯来源的 FACT。

### 可以降级

- 向量服务不可用：降级到 BM25；
- 摘要目录不可用：只提供安全的空目录并允许 Hermes 使用实时证据工具；
- 记忆搜索无结果：返回明确的 `no authorized memory matched`；
- 历史观察未完成：仍保留原始 AgentEvent，不阻塞最终答案；
- 详情超预算：返回摘要和 `detail_truncated=true`。

降级不能放宽权限，也不能把未知内容伪装成事实。

## 15. 完成定义

只有满足以下条件，才可称为“ProjectLens Agent 长期记忆体系完成”：

- Hermes 初始上下文只包含安全上下文和短记忆目录；
- 详细 ProjectMemory 通过受控搜索工具按需召回；
- 搜索工具返回结果有 Top-K、字符预算和去重限制；
- 关键词和向量检索都在 ACL、项目和有效期过滤之后运行；
- 记忆详情和历史运行都可以按 ID 定向读取；
- AgentEvent、MemoryObservation、Episode 和 ProjectMemory 职责分离；
- 所有长期事实都有 Evidence ID；
- ProjectMemory 写入必须经过 MemoryProposal 和人工审批；
- Hermes 的记忆工具调用关联正式 AgentRun、trace_id 和审计事件；
- 向量服务故障时可以安全降级；
- 跨项目、跨租户、群聊和失效记忆测试通过；
- 上下文成本和召回质量有可量化指标。

## 16. 与当前 Hermes 的差异

当前：

```text
飞书消息
  -> 读取当前项目全部有效 ProjectMemory
  -> 直接注入 Hermes
  -> Hermes 调实时证据工具
```

目标：

```text
飞书消息
  -> 注入安全上下文和 MemorySummary
  -> Hermes 选择主题并生成搜索参数
  -> ProjectLens 混合检索长期记忆
  -> 返回少量记忆卡片
  -> Hermes 按需读取详情和历史
  -> 统一 Evidence 验证和 AgentRun 持久化
```

因此，这份方案不是简单增加一个向量数据库，而是改变长期记忆的读取协议：

```text
从“系统全量预加载”
变成“摘要导航 + 工具检索 + 按需深入”。
```

## 17. 首版实现记录（2026-09-02）

已落地阶段 A 的最小闭环：

- 新增 src/project_lens/context/memory_retrieval.py；
- 增加 MemorySummaryEntry、MemoryCard、MemoryRecallResult；
- Hermes 调用前可注入只含主题、关键词、数量和更新时间的 project_memory_directory；
- 新增 projectlens_search_project_memory 只读工具；
- 工具服务端强制最多 8 条卡片、4,000 字符预算、420 字符单条摘要；
- 检索候选必须来自当前项目的有效 ProjectMemory，并经过现有 ProjectSpace/聊天 scope 校验；
- 首版使用轻量 BM25 风格词项评分，向量评分通过 MemoryVectorScorer 接口预留；
- 工具返回结构化 result.memories，同时记录候选数、命中数、省略数、检索模式和预算；
- 新增 tests/test_memory_retrieval.py 覆盖目录不泄露正文、Top-K/预算和可插拔向量评分。

本轮已继续落地：

- `projectlens_get_memory_detail` 已支持精确 ID 读取、再次项目/聊天 scope 校验和有效期校验；
- `projectlens_search_project_history` / `projectlens_get_run_detail` 已接入工具目录、AgentRun 和审计 envelope；
- `MemorySummary` 已持久化缓存，避免每次请求扫描全部记忆正文；
- `MemoryObservation` / `Episode` 已从 AgentEvent 派生并持久化，原始工具参数不会进入历史检索结果；
- 新增 `propose_memory_from_episode`：只有带 Evidence 的非占位 Episode 摘要才能生成 pending `MemoryProposal`，不会自动写入 `ProjectMemory`；
- 新增 `consolidate_episodes`：对已授权 Episode 批量生成 pending proposals，按项目隔离并对相似摘要去重；结果仍需通过既有 `MemoryStore.create_proposal` 和人工审批流程；
- 新增 `submit_consolidated_episodes`：通过 `MemoryApprovalGateway` 持久化批量候选，并使用稳定 proposal ID 支持安全重试；已批准提案不会被重复任务重置；
- 历史 Episode 已提供可插拔 `HistoryVectorScorer`，并在授权项目分区内支持关键词与语义排序；
- SQLite runs 已增加 tenant/project 索引，历史查询优先在 SQL 层完成项目隔离。

仍待完成：

- embedding provider 接口、OpenAI-compatible provider、模型配置和安全降级路径已落地；真实环境仍需配置凭据并启用外部调用；
- embedding 向量缓存已落地：按 `kind + object_id + tenant/project + model_version + content_hash` 持久化到 SQLite；
- 相同查询和内容会复用缓存，内容哈希变化或模型版本变化会自动重新生成；支持按项目、类型、模型版本显式失效重建；
- 将 ProjectMemory、Episode、MemoryObservation 统一接入 RRF/加权混合排序；
- consolidation 已提供 proposal-only 单条和批量实现，并支持经审批网关持久化及幂等重试；批量流程按项目隔离、摘要相似度去重并统计跳过原因；冲突检测、旧记忆降权、MMR 多样性和历史 retention 已落地；生产级调度、跨批次合并和人工审核队列仍待完善；
- 完成 Recall@K、MRR、nDCG、跨项目泄漏和向量故障降级的离线/集成评测。

本轮评测能力已先落地：

- 新增 `src/project_lens/evaluation/memory_retrieval.py` 和对应单元测试；
- 支持 `Recall@K`、`MRR`、`nDCG@K`、重复卡片率、平均卡片字符数、检索模式和冲突数；
- 评测输入明确要求为已授权候选集，避免离线测试成为权限旁路；
- `evaluation` 包对重量级 Harness 模块采用惰性导入，使离线指标不依赖完整应用配置即可运行。

### 本轮 embedding 缓存运行规则

1. 只有已经通过 ProjectSpace、聊天 scope、审批和有效期过滤的候选才会进入 embedding 评分。
2. SQLite 缓存不保存原始查询或正文，只保存对象键、内容哈希、模型版本和向量。
3. 变更 `PROJECT_LENS_EMBEDDING_MODEL` 后，旧模型向量不会被误用；首次查询会按新版本重算。
4. 可通过 `SQLiteEmbeddingCache.invalidate(project=..., kind=..., model_version=...)` 清理指定分区，供后台重建任务调用。
5. embedding 服务不可用时，ProjectMemory 和历史 Episode 都自动降级到 BM25/关键词检索。

### 本轮检索质量增强

- 记忆卡片排序加入 RRF：分别计算关键词排名和向量排名，使用 `RRF_K=60` 融合，避免某一单一通道完全支配结果；
- 加入 30 天半衰期的时间分数，旧记忆自然降权，但只对已经有效、已审批的候选生效；
- 使用 MMR 选择卡片，在相关性和文本相似度之间做平衡，防止同一事实的重复表述占满 Top-K；
- 对同类型、内容高度相似但包含相反约束词（例如“必须/禁止”）的卡片生成 `conflicts` 标识；
- 冲突只作为 Hermes 的复核提示，不会自动选择一方、修改记忆或绕过 `MemoryProposal -> 人工审批` 流程；
- 工具返回新增 `conflicts` 和审计 `conflict_count`，仍受 8 条卡片与 4,000 字符预算限制。
