# ProjectLens Obsidian + Hermes 知识库开发实施方案

**版本**：v1.0  
**日期**：2026-09-12  
**状态**：开发设计，待实施  
**关联架构方案**：[projectlens-obsidian-hermes-knowledge-base.md](projectlens-obsidian-hermes-knowledge-base.md)

## 1. 文档目的

本文档把 Obsidian + Hermes 团队知识库方案拆成可执行的研发阶段，明确每个阶段的代码模块、数据契约、API、Hermes 工具、权限要求、测试和验收条件。

本文档假设以下产品决策已经成立：

    独立 Git Obsidian Vault
    飞书文档和多维表格进入原始镜像层
    Hermes 管理同步、整理、关联、检查和提案
    ProjectLens 管理 Evidence、权限、冲突、检索和审批
    Obsidian 编辑进入 Inbox
    正式知识必须经过审核
    第一阶段不自动回写飞书

## 2. 现有代码基础

实施必须优先复用现有能力，不重新建设第二套知识库内核。

| 现有能力 | 代码入口 | 在本项目中的用途 |
| --- | --- | --- |
| ProjectSpace | src/project_lens/project_space/ | 项目、来源、成员、角色和聊天范围 |
| SourceRecord | src/project_lens/context/source_records.py | 版本化来源快照、状态、权威范围和来源 key |
| EvidenceIndex | src/project_lens/context/store.py | 可检索的项目证据 |
| ContextEngine | src/project_lens/context/engine.py | ACL 前置过滤、检索、权威解析、缺口检测 |
| ConnectorSyncService | src/project_lens/application/connector_sync.py | 增量同步、游标、幂等和新鲜度 |
| WikiDraftCompiler | src/project_lens/application/wiki_compiler.py | 按资料类型生成 Wiki 草稿 |
| WikiDraftWorkflowService | src/project_lens/application/wiki_compiler.py | 草稿审核、发布状态和 publisher 调用 |
| ProjectAgentToolService | src/project_lens/application/project_agent_tools.py | Hermes 受控只读/提案工具门面 |
| MemoryProposal | src/project_lens/domain/memory.py | 正式 ProjectMemory 前的审批提案 |
| AgentRun / LifecycleBus | src/project_lens/application/run_service.py | 运行和变更审计 |
| SQLiteDatabase | src/project_lens/persistence/sqlite.py | 当前运行时的持久化基础 |

现有 Feishu Wiki publisher 仅是外部飞书发布适配器，不能直接当作 Obsidian publisher 使用。Obsidian 需要独立的本地文件 publisher、manifest 和安全扫描。

## 3. 总体目标架构

    外部资料：飞书文档 / 多维表格 / 会议 / GitHub / 本地资料
        -> ProjectLens connectors
        -> SourceRecord + Evidence + ACL + sync cursor
        -> ContextEngine / authority / knowledge gaps
        -> Hermes knowledge operations
        -> Obsidian Vault
        -> Hermes 先查 Evidence，必要时再查 Wiki

## 4. 跨阶段不变量

1. 项目隔离：所有读写绑定 tenant_id 和 project_id。
2. 权限前置：导出、搜索、读取和导入都由服务端根据 EffectiveAccessScope 校验，不能相信 Hermes 传入的身份字段。
3. 来源可追溯：自动生成页面必须保留 source_keys、revision、更新时间和原始 URI。
4. 原始不可覆盖：10-sources 只由受控同步任务写入；Hermes 不能把 Wiki 摘要写回原始镜像。
5. 派生内容可重建：20-wiki、50-review 和首页可由 SourceRecord 重新编译。
6. 提案不等于事实：Inbox、Wiki proposal 和 MemoryProposal 都不能直接升级为 confirmed 或 ProjectMemory。
7. Evidence 优先：Hermes 回答重要事实必须优先调用 ProjectLens Evidence 工具，Wiki 仅作导航和整理层。
8. 失败隔离：Obsidian 导出或 Git 操作失败不能破坏 SourceRecord、EvidenceIndex、Hermes 问答和正式记忆。
9. 可审计：同步、导出、Lint、提案、审批和导入都记录操作人、项目、结果和引用。
10. 默认只读：自动化写入只允许写入 Vault 的受控目录，不能获得任意文件系统写权限。

