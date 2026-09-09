# ProjectSpace 新项目接入指南

更新时间：2026-09-09

产品总纲：[`projectlens-product-document.md`](projectlens-product-document.md)。

ProjectSpace 是项目的统一上下文边界。换项目应换配置，而不是改 Hermes 或复制一套 Agent workflow。

```text
不要改 Hermes core
不要复制 Agent workflow
不要新增固定 Skill 覆盖项目差异
只新增 ProjectSpace manifest + 索引 / 连接器资源
```

## 1. 准备项目资料

MVP 建议至少准备：

```text
your-project/
  src/ 或仓库根目录     # 代码（可对接 GitHub）
  docs/ 或 knowledge/   # 需求、说明、发布记录
  # 另：飞书文档 / 会议纪要 token、一个多维表格（可选）
```

第一阶段只读接入：不配置 deploy、rollback、自动 PR、Apply。

## 2. 新增 ProjectSpace manifest

在 `config/projects/` 下新增 JSON，例如 `config/projects/billing.json`。可参考现有 `payment.json` / `projectlens.json`。

最小可用示例：

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
  "public_sources": ["knowledge/"],
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
    },
    {
      "kind": "feishu_docs",
      "name": "billing_feishu_docs",
      "namespace": "demo:billing:rag",
      "metadata": {
        "token": "YOUR_FEISHU_DOC_TOKEN"
      }
    }
  ],
  "feishu_chat_bindings": [
    {
      "tenant_key": "feishu-demo",
      "chat_id": "oc_billing",
      "visibility": "team_shared"
    }
  ],
  "members": [
    {"actor_id": "u_dev", "roles": ["developer"]},
    {"actor_id": "u_product", "roles": ["product"]},
    {"actor_id": "u_manager", "roles": ["manager"]}
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

真实飞书 document token、chat_id、open_id 只放本机配置，不要提交到公开仓库。

## 3. 字段怎么理解

### identity

- `tenant_id`：租户
- `project_id`：项目唯一 ID
- `display_name`：展示名

### repositories / documents_root / file_allowlist

声明代码与文档可读边界。Hermes 只能通过 ProjectLens 只读工具访问，不能直接扫盘。

### public_sources / members

- `public_sources`：未知成员（guest）可见的最小来源；**必填**（deny-by-default）
- `members`：项目成员与角色；未列出的 actor 只能看 `public_sources`

真机飞书联调时，`members[].actor_id` 必须是飞书用户 **`open_id`**（形如 `ou_...`），不是花名或邮箱。  
取 ID 的方法见 [`feishu-test-quickstart.md`](feishu-test-quickstart.md) §2.2。

角色模板可展开为 `role_policies`。角色影响调查范围与表达，不只是卡片过滤。

### namespaces

- `rag_namespace` / `graph_namespace` / `memory_namespace`

不同项目不要复用同一 namespace。

### source_connectors

声明知识来源，例如：

- `repo`
- `documents`
- `feishu_docs`
- （按实现启用）多维表格、GitHub、会议纪要等连接器

manifest 声明 ≠ 自动拥有写权限；同步与检索仍走 ProjectLens 内核。

### feishu_chat_bindings

声明哪些飞书群属于该 ProjectSpace。运行时还需 `.env` 中的 `PROJECT_LENS_FEISHU_PROJECT_BINDINGS` 与飞书身份解析一致。`chat_id` 与 bindings 必须指向同一群。

### role_policies / chat_visibility_policies

权限在检索前进入上下文：

```text
actor_id + chat_id
-> ProjectSpace
-> RolePolicy
-> ChatVisibilityPolicy
-> EffectiveAccessScope
-> Tool Envelope
```

```text
用户角色权限 ∩ 群聊可见范围 ∩ 项目资源权限
```

## 4. 注册到运行环境

1. 确保 `PROJECT_LENS_PROJECT_SPACES_DIR`（若使用）能加载到 `config/projects/`。
2. 在 `.env` 的 `PROJECT_LENS_LOCAL_PROJECT_REGISTRY` 中注册同一 `tenant_id` / `project_id` 与本地路径。
3. 在 `PROJECT_LENS_FEISHU_PROJECT_BINDINGS` 中把目标群绑到该项目（一对一）。
4. 按需配置文档同步、GitHub token 等，见 [`pilot-launch-config.md`](pilot-launch-config.md)。

## 5. 接入后怎么检查

```text
GET /api/v1/project-agent/project-spaces
GET /api/v1/project-agent/project-spaces/{tenant_id}/{project_id}
GET /api/v1/project-agent/project-spaces/{tenant_id}/{project_id}/scope?user_id=...&chat_id=...
```

这些 API 返回 manifest 与权限摘要，不返回文件正文或 Evidence 全文。pilot/production 需带 service token。

## 6. 验收标准

- 新增 `config/projects/*.json` 后可被 registry 发现
- `validate_project_space()` 通过（含 `public_sources`、members）
- repo / documents_root 路径存在
- namespace 不与其他项目冲突
- 飞书群绑定不与其他项目冲突
- 当前用户 + 群聊能解析出 EffectiveAccessScope
- 自然语言问答只走 ProjectLens 只读工具
- Apply / PR / deploy / rollback / restart 仍关闭

对齐产品 MVP 的手测问题：

```text
这个项目是做什么的？
项目当前状态是什么？
这个需求现在做到哪一步了？
负责人是谁，还缺什么信息？
```

## 7. 不要做什么

- 不要为接新项目修改 Hermes core
- 不要复制一套 ProjectWorkflow / 固定 Skill 覆盖项目差异
- 不要让 Feishu adapter 直接读 EvidenceIndex、GraphStore 或文件系统
- 不要把 `.env`、数据库、密钥、私有目录加入 allowlist
- 不要把 LLM Wiki 候选内容当成已确认正式事实源
- 不要在公开仓库提交真实飞书 token / chat_id / 用户 open_id
