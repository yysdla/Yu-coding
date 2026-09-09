# ProjectLens 真实项目试点版开发文档

更新时间：2026-09-09

## 1. 开发目标

为一个真实项目协作群交付第一版 ProjectLens。ProjectLens 是以项目为上下文、以飞书协作群为工作现场的通用项目协作 Agent，帮助不同部门基于同一批授权资料理解项目、讨论问题和确认事实。

```text
真实资料只读同步
-> 统一来源、版本、权限和优先级
-> 生成飞书 LLM Wiki 派生页面
-> Hermes 在被 @ 时检索并回答
-> 发现冲突和知识缺口
-> 管理员可以补充证据并重新同步
```

第一版不做业务、研发、测试、管理者四套不同事实，也不以角色化表达作为核心能力。角色只用于权限控制。成功标准是让团队围绕同一个项目问题更快获得一份有来源、带状态、可继续讨论的共同上下文。

## 2. 范围与非目标

### 2.1 首批真实来源

- 飞书文档；
- 飞书多维表格；
- 已存储的会议纪要；
- GitHub Issue、Pull Request、Commit 和必要的代码只读内容。

### 2.2 首批群聊行为

- 仅在被 `@` 或明确提问时响应；
- 查询当前项目授权范围内的资料；
- 解释需求范围、已有约定、开发安排和测试安排；
- 给出当前最可信结论及依据；
- 展示冲突、过期、缺失和无法确认项；
- 记录知识缺口，供管理员补充证据。
- 不因用户角色不同而改变事实；角色只决定可访问资料范围。

### 2.3 非目标

- 不自动修改原始飞书资料或 GitHub；
- 不自动改变需求、进度、测试或发布状态；
- 不自动分派任务或代表团队作决策；
- 不把普通群聊永久写成正式事实；
- **不建设 GraphRAG**。资料关联使用结构化元数据、来源链接、版本和状态，不引入图数据库或图检索链路。
- 不建设四套角色化 Agent 或角色专属事实模型。

## 3. 总体架构

```text
Feishu / GitHub read-only connectors
             |
             v
     Source normalization layer
             |
             v
 Immutable source records + sync cursor + ACL
             |
             +--> EvidenceIndex / RAG retrieval
             |
             +--> LLM Wiki draft compiler
             |        |
             |        +--> Feishu Wiki pages
             |
             +--> authority resolver / conflict detector
             |
             v
       ProjectLens Tool Gateway
             |
             v
        Hermes @mention loop
             |
             v
  AnswerDraft -> Verifier -> Feishu reply
             |
             +--> KnowledgeGap queue
```

ProjectLens 继续负责 ProjectSpace、Evidence、ACL、检索、知识治理和验证；Hermes 负责群聊会话、模型调用和工具循环；Feishu adapter 负责消息和 Wiki API 适配，不直接访问内部索引。

### 3.1 通用回答原则

第一阶段采用一个通用调查和回答链路：

```text
用户问题
-> 当前项目与权限范围
-> 相关资料检索
-> 来源优先级、版本和时效判断
-> 统一事实、冲突和未知项
-> 带 Evidence 的回答
```

业务、产品、研发、测试和管理者使用同一套事实判断。用户问题本身决定本次需要查询需求、开发、测试或发布资料；用户角色不决定答案内容，也不生成不同版本的项目事实。

## 4. 现有代码复用与新增边界

### 4.1 复用

- `ProjectSpace` / `ProjectRegistry`：试点项目、群绑定和可读范围；角色仅用于权限解析；
- `context.connectors`：外部来源连接器抽象；
- `context.indexing`：资料标准化和 `Evidence` 生成；
- `EvidenceIndex`、`ContextEngine`：权限过滤、精确检索、BM25 和融合排序；
- `ProjectMemory`：仅保存经过审批的稳定事实；
- `KnowledgeGapReport`：承载缺失和冲突发现；
- `ProjectAgentToolService`：向 Hermes 暴露受限只读工具；
- `Verifier` / `EvidenceLedger`：保证回答事实可回溯；
- 现有 Feishu Hermes bridge：维持 `@` 触发和真实 `AgentRun` 审计。

### 4.2 新增模块建议

```text
src/project_lens/knowledge/
  models.py              # SourceRecord, WikiPage, KnowledgeGap
  authority.py           # 按事实类型解析来源优先级
  conflict.py            # 版本、状态和内容冲突检测
  wiki_compiler.py       # 由来源生成 Wiki 草稿
  wiki_publisher.py      # 草稿分级发布到飞书
  sync_service.py        # 增量同步、游标和幂等
  metadata.py            # 统一元数据补全与校验
```

如果现有目录已经有等价模块，应扩展现有边界，不重复创建第二套索引或记忆系统。

## 5. 统一资料模型