## 5. 阶段 0：基础准备和契约冻结

### 5.1 目标

在实现文件导出之前，冻结 Vault 路径、frontmatter、状态和权限策略，避免后续产生不可迁移的 Markdown 格式。

### 5.2 代码和配置改动

新增目录：

    src/project_lens/obsidian/
        __init__.py
        models.py
        paths.py
        frontmatter.py
        safety.py
        errors.py

建议增加配置：

    PROJECT_LENS_OBSIDIAN_ENABLED=false
    PROJECT_LENS_OBSIDIAN_VAULT_ROOT=
    PROJECT_LENS_OBSIDIAN_PROJECT_SUBDIR=projectlens
    PROJECT_LENS_OBSIDIAN_EXPORT_MODE=reviewed
    PROJECT_LENS_OBSIDIAN_MAX_FILE_BYTES=1048576
    PROJECT_LENS_OBSIDIAN_GIT_ENABLED=false

Vault 根目录不写死在代码中。服务启动时使用 Path.resolve，并验证它位于部署允许的根目录内。

### 5.3 数据契约

VaultConfig 至少包含：

    root: Path
    tenant_id: str
    project_id: str
    source_dir: str = "10-sources"
    wiki_dir: str = "20-wiki"
    memory_dir: str = "30-memory"
    inbox_dir: str = "40-inbox"
    review_dir: str = "50-review"
    system_dir: str = "90-system"

VaultManifest 保存 schema version、tenant/project、last export id、最近成功时间、导出的 source revision keys、页面 hashes 和失败安全扫描结果。建议放在 90-system/manifest.json。

### 5.4 测试和完成标准

- Vault 路径不能逃逸出配置根目录。
- 不允许写入 .env、.git、数据库和 Vault 外部路径。
- frontmatter 可以稳定序列化和读取。
- schema version 有明确升级策略。
- 路径穿越、敏感文件和超大文件测试通过。

## 6. 阶段 1：只读 Obsidian 导出

### 6.1 目标

把现有 SourceRecord、Wiki 草稿和知识缺口导出成团队可打开的 Obsidian Vault。此阶段不读取 Inbox，也不回写 ProjectLens。

### 6.2 新增模块和接口

    src/project_lens/obsidian/
        publisher.py
        source_exporter.py
        wiki_exporter.py
        navigation.py
        review_exporter.py
        manifest_store.py

核心接口：

    class ObsidianPublisher(WikiPublisher):
        def publish(self, draft: WikiPageDraft) -> str: ...

    class ObsidianExportService:
        def export_project(
            self,
            *,
            project: ProjectRef,
            access: AccessContext,
            mode: str = "reviewed",
        ) -> ObsidianExportResult: ...

ObsidianExportService 不直接访问外部系统，只接收经过 ProjectLens 过滤的 SourceRecord、WikiPageDraft、KnowledgeGapReport 和项目元数据。

### 6.3 导出路径规则

    10-sources/feishu-docs/{safe-slug}--{revision}.md
    10-sources/feishu-bitable/{connector}.current.json
    10-sources/feishu-bitable/{connector}.current.md
    10-sources/meetings/{safe-slug}--{revision}.md
    10-sources/github/{kind}-{safe-id}--{revision}.md
    20-wiki/{page-type}/{safe-slug}.md
    50-review/conflicts/{safe-id}.md
    50-review/stale-pages/{safe-id}.md
    50-review/pending-approval/{safe-id}.md

文件名只允许字母、数字、中文、空格、连字符、下划线和点，不能使用来源提供的任意路径。

### 6.4 来源镜像格式

每份来源镜像必须保存：

    tenant_id
    project_id
    source_type
    source_id
    revision
    status
    observed_at
    raw_uri
    access_scope
    source_key
    immutable: true

正文保留来源内容。同步更新生成新 revision 文件或明确的 current pointer，不覆盖历史 revision。

### 6.5 Wiki 页面格式

