# ProjectLens 后续计划白话版

更新时间：2026-08-05

这份文档是给项目作者自己看的，用来快速理解 ProjectLens 后续到底要做成什么样。它不是 Cursor 的详细任务清单，也不是完整架构说明；正式规划以 `docs/project-agent-final-plan.md` 为准。

## 1. 这个项目到底要做成什么

ProjectLens 不是一个“飞书里输入 `/project` 然后返回项目报告”的机器人。

真正目标是：

```text
团队成员在飞书里自然提问，
机器人知道当前项目、提问者角色和聊天场景，
然后在权限范围内读取项目资料，
基于证据回答项目问题，
并用适合这个人的方式说明结论、未知和下一步。
```

一句话：

```text
Hermes 负责 Agent 怎么跑；
ProjectLens 负责项目里什么能读、什么可信、什么能对谁说。
```

## 2. 为什么不能继续按之前那样做

之前的效果越来越像：

```text
/project 命令
-> Skill 分类
-> 固定流程收集证据
-> 生成通用报告
-> 卡片按钮切技术版 / 产品版 / 证据版
```

这个方向能演示，但不够像真正的团队项目 Agent。

问题主要有 4 个：

1. 用户要记 `/project`，不像自然沟通；
2. Skill 和卡片越来越多，但 Agent 自主调查能力没有真正变强；
3. 角色变成了“答案生成后的过滤器”，权限和风格进入得太晚；
4. 容易让用户感觉自己在问 Hermes 插件，而不是在问当前项目。

所以现在要纠偏：先做项目空间、角色权限和只读调查能力，再做展示。

## 3. Hermes 和 ProjectLens 怎么分工

可以这样理解：

```text
Hermes = 会说话、会调用工具、会接飞书、会记住对话上下文的 Agent 运行时
ProjectLens = 项目资料室 + 权限管理员 + 事实审核员 + 项目知识地图
```

Hermes 负责：

- 飞书消息接入；
- 多轮对话；
- 调大模型；
- tool-calling loop；
- session / profile；
- 上下文压缩；
- 插件和 MCP。

ProjectLens 负责：

- 当前是哪个项目；
- 这个项目有哪些代码、文档、任务、发布、事故；
- 这个人是什么角色；
- 这个群能展示哪些内容；
- RAG / GraphRAG 怎么查；
- 每条事实有没有来源；
- 回答有没有越权；
- 哪些项目记忆可以长期保存。

## 4. ProjectSpace 是什么

ProjectSpace 就是“一个项目的上下文包”。

比如以后可以有：

```text
payment-system ProjectSpace
crm-system ProjectSpace
mobile-app ProjectSpace
ai-platform ProjectSpace
```

每个 ProjectSpace 里面配置：

- 项目 ID；
- 项目名称；
- 代码仓库；
- 文档来源；
- 任务系统；
- 发布记录；
- 故障记录；
- 知识图谱命名空间；
- 项目记忆命名空间；
- 文件读取白名单；
- 飞书群绑定；
- 角色权限规则。

这样以后换项目时，不是改 Agent 代码，而是换 ProjectSpace 配置。

## 5. 角色和权限到底怎么用

以前的思路容易变成：

```text
先生成一份通用答案
再用卡片切技术版、产品版、管理版
```

现在要改成：

```text
先知道这个人是谁、在哪个群、是什么角色、能看什么
然后 Agent 再开始查资料和回答
```

需要三个东西：

```text
ProjectMemberRolePolicy：这个人在项目里的角色和权限
ChatVisibilityPolicy：当前群聊允许展示的内容范围
EffectiveAccessScope：本次回答真正能使用和展示的范围
```

实际权限是：

```text
用户权限 ∩ 群聊权限 = 本次回答权限
```

举例：

- 开发私聊机器人：可以看代码、commit、错误栈、调用链；
- 开发在混合项目群提问：不能把完整日志和敏感技术细节直接发到群里；
- 产品提问：重点回答业务影响、当前进度、需要谁配合；
- 管理者提问：重点回答风险、阻塞、负责人、决策点；
- 新人提问：重点回答项目背景、模块地图、推荐阅读路径。

角色不是最后的卡片按钮，而是 Agent 一开始工作的前置条件。

## 6. 后续核心链路应该是什么样

目标链路：

```text
用户在飞书自然提问
-> Hermes 收到消息
-> ProjectLens 解析当前 ProjectSpace
-> ProjectLens 解析用户角色和群聊可见范围
-> Hermes 进入 ProjectLens-safe profile
-> Hermes 决定要调用哪些 ProjectLens 只读工具
-> ProjectLens 检查权限并返回 Evidence / ToolResult
-> Hermes 根据证据生成答案草稿
-> ProjectLens 校验引用、权限和未知项
-> Hermes 把答案发回飞书
```