原始来源进入系统后转换为不可变 `SourceRecord`，再按内容块生成 `Evidence`：

```text
SourceRecord
- source_id
- source_type
- project_id
- title
- raw_uri
- revision
- observed_at
- effective_from / effective_to
- owner
- status
- topic
- authority_scope
- supersedes
- related_sources
- access_scope
- content_hash
- raw_content_ref
```

约束：

1. 同一 `source_id + revision` 幂等；
2. 新 revision 不覆盖旧 revision；
3. 原文和来源 URI 可追溯；
4. 所有 `Evidence` 继承项目、权限、来源和版本信息；
5. 删除或撤销采用失效标记，不物理抹除审计记录。

## 6. 资料类型与权威规则

优先级由事实类型决定：

| 事实类型 | 默认首选来源 | 次级来源 |
| --- | --- | --- |
| 需求范围/验收条件 | 正式需求文档、需求多维表 | 已归档会议纪要 |
| 需求状态/排期 | 项目计划表、需求表状态 | 会议纪要 |
| 开发进度 | GitHub Issue、PR、Merge | 开发讨论 |
| 测试状态 | 测试记录、验收结果 | 测试讨论 |
| 技术决策 | 已确认 ADR、正式技术方案 | 会议纪要 |
| 负责人 | 项目成员表、任务分派记录 | 会议纪要 |

解析评分由以下因素组成，而非只比较来源类型：

```text
authority_scope
+ status
+ revision
+ effective time
+ freshness
+ project / topic match
```

会议纪要可以产生 `proposed` 变化，但不能无条件覆盖正式来源。冲突时保留双方证据，并输出选择依据。

## 7. 同步流程

```text
定时任务或手动 sync
-> 读取 connector cursor
-> 拉取新增 / 变更来源
-> 校验项目和 ACL
-> 标准化 SourceRecord
-> 计算 content_hash
-> 幂等写入 source snapshot
-> 分块并写入 EvidenceIndex
-> 更新 Wiki draft
-> 生成 conflict / knowledge gap
-> 对低风险页面发布，对高影响结论进入 review
```

同步要求：

- 失败可重试，不重复生成同一版本；
- 单一来源失败不影响其他来源同步；
- 同步状态可查询：`last_success_at`、`cursor`、`indexed_count`、`failed_count`；
- 同步不得绕过 ProjectSpace 或 ACL；
- 真实连接器和本地 fixture 使用同一标准化接口。

## 8. LLM Wiki 编译与发布

### 8.1 页面结构

每个项目建立一个飞书 Wiki 根空间，按资料类型建立页面：

```text
项目 Wiki
├── 项目总览
├── 需求资料
├── 多维表资料
├── 会议纪要
├── 开发资料
├── 测试资料
├── 发布资料
└── 项目规则与术语
```

每个页面由固定区块组成：

```text
当前整理
来源依据
最近更新
关联资料
冲突与待确认
```

### 8.2 编译原则

- 原始资料不可被 Wiki 编译器改写；
- Wiki 内容必须携带来源 ID、版本和更新时间；
- 摘要、目录、关键词、时间线和冲突列表可自动发布；
- 需求范围、开发进度、测试结论、负责人和正式决策必须先生成草稿；
- 草稿状态和正式状态必须可区分；
- 发布失败不影响内部 Evidence 和检索结果。

### 8.3 发布状态

```text
draft -> review_required -> published
                         \-> rejected
```

低风险摘要可以由系统直接标记为 `published`；高影响事实必须保留 `review_required`，不能被 Hermes 当作确认事实使用。

## 9. Hermes 群聊回答流程

```text
收到 @ 问题
-> 解析 ProjectSpace / actor / chat scope
-> 识别问题主题和事实类型
-> 检索相关 Wiki 摘要和 Evidence
-> 调用 projectlens_search_context
-> 必要时读取多维表、GitHub 或会议来源详情
-> authority resolver 计算当前最可信候选
-> conflict detector 检查冲突和过期
-> 生成带 Evidence 引用的 AnswerDraft
-> Verifier 校验引用、权限和状态
-> 在群里回复
-> 写入知识缺口和审计事件
```

工具结果不得只返回一段拼接文本，应携带：

```text
source_id / evidence_id
source_type
revision
status
observed_at
authority_scope
summary
```

群聊标准回答结构为：

```text
结论
依据（来源、版本、更新时间）
当前范围 / 约定 / 安排
冲突与不确定性
知识缺口及其影响
```

同一个问题在访问范围相同的情况下，应得到相同的事实、引用、冲突和未知项。允许根据用户追问调整篇幅，但不得仅因角色不同改变结论或隐藏已经授权的重要不确定性。

## 10. 知识缺口模型

知识缺口分为：