Wiki 页面必须包含当前整理结论、来源依据、最近更新、关联资料、冲突和待确认、source_keys、页面状态和生成时间。现有 WikiDraftCompiler 的 overview、requirements、bitable、minutes、development、testing、release、rules 页面类型可以先原样导出；对象化页面在后续阶段增加。

### 6.6 原子写入和幂等

1. 计算目标相对路径。
2. 生成完整内容和 content hash。
3. 写入同目录临时文件。
4. 使用 replace 原子替换目标文件。
5. 更新 manifest。
6. 写入 90-system/sync-log.md 或结构化事件。

同一 source_key + content_hash 重复导出不得产生新变更。发布失败时不得更新 manifest 成功状态。

### 6.7 API

新增建议接口：

    POST /api/v1/projects/obsidian/export

请求至少包含 project、user_id、compile、include_sources 和 include_review。生产路径不能只相信请求中的 permissions，应复用现有 ProjectRuntimeContextResolver。

### 6.8 测试

新增 tests/test_obsidian_export.py，覆盖：

- 目录初始化和固定结构；
- SourceRecord revision 保留；
- source key 和 frontmatter 完整；
- 重复导出幂等；
- 文件名安全化；
- 路径穿越被拒绝；
- .env、.db、.git 被拒绝；
- ACL 过滤后才导出；
- 冲突来源生成 review 页面；
- publisher 失败不更新 manifest；
- Wiki draft status 正确映射。

### 6.9 完成标准

使用当前 projectlens fixture 导出后，团队可以在 Obsidian 中打开 Home.md，跳转到需求、会议、开发、测试和知识缺口页面；每个自动生成页面都能回到 SourceRecord key。

## 7. 阶段 2：Hermes 查询 Obsidian Wiki

### 7.1 目标

让 Hermes 查询 Wiki 导航和整理结果，但不绕过 ProjectLens ACL 和 Evidence 事实链。

### 7.2 新增模块

    src/project_lens/obsidian/repository.py
    src/project_lens/obsidian/search.py
    src/project_lens/application/obsidian_service.py

ObsidianRepository 只允许读取 manifest 登记的页面，并返回 path、title、page_type、status、snippet、source_keys 和 generated_at。不能把任意文件系统搜索暴露给 Hermes。

### 7.3 搜索策略

第一版使用确定性的本地搜索：

1. title 精确匹配；
2. source key、page type、status 过滤；
3. 标题和正文 token 匹配；
4. 返回最多 8 个摘要；
5. 单页正文读取有字符上限；
6. 不批量注入全文。

Wiki 搜索不是 ContextEngine.search 的替代品。事实回答仍先走 projectlens_search_context。

### 7.4 Hermes 工具契约

新增：

    projectlens_search_wiki
    projectlens_read_wiki_page

search_wiki 参数包括 query、page_types、statuses、limit。read_wiki_page 只接受 Vault manifest 中登记的项目相对路径。

返回必须包含 derived=true、source_keys、status、project、truncated 和 warnings。

### 7.5 Prompt 规则

Hermes profile 增加以下规则：

    Obsidian Wiki 是 ProjectLens 的派生整理层，不是独立事实源。
    使用 Wiki 导航后，重要事实必须用 ProjectLens Evidence 工具核验。
    proposed、conflicted、expired 页面不得直接表述为已确认事实。

### 7.6 测试和完成标准

新增 tests/test_obsidian_wiki_tools.py，覆盖项目隔离、路径登记、ACL、limit、字符预算、状态透传、derived 标记和工具目录注册。

完成后 Hermes 能回答项目有哪些 Wiki 页面、哪些页面待审阅、某个页面的整理内容是什么；事实回答仍显示 Evidence 和来源版本。

## 8. 阶段 3：Hermes 管理同步和维护

### 8.1 目标

让 Hermes 主动触发来源同步、导出、Lint 和知识库状态检查，形成可审计的知识维护循环。

### 8.2 新增服务和对象

    src/project_lens/application/knowledge_operations.py
    src/project_lens/application/obsidian_sync.py
    src/project_lens/application/obsidian_lint.py
    src/project_lens/context/knowledge_events.py

