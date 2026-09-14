# ProjectLens Obsidian + Hermes 知识库方案

**版本**：v1.0  
**更新时间**：2026-09-11  
**状态**：设计方案，待实施  
**适用范围**：ProjectLens、Hermes、团队共享 Obsidian Vault

## 1. 目标

本方案定义 ProjectLens 如何使用 Obsidian 构建团队共享知识库，并让 Hermes 成为知识库的管理 Agent。

目标不是再建设一套独立的 RAG 或 Wiki 系统，而是把现有的来源同步、Evidence、权限、知识缺口、Wiki 草稿和 Hermes 工具链连接起来：

```text
飞书 / GitHub / 本地资料
        -> ProjectLens 来源与 Evidence
        -> Hermes 整理、关联、检查、提案
        -> Obsidian 团队共享知识库
        -> ProjectLens 受权限约束的检索与回答
```

Obsidian 是团队的知识工作台，不是权限系统、事实裁决系统或正式记忆数据库。

## 2. 设计原则

### 2.1 一个事实来源，多个阅读界面

飞书文档、飞书多维表格、GitHub、会议资料和代码仍然是外部事实来源。ProjectLens 将它们标准化为 `SourceRecord` 和 `Evidence`，保留版本、更新时间、来源地址、权限和状态。

Obsidian 保存来源镜像和派生 Wiki 页面，但不能因为页面内容存在，就取代 Evidence。

### 2.2 Hermes 是知识管家，不是最终裁判

Hermes 可以摄取资料、生成摘要、建立链接、发现冲突、维护导航、提出页面更新和生成记忆提案。高影响事实仍需由 ProjectLens 的来源权威规则、Evidence 校验和人工审批决定。

### 2.3 权限先于导出和检索

任何导出到共享 Vault 或返回 Hermes 的内容都必须满足：

```text
ProjectSpace 权限
∩ 用户 / 角色权限
∩ 当前群聊可见范围
∩ Obsidian 导出策略
```

共享 Vault 不得包含 `.env`、密钥、数据库、私人消息、未授权日志或只对单一角色可见的资料。

### 2.4 原始资料不可覆盖，派生内容可重建

原始镜像不可由 Hermes 直接改写。Wiki 页面可以重新编译，人工补充进入 Inbox，正式知识通过审核后生效。

### 2.5 先可解释，再扩展检索

第一阶段使用 Markdown、YAML frontmatter、SourceRecord key 和 Obsidian wikilink 表达关系，不提前建设复杂 GraphRAG。Hermes 回答仍优先使用 ProjectLens 的授权 Evidence 检索。

## 3. 组件职责

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| 飞书文档 | 正式业务文档、会议和方案 | 项目知识全景导航 |
| 飞书多维表格 | 结构化状态、负责人、任务、知识缺口 | 长篇上下文沉淀 |
| GitHub | 代码、Issue、PR、Commit | 业务知识整理 |
| ProjectLens | 来源、版本、Evidence、权限、检索、冲突、知识缺口、记忆审批、审计 | 团队阅读界面 |
| Hermes | 同步编排、摘要、关联、Lint、提案、问答、维护 | 越权读取、直接决定事实、绕过审批写正式记忆 |
| Obsidian | 共享阅读、页面关联、变更审阅、人工补充、Git 历史 | 权限裁决、事实裁决、正式记忆写入 |
| Git | Vault 版本历史、审阅、回滚和协作合并 | 业务权限和 Evidence 校验 |

## 4. Obsidian 的职责

### 4.1 统一阅读入口

Obsidian 将项目总览、需求、决策、开发、测试、发布、风险、负责人、术语和知识缺口组织成一套可导航的团队知识。用户从 `Home.md` 开始，通过 `[[wikilinks]]` 在项目对象之间移动，不必知道资料原来位于哪一个外部系统。

### 4.2 展示关系和阅读路径

Obsidian 将 ProjectLens 的来源元数据、`related_sources`、authority scope 和状态转换成可读链接。这类关系优先用页面链接和 YAML 表达，不要求首版建设完整图数据库。

### 4.3 审阅 Hermes 产物

Hermes 生成的页面必须带状态：`proposed`、`review_required`、`confirmed`、`conflicted`、`superseded`、`expired`。Obsidian 是团队查看、比较和审阅这些状态的界面，正式状态仍由 ProjectLens 记录。

### 4.4 接收人工知识提案

团队在 `40-inbox/` 中补充尚未进入外部系统的知识。Inbox 内容默认是 `proposed`，不能直接成为正式事实或 `ProjectMemory`。Hermes 导入 Inbox 后，需要寻找 Evidence、识别冲突并生成审批提案。

### 4.5 提供版本审阅和回滚

Vault 作为独立 Git 仓库管理。Hermes 的批量更新应当可通过 Git diff 审阅，错误结果可以回滚，不影响 ProjectLens 中已经保存的来源和运行审计。

## 5. 推荐 Vault 结构

