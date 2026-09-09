# Phase 4：ProjectSpace 可插拔项目空间

更新日期：2026-08-07

## 这一阶段解决什么

Phase 4 的目标不是新增 Skill、卡片或代码修改能力，而是把 ProjectLens 从“当前项目专用 Agent”推进成：

```text
固定 Hermes Runtime
+ 固定 ProjectLens Kernel
+ 可替换 ProjectSpace
```

换项目时，应该新增或替换 ProjectSpace 配置和索引资源，而不是改 Hermes、改 Agent loop、复制 workflow。

## 已完成的基础

本阶段第一刀已经落地：

- `ProjectSpace` 新增 `rag_namespace`，与已有 `graph_namespace` / `memory_namespace` 一起成为项目级索引命名空间。
- 新增 `SourceConnectorRef`，用于声明项目包里的 repo、documents、feishu_docs 等来源。
- 新增 `FeishuChatBindingRef`，用于在项目包层描述飞书群聊绑定。
- JSON loader 支持：
  - `rag_namespace`
  - `source_connectors`
  - `feishu_chat_bindings`
- 新增 `validate_project_space(space)`，用于提前发现不可插拔或不安全的项目配置。
- `config/projects/projectlens.json`、`payment.json`、`crm.json` 已补齐 manifest 风格字段。
- registry 级别集合校验已落地，可检查重复项目、重复飞书群绑定和 rag/graph/memory namespace 冲突。
- 新增 `ProjectSpaceInspectService` 和只读 inspect API。
- 新增新项目接入指南：`docs/projectspace-onboarding-guide.md`。

相关代码：

```text
src/project_lens/project_space/models.py
src/project_lens/project_space/registry.py
src/project_lens/project_space/validation.py
src/project_lens/application/project_space_inspect.py
tests/test_project_space_manifest.py
tests/test_project_space_inspect_api.py
```

## ProjectSpace Manifest 应该包含什么

一个项目空间至少应该能描述：

```text
ProjectSpace
- tenant_id / project_id / display_name
- repositories
- documents_root
- rag_namespace
- graph_namespace
- memory_namespace
- source_connectors
- feishu_chat_bindings
- file_allowlist
- role_policies
- chat_visibility_policies
```

其中：

- `repositories` 描述代码在哪里。
- `documents_root` 描述本地项目文档在哪里。
- `source_connectors` 描述项目知识来源，但不代表 Agent 可以直接访问这些来源。
- `rag_namespace` / `graph_namespace` / `memory_namespace` 描述索引隔离边界。
- `feishu_chat_bindings` 描述哪些飞书群可以绑定这个项目。
- `role_policies` 和 `chat_visibility_policies` 决定运行前权限。

## 重要边界

`SourceConnectorRef` 只是项目包声明，不是运行时直连入口。

正确链路是：

```text
Hermes 选择 projectlens_* 工具
-> ProjectLens Tool Envelope
-> ProjectSpace / RolePolicy / ChatVisibilityPolicy
-> Gateway / ContextEngine / Evidence
-> 返回带 citation 的 ToolResult
```

错误链路是：

```text
Hermes / Feishu adapter / plugin
-> 直接读 repo / EvidenceIndex / GraphStore / 文件系统
```

后一种仍然禁止。

## 校验能力

当前 `validate_project_space` 会检查：

- 是否声明了 repository；
- repository 路径是否存在；
- documents_root 路径是否存在；
- file_allowlist 是否包含绝对路径或 `..`；
- 是否有 `rag_namespace`；
- 是否有 `graph_namespace`；
- 是否有 `memory_namespace`；
- 是否存在重复飞书群绑定。

这一步的意义是：新项目接入时先 fail fast，不要等用户在飞书里问问题时才发现项目包缺字段。

## 下一步给 Cursor 的任务

Cursor 后续可以继续做 Phase 4 第二刀，但不要改变方向。

建议任务：

```text
Phase 4 第二刀已由 Codex 完成。

当前已经完成：
- ProjectSpace.rag_namespace
- SourceConnectorRef
- FeishuChatBindingRef
- JSON loader 支持 source_connectors / feishu_chat_bindings
- validate_project_space
- validate_project_spaces
- ProjectSpaceInspectService
- GET /api/v1/project-agent/project-spaces
- GET /api/v1/project-agent/project-spaces/{tenant_id}/{project_id}
- GET /api/v1/project-agent/project-spaces/{tenant_id}/{project_id}/scope
- config/projects 三个示例 manifest
- docs/projectspace-onboarding-guide.md
- tests/test_project_space_manifest.py
- tests/test_project_space_inspect_api.py

下一步不要继续加 ProjectSpace 基础字段，除非真实项目接入时发现缺口。
建议转入：
1. 用真实项目 manifest 做一次接入演练。
2. 将 RAG / GraphRAG 索引构建与 ProjectSpace namespace 对齐。
3. 让 Hermes tool loop 的工具结果带上 ProjectSpace inspect/debug 链接或 audit ref，而不是卡片堆信息。

不要做：
- 不新增 Skill
- 不重做卡片
- 不打开 Apply / PR / deploy / rollback / restart
- 不修改 Hermes core
- 不让 Hermes 或 Feishu adapter 直接读 EvidenceIndex / GraphStore / filesystem
```

## 验收命令

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_project_space_manifest.py tests/test_project_space_policies.py tests/test_project_registry.py -q
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_project_space_inspect_api.py -q
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m ruff check src/project_lens/project_space src/project_lens/application/project_space_inspect.py tests/test_project_space_manifest.py tests/test_project_space_policies.py tests/test_project_registry.py tests/test_project_space_inspect_api.py
```

本次验证结果：

```text
18 passed
All checks passed
```