KnowledgeOperation 至少包含 id、operation_type、project、requested_by、status、started_at、finished_at、summary、error 和 audit_refs。operation_type 为 sync、export 或 lint；status 为 queued、running、succeeded 或 failed。

### 8.3 同步编排

    sync_sources
        -> resolve ProjectSpace and EffectiveAccessScope
        -> build allowed connectors
        -> execute connector sync with cursor
        -> persist SourceRecord revisions
        -> update EvidenceIndex
        -> compute authority/conflict/gap changes
        -> optionally compile Wiki drafts
        -> export reviewed content
        -> update manifest and audit

同步和导出分开记录。连接器成功但导出失败时，SourceRecord 和 Evidence 仍然有效，下一次可以只重试导出。

### 8.4 Hermes 工具

新增：

    projectlens_sync_sources
    projectlens_export_obsidian
    projectlens_lint_wiki

工具支持 dry_run 或等价预览，默认返回变更摘要，不把大段 Markdown 放入 Hermes 上下文。所有工具都必须经过 ProjectRuntimeContextResolver。

### 8.5 Lint 规则

首版检查：

- frontmatter 缺失或类型错误；
- source key 不存在；
- 引用页面不存在；
- 页面状态和目录不一致；
- source revision 已被 superseded；
- 页面超过 freshness 窗口；
- 同一对象存在互相冲突的页面；
- 页面没有来源或关联；
- 共享目录出现禁止文件类型；
- 页面正文超过配置上限。

Lint 输出 LintFinding，写入 50-review 并记录操作审计，不自动修改页面。

### 8.6 调度

可以复用 FeishuDocSyncScheduler 的调度思想，但后台任务不直接调用 Hermes。由 ProjectLens 后台触发同步，Hermes 通过工具查询和发起受控操作。

首版支持手动触发、每日同步、来源变更后的增量同步、失败重试和同一 connector 的并发锁。

### 8.7 测试和完成标准

新增 tests/test_knowledge_operations.py、tests/test_obsidian_lint.py 和 tests/test_obsidian_sync.py，覆盖游标、部分 connector 失败、导出失败隔离、dry-run、并发锁、Lint finding 和审计。

完成后 Hermes 可以完成一次同步、编译、导出和 Lint，并返回变更摘要和失败项；失败不影响已有 Evidence 和问答。

## 9. 阶段 4：Obsidian Inbox 受控回写

### 9.1 目标

允许团队在 Obsidian 中补充知识，同时保证人工文本必须经过 Evidence 分析、冲突检查和审批，才能进入 confirmed Wiki 或 ProjectMemory。

### 9.2 Inbox 格式

Inbox 文件只能位于 40-inbox/human-proposals 或 40-inbox/hermes-proposals，并包含 proposal_id、tenant_id、project_id、kind、status=proposed、author、created_at、evidence_ids 和 approval_required=true。

不能使用 Inbox frontmatter 伪造 confirmed、approved_by 或其他正式状态。

### 9.3 新增模块和对象

    src/project_lens/obsidian/inbox.py
    src/project_lens/application/obsidian_inbox_service.py
    src/project_lens/domain/knowledge_proposal.py

KnowledgeProposal 至少包含 id、project、kind、title、content、source_path、source_hash、status、evidence_ids、created_by、decided_by 和 decision_reason。status 为 proposed、needs_evidence、conflicted、approved 或 rejected。

### 9.4 导入流程

    scan inbox
        -> validate path/frontmatter/size
        -> verify tenant/project ownership
        -> calculate content hash
        -> match Evidence and SourceRecord
        -> detect conflicts and duplicate proposal
        -> create KnowledgeProposal
        -> write review page
        -> optional MemoryProposal
        -> human approval
        -> publish confirmed Wiki or ProjectMemory

相同 source_path + source_hash 不得重复创建 proposal。

### 9.5 Hermes 工具

新增：

    projectlens_import_obsidian_inbox
    projectlens_propose_wiki_update

import 工具只扫描 Inbox 白名单目录，不接受任意路径。propose 工具只能创建或更新 proposal，不能直接将页面置为 confirmed。

### 9.6 审批规则