```text
ProjectLens-Vault/
├── 00-home/
│   ├── Home.md
│   ├── Project-Overview.md
│   ├── Reading-Guide.md
│   └── Current-Status.md
├── 10-sources/
│   ├── feishu-docs/
│   ├── feishu-bitable/
│   ├── meetings/
│   └── github/
├── 20-wiki/
│   ├── requirements/
│   ├── decisions/
│   ├── development/
│   ├── testing/
│   ├── releases/
│   ├── risks/
│   ├── owners/
│   ├── terms/
│   └── timelines/
├── 30-memory/
│   ├── confirmed/
│   ├── proposed/
│   └── expired/
├── 40-inbox/
│   ├── human-proposals/
│   └── hermes-proposals/
├── 50-review/
│   ├── conflicts/
│   ├── stale-pages/
│   └── pending-approval/
└── 90-system/
    ├── sync-status.md
    ├── sync-log.md
    ├── schema.md
    └── export-policy.md
```

`10-sources/` 保存外部资料的只读镜像；`20-wiki/` 保存可读的派生知识；`30-memory/` 展示已审批和待审批记忆；`40-inbox/` 接收人工和 Hermes 提案；`50-review/` 汇总冲突、过期页面和待审批变更；`90-system/` 保存同步状态、Schema、导出策略和日志。

## 6. 页面元数据契约

所有自动生成页面必须包含可机器读取的 frontmatter：

```yaml
---
tenant_id: demo
project_id: projectlens
page_type: requirements
status: review_required
derived: true
generated_by: hermes
generated_at: 2026-09-11T10:00:00+08:00
source_keys:
  - demo:projectlens:docx_projectlens_scope:v1.2
authority_scope:
  - requirement_scope
---
```

最低字段要求：`tenant_id`、`project_id`、`page_type`、`status`、`derived`、`generated_by`、`generated_at`、`source_keys`、`authority_scope`。没有 `source_keys` 的页面只能作为导航、提案或未知项，不能作为已确认事实依据。

## 7. 来源进入 Obsidian 的流程

### 7.1 飞书文档

```text
FeishuDocumentConnector
 -> SourceRecord
 -> EvidenceIndex
 -> 只读 Markdown 镜像
 -> Hermes 生成 Wiki 页面
```

例如：`10-sources/feishu-docs/projectlens-scope--v1.2.md`。页面需要保留 `doc_token`、URL、revision、更新时间、owner、status 和 source key。

### 7.2 飞书多维表格

多维表格不按每行生成一个 Markdown 页面，避免碎片化。推荐同时保留：

```text
10-sources/feishu-bitable/
├── projectlens_knowledge_gaps.current.json
└── projectlens_knowledge_gaps.current.md
```

JSON 保留完整字段、记录 ID 和机器处理所需的数据；Markdown 提供当前视图。Hermes 再生成 `Open-Risks.md`、`Owners.md`、`Knowledge-Gaps.md` 等可读页面。

### 7.3 GitHub 和代码

共享 Vault 默认导出 Issue、PR、Commit 和必要的代码摘要。代码全文是否导出必须由 ProjectSpace 和导出策略共同决定，不能因为本地仓库可读就自动进入团队 Vault。

## 8. Hermes 的知识管理职责

Hermes 通过 ProjectLens 受控工具完成以下工作：

1. **摄取**：发现新 revision、同步飞书/GitHub 来源、检查同步状态；
2. **整理**：生成项目概览、需求、决策、测试、风险和时间线；
3. **关联**：创建 `[[wikilinks]]`、补充 source keys、发现孤立页面；
4. **治理**：检查冲突、过期、缺少负责人、缺少测试证据和知识缺口；
5. **提案**：提出 Wiki 更新、稳定记忆和人工确认事项；
6. **解释**：在飞书回答问题时返回结论、Evidence、冲突、未知项和相关 Obsidian 页面。

Hermes 不得直接访问 SQLite、EvidenceIndex、任意本地路径或其他项目的 Vault 内容。

## 9. Hermes 工具面

建议逐步增加以下只读或提案工具：

```text
projectlens_sync_sources
projectlens_export_obsidian
projectlens_search_wiki
projectlens_read_wiki_page
projectlens_propose_wiki_update
projectlens_lint_wiki
projectlens_import_obsidian_inbox
```

工具必须沿用现有 ProjectLens Tool Gateway，服务端解析并校验 `tenant_id`、`project_id`、`chat_id`、`user_id` 和 EffectiveAccessScope。工具参数不能成为权限凭据。

Hermes 回答用户问题时，优先使用 `projectlens_search_context` 获取授权 Evidence；需要项目导航时再使用 Wiki 搜索和页面读取；重要事实必须返回 source key、版本或来源链接；没有 Evidence 支持的内容只能输出为推断、未知或待确认。

## 10. 人工回写和审批

Obsidian 的编辑采用“提案优先”模型：

```text
Obsidian Inbox
 -> Hermes 导入
 -> SourceRecord(status=proposed)
 -> Evidence / 冲突分析
 -> 有权限人员审批
 -> confirmed Wiki 或 ProjectMemory
```

人工编辑不能直接覆盖原始来源，也不能直接写入正式 `ProjectMemory`。稳定项目事实必须经过现有 `MemoryProposal` 和审批网关。

