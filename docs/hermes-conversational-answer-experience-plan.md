# Hermes 自然对话回答体验改进方案

版本：v0.1  
日期：2026-09-05  
状态：设计中  
适用范围：ProjectLens 飞书入口、Hermes Runtime、ProjectLens Answer/Card Renderer

## 1. 背景

Hermes-only 迁移完成后，ProjectLens 已经具备可信身份、项目权限、受控工具、Evidence 校验、AgentRun 审计和答案渲染能力。但当前实际测试中仍出现了明显的“系统报告腔”：

- 回答开头直接暴露内部结构，而不是先回答用户问题；
- Hermes 返回普通文本时，被整体降级为“未结构化文本”；
- 工具没有充分继续检索，就直接给出“资料不足”；
- trace、runtime、skill、provider 等调试字段占据回答主体；
- 测试结果中出现 `read_agent` / `project_investigation`，说明仍可能连接旧 gateway、旧进程或旧 profile。

本方案不改变 ProjectLens 的安全边界，而是重做回答体验，使其更接近豆包式的自然交流：口语、主动、有上下文，但保持克制、专业和可验证。

## 2. 目标与非目标

### 2.1 目标

1. 用户先得到自然、直接的一句话回答，再看到必要的依据和下一步。
2. Hermes 在说“资料不足”之前，主动使用受控工具进行补查和交叉核对。
3. 非结构化模型输出能够自动恢复为统一的 `AnswerDraft`，避免无谓降级。
4. 飞书主卡片只展示用户需要的内容；运行细节默认折叠到“查看运行详情”。
5. 保留以下不可绕过的 Kernel 边界：可信身份、EffectiveAccessScope、ACL、Evidence、引用验证、只读门禁、审计和审批。

### 2.2 非目标

- 不开放终端、文件系统、浏览器、部署、回滚、重启或自动改代码能力。
- 不让 Hermes 直接访问 SQLite、EvidenceIndex、Git、文件系统或记忆库。
- 不把未经 ProjectLens verifier 验证的模型文本直接发送给用户。
- 不删除 legacy workflow/read-agent 回归代码；它们仍可作为隔离 fixture，但不能成为生产入口。

## 3. 用户体验原则

### 3.1 先回答，再解释

默认回答顺序：

```text
直接结论
-> 2-4 条已确认信息
-> 尚未确认的部分（如有）
-> 贴合当前话题的追问或下一步
```

例如“你现在接的项目叫什么”应回答为：

> 我现在接的是 Payment 项目，服务是 `order-service`。你可以继续问我它的核心流程、负责人、近期变更，或者某个报错该怎么排查。

这里的项目绑定来自可信 ProjectSpace 和当前会话上下文，不需要把它包装成一次检索失败。

### 3.2 资料不足要自然、具体

禁止向用户暴露以下内部术语：

- 未结构化文本；
- Evidence 引用校验；
- fact core；
- tool trail；
- skill guide；
- provider fallback。

建议使用：

> 我先查了当前项目里能访问到的资料，但还没有找到足够证据确认这一点，所以不想凭猜测回答。你可以补充模块名、文件名、接口或报错，我再继续往下查。

如果工具确实没有结果，应说明“查了什么”和“还缺什么”，而不是只说“资料不足”。

### 3.3 事实核心一致，表达可以个性化

团队、技术、产品、测试视图可以调整深度和措辞，但必须基于同一份已验证的事实核心。角色视图不能新增事实，也不能扩大可见范围。

## 4. 运行时与工具策略

### 4.1 工具优先

凡是涉及注册项目事实的问题，Hermes 必须先调用至少一个 ProjectLens 受控工具。优先级建议：

| 问题类型 | 首选工具 | 必要时补充 |
| --- | --- | --- |
| 项目概览、服务、负责人 | `projectlens_search_context` | `projectlens_authorized_evidence` |
| 架构、依赖、入口 | `projectlens_search_context` | `projectlens_query_graph`、`projectlens_read_project_file` |
| 最近变更、发布、故障 | `projectlens_search_context` | `projectlens_search_project_history` |
| 知识库缺口 | `projectlens_list_knowledge_gaps` | `projectlens_search_context` |
| 指定文件或函数 | `projectlens_read_project_file` | `projectlens_search_context` |

### 4.2 主动补查

Hermes 的调查循环应遵循：

```text
首轮检索
-> 判断证据是否足够
-> 不足时改写查询并补查一次
-> 关系问题补 Graph 或指定文件
-> 统一交给 verifier
-> 仍不足才生成“暂未确认”
```

补查只能使用当前 `EffectiveAccessScope` 允许的工具、项目和来源。不得因为“想查得更全”而扩大权限。

建议设置：

- 默认最多 2-3 轮受控补查；
- 每轮限制工具数量和返回条数；
- 重复查询、重复工具批次及时停止；
- 工具失败时保留失败信息，但继续尝试安全的替代只读工具；
- 达到预算后明确告诉用户“我查到了什么、还缺什么”。

### 4.3 输出格式恢复

Hermes 最终文本不是合法 JSON 时，不应立即把答案降级成内部错误。应增加一次恢复回合：

1. 保留已获得的工具证据和工具轨迹；
2. 向 Hermes 发送短提示，要求仅按 `AnswerDraft` 字段返回 JSON；
3. 再次解析并交给 ProjectLens verifier；
4. 若恢复仍失败，使用自然语言保底模板，并明确没有形成可验证结论。

恢复回合不得新增未授权工具，也不得绕过 verifier。

## 5. AnswerDraft 与答案适配

### 5.1 结构化契约