- 需求范围、正式决策、测试通过、负责人、风险等级和发布时间等高影响内容必须人工审批；
- 有 Evidence 且低影响的导航、关键词和链接可以自动发布，但保留生成审计；
- MemoryProposal 必须沿用 MemoryApprovalGateway；
- 被拒绝的 proposal 保留原因，不能直接删除；
- 批准后原 Inbox 文件移动到 processed 或标记 processed，不覆盖原文。

### 9.7 测试和完成标准

新增 tests/test_obsidian_inbox.py 和 tests/test_knowledge_proposals.py，覆盖 frontmatter、路径、重复导入、Evidence 匹配、冲突、越权、审批转换、MemoryProposal 边界和拒绝原因。

完成后团队可以提交一条新决策，Hermes 将其识别为 proposed，展示依据和冲突，并由有权限人员批准后进入 confirmed Wiki 或 ProjectMemory。

## 10. 阶段 5：团队共享运营和 Git 工作流

### 10.1 目标

让独立 Vault 适合多人使用，具备变更审阅、回滚、同步状态、权限变更和失败恢复能力。

### 10.2 Git 策略

Vault 是独立 Git 仓库，不与 ProjectLens 代码仓库混合。建议分支：

- main：团队当前可读版本；
- hermes/sync-date：自动同步变更；
- hermes/proposal-id：知识提案；
- review/id：人工审阅分支。

Hermes 默认只能生成变更和 commit，不自动推送或合并到 main，除非部署策略明确允许。

### 10.3 冲突处理

1. 来源冲突：由 ProjectLens authority resolver 处理，Obsidian 展示结果。
2. 页面生成冲突：由 manifest/content hash 检测，重新编译或生成 review。
3. Git 编辑冲突：保留双方内容，禁止 Hermes 静默覆盖人工修改。

### 10.4 运营页面和指标

90-system 至少维护 sync-status.md、sync-log.md、schema.md、export-policy.md 和 manifest.json。

记录同步成功率、导出成功率、缺 source key 页面数、conflicted/stale 页面数、Inbox 待处理时长、Wiki 命中率、Evidence 引用缺失率、Git 冲突数和自动化失败重试次数。

### 10.5 测试和完成标准

新增 tests/test_obsidian_git_workflow.py、tests/test_obsidian_recovery.py 和 tests/test_obsidian_export_policy.py，覆盖 Git diff、人工修改保护、回滚、部分写入失败、manifest 损坏恢复、权限收紧后的重新导出和禁止文件扫描。

完成后团队可以从独立 Git Vault 打开最新知识；Hermes 变更可审阅、可回滚；权限变化可触发重新导出；同步、导出、Lint 和审批状态都能追踪。

## 11. API 和工具注册顺序

| 顺序 | API / 工具 | 阶段 | 写入级别 |
| --- | --- | --- | --- |
| 1 | POST /projects/obsidian/export | 1 | 受控本地文件写入 |
| 2 | projectlens_search_wiki | 2 | 只读 |
| 3 | projectlens_read_wiki_page | 2 | 只读 |
| 4 | projectlens_sync_sources | 3 | 外部只读同步 + 本地导出 |
| 5 | projectlens_export_obsidian | 3 | 受控本地文件写入 |
| 6 | projectlens_lint_wiki | 3 | 只读诊断 + review 输出 |
| 7 | projectlens_import_obsidian_inbox | 4 | 创建 proposal |
| 8 | projectlens_propose_wiki_update | 4 | 创建或更新 proposal |

工具目录中的所有工具必须明确 allow_apply=false，或使用单独的 proposal/approval 类别。任何正式状态变更都必须走已有审批服务。

## 12. 数据库和持久化建议

### 12.1 阶段 1

复用现有 SQLite wiki_page_drafts。Vault manifest 作为文件系统状态，不把 Markdown 正文复制一份到数据库。

### 12.2 阶段 3

新增 knowledge_operations 和 obsidian_exports：

    knowledge_operations
        id, tenant_id, project_id, operation_type, requested_by,
        status, started_at, finished_at, summary, error, audit_refs

    obsidian_exports
        tenant_id, project_id, vault_id, relative_path,
        content_hash, source_keys, status, exported_at, operation_id