## 11. 权限和共享策略

共享 Vault 只包含 `team_shared` 范围内的内容。建议第一阶段导出已确认的项目文档、会议纪要、多维表格的项目状态和知识缺口、GitHub Issue/PR/Commit 摘要、项目 Wiki、冲突和待确认事项。

默认不导出 `.env`、API key、数据库和运行时秘密；私人聊天和未经授权的群聊内容；敏感日志和完整错误栈；只对单一角色可见的资料；没有明确 ProjectSpace 归属的文件。

导出策略应该写入 `90-system/export-policy.md`，并在每次同步时执行，而不是依赖人工记忆。

## 12. 一次完整流程

### 12.1 新资料同步

```text
Hermes 发现新资料
 -> ProjectLens connector 拉取
 -> 保存 SourceRecord revision
 -> 写入 EvidenceIndex
 -> 计算冲突和知识缺口
 -> 更新 Obsidian 来源镜像
 -> 编译 Wiki 草稿
 -> 低风险自动发布，高影响内容进入 Review
```

### 12.2 用户提问

```text
飞书问题
 -> Hermes 理解问题
 -> ProjectLens 解析身份和权限
 -> search_context 获取 Evidence
 -> 必要时读取 Wiki 导航
 -> 答案验证与引用检查
 -> 返回事实、推断、未知和来源
```

### 12.3 团队补充知识

```text
团队编辑 40-inbox/
 -> Hermes import
 -> 匹配现有 Evidence
 -> 标记 proposed / conflicted
 -> 人工审批
 -> 更新 Wiki 或 ProjectMemory
```

## 13. 实施阶段

### 阶段 1：只读 Obsidian 导出

新增 `ObsidianMarkdownPublisher`，复用现有 `WikiDraftCompiler` 和 `WikiDraftWorkflowService`，生成来源镜像、Wiki 页面、首页、知识缺口和 Review 页面。这一阶段不允许 Obsidian 内容回写 ProjectLens。

### 阶段 2：Hermes 查询 Wiki

增加 `projectlens_search_wiki` 和 `projectlens_read_wiki_page`。Wiki 只作为导航和派生上下文，Evidence 仍是事实回答的主要依据。

### 阶段 3：Hermes 管理同步和维护

增加 `projectlens_sync_sources`、`projectlens_export_obsidian` 和 `projectlens_lint_wiki`，使 Hermes 可以主动维护来源镜像、页面链接、状态和缺口。

### 阶段 4：Inbox 受控回写

增加 `projectlens_import_obsidian_inbox`，将人工内容导入为提案；接入冲突检查、MemoryProposal 和审批流。

### 阶段 5：团队共享运营

为 Vault 建立 Git 分支、Pull Request、变更审阅和发布约定；补充同步状态、失败重试、导出审计和权限变更处理。

## 14. 验收标准

### 知识正确性

- 每个自动生成页面都能定位到至少一个 source key；
- 页面明确区分 confirmed、proposed、conflicted、expired；
- 冲突来源不会被静默覆盖；
- 过期来源不会被标记为当前事实。

### 权限安全

- 越权资料不会进入共享 Vault；
- Hermes 不能通过 Wiki 工具绕过 ProjectLens ACL；
- 工具参数不能伪造 actor、project 或 tenant；
- `.env`、数据库和密钥不会被导出。

### 运行可靠性

- 同一来源 revision 重复同步是幂等的；
- 外部来源更新后可以重新生成对应 Wiki 页面；
- Wiki 发布失败不会破坏 Evidence 索引和群聊问答；
- Vault 变更可以通过 Git diff 审阅和回滚。

### 团队体验

- 新成员可以从 `Home.md` 找到推荐阅读路径；
- 需求、决策、任务、测试、风险和负责人之间存在可用链接；
- 知识缺口有独立页面，并能回到 ProjectLens 的来源和状态；
- 团队可以在 Inbox 提交补充，而不必直接修改正式事实页面。

## 15. 与现有项目的对应关系

本方案复用当前项目已有能力：`ProjectSpace`、`SourceRecordStore`、`EvidenceIndex`、`ContextEngine`、`WikiDraftCompiler`、`WikiDraftWorkflowService`、`ProjectMemory`、`MemoryProposal`、Hermes `projectlens_*` 工具、`AgentRun`、`LifecycleBus` 和事件存储。

首个实现重点不是新增数据库，而是新增一个遵守上述边界的 Obsidian Markdown Publisher、Vault schema 和 Hermes 受控工具适配层。

## 16. 最终决策

ProjectLens 采用：

```text
独立 Git Obsidian Vault
+ 飞书文档和多维表格进入原始镜像层
+ Hermes 负责整理、关联、检查和提案
+ ProjectLens 负责权限、Evidence、冲突、检索和审批
+ Obsidian 人工编辑进入 Inbox
+ 正式知识必须经过审核
+ 第一阶段不自动回写飞书
```

Obsidian 的职责可以概括为：

> **看、连、审、补。**

事实、权限、检索、审批和审计继续由 ProjectLens 负责。