`AnswerDraft` 继续作为 Hermes 输出和 ProjectAnswer 之间的唯一适配层，至少支持：

```json
{
  "conclusion": "一句话结论",
  "facts": [
    {"text": "已确认事实", "citations": ["evidence-id"]}
  ],
  "inferences": ["基于证据的有限推断"],
  "unknowns": ["尚未确认的部分"],
  "next_actions": ["只读的下一步建议"]
}
```

解析器可以容忍 Markdown code fence、前后说明文字和字段缺失，但不能自动把无引用文本升级为事实。

### 5.2 自然语言保底

当没有可展示事实时，保底答案应由 ProjectLens 生成，示例：

> 我查了当前项目可访问的代码、文档和知识库，但暂时没有找到能直接回答这个问题的证据。最有帮助的补充信息是：相关服务名、文件路径、接口名，或一段具体报错。

保底答案可以提到检索范围，但不展示内部 parser、ledger、skill 或 provider 名称。

## 6. 飞书卡片设计

### 6.1 默认主卡片

主卡片只显示：

1. 用户问题的自然回应；
2. 已确认事实或关键摘要；
3. 必要的未知项；
4. 2-4 个相关追问；
5. `查看运行详情` 按钮。

默认不显示：

- `trace_id`；
- `agent_mode`；
- 内部路由和 skill；
- provider、model、retry、usage；
- 完整工具参数；
- Evidence UUID 列表。

### 6.2 运行详情

点击 `查看运行详情` 后，展示同一个已完成 AgentRun 的安全摘要：

- `run_id` 和 `trace_id`；
- `runtime=hermes`；
- `entry_mode`；
- 调用过的 ProjectLens 工具名称；
- 引用数量和来源摘要；
- 失败原因、重试次数或验证状态；
- `allow_apply=false`。

详情接口必须重新校验当前用户的 tenant、project、actor、chat、角色和 policy version，不能仅凭前端传入的 run ID 读取其他运行。

### 6.3 失败卡片

失败卡片也要保持自然：

> 这次我没能完成项目资料核对。你可以稍后重试，或者把问题缩小到某个服务、文件或接口。

内部错误原因只放在运行详情中；如果是权限拒绝，则明确告诉用户“当前群聊或角色没有访问这部分资料的权限”。

## 7. 入口与配置要求

正式飞书项目问答链路必须是：

```text
Feishu Event
-> trusted identity / EffectiveAccessScope
-> HermesRuntimeService
-> ProjectLens tools
-> AnswerDraft
-> verifier
-> ProjectAnswer / AudienceView
-> natural Feishu card
```

`projectlens_ask_project` 保留为 MCP debug/smoke 工具；正式飞书群不应再通过旧 gateway 的 `read_agent` 或 `project_investigation` 路径回答项目问题。

手工测试出现以下字段时，应先检查进程、gateway 和 profile 是否过期：

```text
agent_mode: read_agent
内部路由: project_investigation
```

期望值为：

```text
runtime: hermes
agent_mode: hermes
```

## 8. 实施分阶段

### 阶段 1：回答适配层

- 扩展 `parse_hermes_answer()` 的容错和恢复回合；
- 增加自然语言保底模板；
- 确保所有输出仍进入 verifier；
- 增加“项目绑定自然回答”的基础上下文处理。

### 阶段 2：主动补查循环

- 为 Hermes loop 增加证据充分性判断；
- 支持查询改写和有限补查；
- 记录每轮工具摘要和停止原因；
- 工具失败时只允许安全替代路径。

### 阶段 3：飞书卡片与详情

- 主卡片隐藏调试区；
- 增加 `查看运行详情` action；
- 新增安全的 run detail 查询和权限复核；
- 保持角色视图复用同一 ProjectAnswer。

### 阶段 4：真实 gateway 验收

- 重启 ProjectLens API 和 Hermes gateway；
- 确认 profile 路由指向当前项目；
- 确认 Feishu 测试群绑定正确；
- 验证不再出现 `read_agent` / `project_investigation`；
- 用自然问题、指定文件问题、资料不足问题和写操作拒绝问题做冒烟。

## 9. 测试与验收

### 9.1 自动化测试

必须覆盖：

1. 合法 JSON 输出可以生成自然主卡片和已验证事实；
2. Markdown/普通文本输出触发一次格式恢复；
3. 两轮检索仍无证据时才返回自然“暂未确认”；
4. 工具失败不会切回 workflow/read-agent；
5. 主卡片不包含 trace、provider、skill 等调试字段；
6. 运行详情包含 `runtime=hermes`，且重新校验访问范围；
7. 角色视图不创建新事实、不创建新 AgentRun；
8. 写操作仍拒绝，`allow_apply=false`；
9. Feishu、HTTP、MCP 的答案 envelope 保持兼容。

### 9.2 人工验收问题

在飞书测试群中验证：

- “你现在接的项目叫什么”；
- “介绍一下这个项目”；
- “订单创建入口在哪个文件”；
- “这个报错怎么排查”；
- “负责人是谁”；
- “知识库还缺什么”；
- “请帮我部署到生产”。

验收重点不是固定文案，而是：回答是否先解决问题、是否主动查证、是否自然表达未知、是否默认隐藏内部运行细节。

## 10. 完成定义

满足以下条件时，本方案完成：

```text
用户看到的是自然、克制、项目化的回答
资料不足前已经完成有限主动补查
所有事实仍经过 Evidence / citation verifier
主卡片默认隐藏调试信息
运行详情可按权限展开
生产链路只显示 runtime=hermes
```

