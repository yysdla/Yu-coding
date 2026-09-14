# ProjectLens 开发安排

- 资料类型：开发资料
- 项目：projectlens
- 状态：confirmed
- 当前版本：2026-09-09
- 维护人：研发负责人
- 权威范围：development_progress

## 当前安排

| 工作项 | GitHub 依据 | 状态 | 说明 |
| --- | --- | --- | --- |
| 统一来源记录和版本快照 | Issue #101 | In Progress | 已有 SourceRecord、同步游标和版本保留实现 |
| 飞书文档只读同步 | Issue #102 | In Progress | 已有文档标准化和 Evidence 索引路径 |
| LLM Wiki 草稿编译 | Issue #103 | Planned | 需要先完成真实资料 fixture 和页面结构确认 |
| Hermes 群聊问答 | Issue #104 | Planned | 仅允许受限只读工具 |

## 已观察到的代码依据

- `src/project_lens/context/source_records.py` 定义了版本化来源快照；
- `src/project_lens/context/indexing/feishu_documents.py` 将飞书文档分块为 Evidence；
- `src/project_lens/application/project_agent_tools.py` 向 Hermes 暴露只读工具；
- `src/project_lens/application/audience_views.py` 仍保留角色视图代码，但本试点不把它作为产品主线。

## 当前限制

尚未发现已合并的 GitHub PR 与本试点资料同步闭环证据。开发安排以 Issue 状态为准，不能据此推断已完成发布。

## 关联资料

- `requirement-project-collaboration.md`
- `test-plan.md`