### 12.3 阶段 4

新增 knowledge_proposals：

    id, tenant_id, project_id, source_path, source_hash,
    kind, title, content, status, evidence_ids,
    created_by, decided_by, decision_reason, created_at, decided_at

所有表按 tenant/project 隔离，proposal 状态变更追加审计事件。

## 13. 安全要求

1. Vault 路径必须在部署配置的允许根目录内。
2. 所有路径使用相对路径，拒绝 ..、盘符和 UNC 路径。
3. 只允许写入固定目录。
4. 写入前扫描敏感文件名和敏感字段。
5. 不把 API key、Cookie、token、.env 或数据库导出到 Markdown。
6. Wiki 工具返回的内容必须再次绑定当前项目和权限。
7. Hermes 不能使用 read_project_file 读取 Vault 外部任意路径。
8. 提案内容不能伪造审批人和 confirmed 状态。
9. 文件写入失败必须可恢复，不得删除原始 revision。
10. Git 推送、合并和外部发布必须是单独的显式部署能力。

## 14. 评测和回放

每个阶段都应加入固定回放问题：

- 项目当前需求范围是什么？
- 哪些内容已经确认，哪些仍是提议？
- 测试是否已经通过？
- 当前有哪些知识缺口？
- 飞书文档和会议纪要是否冲突？
- Hermes 生成的 Wiki 页面能否回到 Evidence？
- 无权限用户能否看到受限来源？
- 旧 revision 是否被错误当作当前事实？

评测至少检查 source key 完整率、Evidence 引用正确率、越权泄露为零、conflicted/expired 内容未被当作 confirmed、同步导出幂等和工具错误恢复提示。

## 15. 推荐提交边界

1. feat(obsidian): add vault config and frontmatter contracts
2. feat(obsidian): export source mirrors and wiki drafts
3. feat(hermes): add wiki search and page read tools
4. feat(knowledge): add sync export and lint operations
5. feat(obsidian): import inbox proposals with approval boundary
6. feat(ops): add vault git workflow and recovery checks

每个提交都应包含对应测试，不把五个阶段混在一个大提交里。阶段之间通过 feature flag 独立启停。

## 16. 最终验收清单

### 阶段 0

- [ ] Vault 配置、目录和 schema 冻结
- [ ] 路径和敏感文件安全测试通过
- [ ] export policy 已记录

### 阶段 1

- [ ] 来源镜像和 Wiki 页面可以导出
- [ ] revision、source key 和 frontmatter 完整
- [ ] 导出幂等且失败可恢复
- [ ] Home、Review、Knowledge Gaps 页面生成

### 阶段 2

- [ ] Hermes 可搜索和读取 Wiki
- [ ] Wiki 内容明确标记为派生
- [ ] ACL、项目隔离和预算测试通过
- [ ] Evidence 优先规则进入 Hermes profile

### 阶段 3

- [ ] Hermes 可发起同步、导出和 Lint
- [ ] 操作有状态、审计和失败恢复
- [ ] Lint 能发现链接、来源、状态和过期问题

### 阶段 4

- [ ] Inbox 可导入为 proposal
- [ ] proposal 可关联 Evidence 并标记冲突
- [ ] 高影响内容必须审批
- [ ] ProjectMemory 只能通过审批网关写入

### 阶段 5

- [ ] Vault 独立 Git 仓库运行
- [ ] Hermes 变更可审阅和回滚
- [ ] 人工修改不会被静默覆盖
- [ ] 权限变更、失败重试和恢复演练通过

## 17. 结论

开发路线的核心不是把 Obsidian 变成 ProjectLens 的替代数据库，而是把它接到现有知识治理链路上：

    ProjectLens 保证真实、权限和证据
    Hermes 负责整理、维护和提案
    Obsidian 负责团队共享阅读、关联、审阅和补充

第一份代码应从阶段 0 和阶段 1 开始：先冻结 Vault 契约，再实现只读导出。只有导出内容、权限边界和 source key 稳定之后，才进入 Hermes Wiki 工具和 Inbox 回写。