这里的重点是：

```text
Hermes 可以决定下一步想查什么；
ProjectLens 决定能不能查、查到什么、能不能说。
```

## 7. `/project` 以后怎么处理

`/project` 不删除，但降级为：

- 本地 smoke test；
- 开发调试入口；
- 兜底手动入口。

正式体验不是：

```text
/project 介绍一下这个项目
```

而是：

```text
介绍一下这个项目
这个接口为什么报错？
最近订单模块谁改了？
我作为产品现在需要配合什么？
```

## 8. 回答契约是什么

Agent 最后不能只吐一段随意文本，内部要先形成结构化答案。

例如：

```text
AnswerDraft
- conclusion：一句话结论
- facts：事实，必须有引用
- inferences：推断，不能伪装成事实
- unknowns：还不知道什么
- next_actions：建议下一步
- citations：资料来源
- visibility_scope：这条回答能发给谁
- role_policy_used：用了哪个角色策略
```

然后 ProjectLens 再检查：

- 事实有没有证据；
- 引用是不是当前项目的；
- 用户有没有权限看；
- 当前群能不能展示；
- 有没有泄露敏感信息；
- 有没有把未知说成确定；
- 有没有偷偷打开写代码、部署、回滚。

通过后才发到飞书。

## 9. 后续阶段怎么做

### Phase 1：先做 ProjectSpace / RolePolicy

目标：让系统知道“当前是哪个项目、这个人是什么角色、这个群能展示什么”。

做完后的效果：

```text
同一个 Agent 壳可以服务多个项目；
同一个问题，不同角色能看到不同深度；
不改代码也可以换项目。
```

### Phase 2：自然语言飞书入口

目标：不要依赖 `/project`。

做完后的效果：

```text
用户在项目群自然提问，机器人能自动进入当前项目上下文。
```

### Phase 3：Hermes-driven 只读调查

目标：让 Hermes 真正作为 Agent runtime 调 ProjectLens 工具。

做完后的效果：

```text
未预置问题也能查代码、文档、图谱、最近变更，并给出带引用答案。
```

### Phase 4：RAG / GraphRAG 增强

目标：让 ProjectLens 更懂项目结构。

做完后的效果：

```text
能回答模块关系、接口调用、负责人、变更影响、事故关联、知识缺口。
```

### Phase 5：角色化协作回答

目标：不同角色得到适合自己的项目答案。

做完后的效果：

```text
开发看到排查路径；
产品看到业务影响；
管理者看到风险和阻塞；
新人看到背景和学习路径。
```

### Phase 6：项目记忆和知识闭环

目标：把团队确认过的信息沉淀下来。

做完后的效果：

```text
重要决策、事故复盘、知识缺口、负责人信息可以进入长期项目记忆，
但必须经过审批。
```

### Phase 7：受控代码修改能力

目标：只读能力稳定后，再考虑让 Agent 提出修复方案。

做完后的效果：

```text
Agent 可以提出 patch plan / test plan，
但真正 Apply、PR、部署、回滚都需要人工审批。
```

## 10. 近期最应该做什么

现在最应该做：

1. 固化最终规划；
2. 做 ProjectSpace 注册表；
3. 做 ProjectMemberRolePolicy；
4. 做 ChatVisibilityPolicy；
5. 让飞书消息能自然解析到项目和角色；
6. 准备 Hermes 调用 ProjectLens 只读工具。

现在不应该做：

- 不继续加 `/project-*` 命令；
- 不继续做更多卡片按钮；
- 不继续新增业务 Skill；
- 不把 RoleView 当权限过滤器；
- 不打开代码修改、PR、部署、回滚；
- 不 fork Hermes core。

## 11. 面试时怎么讲

可以这样讲：

> 我一开始做的是一个接入飞书的项目分析 Agent，后来发现单纯做 `/project` 命令和卡片报告会偏成普通 RAG bot。所以我把架构重新拆成 Hermes Runtime + ProjectLens Kernel：Hermes 负责通用 Agent 运行时，比如飞书网关、模型调用、工具循环、session 和 profile；ProjectLens 负责项目空间、RAG/GraphRAG、证据、权限、角色策略和引用校验。这样系统可以服务不同项目，也能根据用户角色和群聊场景控制能读什么、能说什么。短期我先做只读项目协作问答，等项目事实和权限体系稳定后，再考虑受控代码修改能力。