- `missing`：没有任何授权资料；
- `conflict`：存在多个互相矛盾的来源；
- `stale`：只有过期或失效资料；
- `unconfirmed`：只有会议提议或 Agent 观察，没有正式确认来源。

每条缺口保存：

```text
gap_id
project_id
question
fact_type
evidence_ids
reason
impact
suggested_source_type
status
created_at / updated_at
```

缺口进入管理员队列，但不自动修改原始来源。补充新来源或确认状态后，重新触发 Wiki 编译和检索索引更新。

## 11. API 与工具契约

第一版建议提供：

```text
POST /api/v1/knowledge/sync
GET  /api/v1/knowledge/sync-status
GET  /api/v1/knowledge/wiki/draft
POST /api/v1/knowledge/wiki/publish
GET  /api/v1/knowledge/gaps
POST /api/v1/knowledge/gaps/{gap_id}/resolve
```

Hermes 继续只使用 `projectlens_*` 只读工具。新增工具优先考虑：

```text
projectlens_get_wiki_summary
projectlens_search_context
projectlens_get_source_detail
projectlens_list_knowledge_gaps
```

Wiki 发布和缺口解决属于管理操作，必须经过 ProjectLens API 的权限和审计，不开放给普通群聊 Agent 直接调用。

## 12. 安全与权限

- 同步时按 ProjectSpace 和来源 ACL 过滤；
- 检索前过滤，不能只在回答渲染时过滤；
- 飞书 Wiki 页面只能写入当前项目允许的目标空间；
- 群聊回答使用 `user scope ∩ chat scope`；
- 角色只决定访问范围，不决定资料权威性、事实版本或回答模板；
- 原始资料 URI、Wiki 页面和 Evidence 都记录租户、项目和访问范围；
- 任何跨项目、越权、敏感文件读取都 fail closed；
- 会议纪要中的敏感内容不得因为被摘要而扩大可见范围。

## 13. 测试与价值验收

### 13.1 单元和契约测试

- connector 标准化和 revision 幂等；
- ACL 过滤和跨项目隔离；
- 事实类型权威解析；
- 版本、状态、过期和冲突检测；
- Wiki 草稿结构和来源引用；
- 高影响内容不会自动发布；
- 知识缺口创建、解决和重算；
- Hermes 工具 envelope 和 Evidence 引用完整；
- 角色不会改变同一问题的事实、引用、冲突和未知项。

### 13.2 真实问题回放

从试点群历史中抽取一组匿名问题，至少覆盖：

- 需求范围；
- 会议约定；
- 开发是否开始；
- 测试是否安排；
- 文档冲突；
- 资料缺失。

每题标注正确结论、应引用来源、不可泄露内容和必须声明的未知项。

### 13.3 价值指标

- 有来源依据的事实比例；
- 需求范围回答准确率；
- 开发/测试安排判断准确率；
- 冲突识别率；
- 知识缺口漏报率；
- 越权读取和泄露数，必须为 0；
- 团队重复追问次数变化；
- 从提问到获得可继续讨论上下文的时间。

## 14. 实施阶段

### Phase 1：只读同步与内部索引

- 接入真实飞书文档、多维表格和 GitHub；
- 建立统一 SourceRecord、同步游标和 Evidence；
- 完成优先级、冲突和缺口检测；
- 用 fixture 完成回放测试。

### Phase 2：LLM Wiki 草稿与发布

- 创建项目 Wiki 根空间和资料类型页面；
- 生成带来源的类型摘要和详情页；
- 实现低风险自动发布、高影响审核；
- 提供同步状态和管理员缺口队列。

### Phase 3：Hermes 群聊试点

- 在真实项目群启用 `@` 触发；
- 回答需求范围、约定、开发和测试安排；
- 保存 AgentRun、Evidence、引用和知识缺口；
- 用真实问题回放评估质量。

### Phase 4：根据试点结果收敛

- 优先修复错误来源选择、过期误用和冲突漏报；
- 优化 Wiki 页面可读性和资料治理；
- 只有在实际协作指标改善后，才扩展新的来源或行动能力；
- 不因技术完整性引入 GraphRAG 或复杂角色体系。

## 15. 完成定义

第一版只有同时满足以下条件才算完成：

1. 真实来源可以增量、幂等、可审计地同步；
2. 原始资料不会被 LLM 覆盖；
3. Wiki 页面按资料类型可读，并保留来源、版本和状态；
4. Hermes 只能在被 @ 时通过受限只读工具回答；
5. 回答中的事实都能回溯到授权 Evidence；
6. 冲突、缺失、过期和未确认状态不会被伪装成确定事实；
7. 知识缺口可以交给管理员补证并重新计算；
8. 真实历史问题回放达到预先设定的质量门槛；
9. 权限泄露数为 0；
10. 团队成员确认项目范围和安排的重复沟通成本有可测量下降。
