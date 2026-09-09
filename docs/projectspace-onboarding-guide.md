# ProjectSpace 新项目接入指南

更新日期：2026-08-07

这份文档说明如何把一个新项目接入 ProjectLens。核心原则是：

```text
不要改 Hermes
不要复制 Agent workflow
不要新增固定 Skill
只新增 ProjectSpace manifest + 对应索引资源
```

## 1. 准备项目资料

建议先准备这些目录或文件：

```text
your-project/
  src/                 # 代码
  tests/               # 测试，可选
  knowledge/           # 项目文档、需求、发布说明、故障记录
```

第一版只读接入时，不需要配置 deploy、rollback、PR、Apply。

## 2. 新增 ProjectSpace manifest

在 `config/projects/` 下新增一个 JSON 文件，例如：

```text
config/projects/billing.json
```

示例：

```json
{
  "tenant_id": "demo",
  "project_id": "billing",
  "display_name": "Billing Service",
  "repositories": [
    {
      "name": "billing_service",
      "path": "examples/billing_service",
      "default_branch": "main"
    }
  ],
  "services": ["billing-service"],
  "environments": ["production"],
  "documents_root": "examples/billing_service/knowledge",
  "access_scope": "project:billing:read",
  "file_allowlist": ["src/", "tests/", "knowledge/"],
  "rag_namespace": "demo:billing:rag",
  "graph_namespace": "demo:billing",
  "memory_namespace": "demo:billing",
  "source_connectors": [
    {
      "kind": "repo",
      "name": "billing_service",
      "path": "examples/billing_service"
    },
    {
      "kind": "documents",
      "name": "billing_knowledge",
      "path": "examples/billing_service/knowledge",
      "namespace": "demo:billing:rag"
    }
  ],
  "feishu_chat_bindings": [
    {
      "tenant_key": "feishu-demo",
      "chat_id": "oc_billing",
      "visibility": "team_shared"
    }
  ],
  "role_policies": [
    {
      "actor_id": "u_dev",
      "role": "developer",
      "readable_sources": ["src/", "tests/", "knowledge/"],
      "allowed_tools": [
        "search_context",
        "read_project_file",
        "query_graph",
        "authorized_evidence",
        "list_knowledge_gaps"
      ],
      "forbidden_sources": ["secrets/", ".env", "*.db"],
      "answer_depth": "detailed",
      "answer_style": "technical",
      "visibility_level": "team_shared"
    }
  ],
  "chat_visibility_policies": [
    {
      "chat_id": "oc_billing",
      "visibility_level": "team_shared",
      "allowed_roles": ["developer", "qa", "product", "manager"],
      "readable_sources": ["src/", "tests/", "knowledge/"],
      "allowed_tools": [
        "search_context",
        "read_project_file",
        "query_graph",
        "authorized_evidence",
        "list_knowledge_gaps"
      ]
    }
  ]
}
```

## 3. 字段怎么理解

### identity

- `tenant_id`：租户。
- `project_id`：项目唯一 ID。
- `display_name`：给人看的项目名。

### repositories

声明项目代码在哪里。Agent 不能直接读这里，Hermes 只能通过 `projectlens_read_project_file` 等只读工具访问。

### namespaces

- `rag_namespace`：RAG/向量检索隔离边界。
- `graph_namespace`：知识图谱隔离边界。
- `memory_namespace`：项目记忆隔离边界。

不同项目不要复用同一个 namespace。

### source_connectors

声明这个项目有哪些知识来源，例如：

- `repo`
- `documents`
- `feishu_docs`

它只是 manifest 声明，不是运行时直连入口。

### feishu_chat_bindings

声明哪些飞书群属于这个 ProjectSpace。

正式运行时还需要通过飞书身份映射解析用户和群聊，但 ProjectSpace manifest 应该先把项目绑定关系描述清楚。

### role_policies / chat_visibility_policies

角色和群聊权限必须在生成前进入运行时。

不要先生成一份通用答案再过滤；正确链路是：

```text
user_id + chat_id
-> ProjectSpace
-> RolePolicy
-> ChatVisibilityPolicy
-> EffectiveAccessScope
-> Tool Envelope
```

## 4. 接入后怎么检查

启动服务后，可以用只读 inspect API 检查：

```text
GET /api/v1/project-agent/project-spaces
GET /api/v1/project-agent/project-spaces/{tenant_id}/{project_id}
GET /api/v1/project-agent/project-spaces/{tenant_id}/{project_id}/scope?user_id=...&chat_id=...
```

这些 API 只返回 manifest 和权限摘要，不返回文件内容、Evidence 正文或知识库内容。

## 5. 验收标准

一个新项目接入完成，至少要满足：

- 新增 `config/projects/*.json` 后能被 `ProjectRegistry` 发现；
- `validate_project_space()` 通过；
- repo / documents_root 路径存在；
- `rag_namespace` / `graph_namespace` / `memory_namespace` 不和其他项目冲突；
- 飞书群绑定不和其他项目冲突；
- 当前用户和群聊能解析出 effective scope；
- 飞书自然语言项目问题仍然只能走 `projectlens_*` 只读工具；
- Apply / PR / deploy / rollback / restart 仍然关闭。

## 6. 不要做什么

- 不要为了接新项目修改 Hermes core。
- 不要复制一套 ProjectWorkflow。
- 不要用新增 Skill 覆盖项目差异。
- 不要让 Feishu adapter 直接读 EvidenceIndex、GraphStore 或文件系统。
- 不要把 `.env`、数据库、密钥、私有目录加入 allowlist。
