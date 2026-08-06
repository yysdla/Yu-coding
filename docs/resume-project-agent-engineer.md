# ProjectLens：面向多项目团队协作的可插拔项目 Agent

本文档用于整理 ProjectLens 在 Agent 开发工程师简历中的项目描述、面试讲法和能力边界。

## 一、简历项目标题

**ProjectLens｜面向多项目团队协作的 Evidence-Grounded Agent（个人项目，开发中）**

一句话定位：

> 一个接入飞书、可切换多个 ProjectSpace 的只读优先项目 Agent。通用 Agent Runtime 负责对话和工具循环，ProjectLens Kernel 负责项目知识、权限、证据和引用可信度。

## 二、推荐简历版本（当前可使用）

### 项目描述

针对研发团队中“项目架构难理解、变更影响难判断、故障信息分散、技术与业务沟通成本高”等问题，设计并实现一个以 ProjectSpace 为边界的飞书项目协作 Agent。系统支持自然语言项目问答，能够在权限范围内检索代码、项目文档、Git 变更、任务信息和项目图谱，并输出带证据引用的事实、推断和未知项。

### 个人工作

- 设计 **Agent Runtime + ProjectLens Kernel** 分层架构：将通用对话、模型调用、session、profile 和 tool loop 与项目知识、RAG/GraphRAG、Evidence、权限和 Verifier 解耦，使同一 Agent 壳可以绑定不同项目空间。
- 设计并实现 **ProjectSpace / ProjectRegistry / RolePolicy / ChatVisibilityPolicy**，在生成前解析租户、项目、用户、角色和群聊可见范围，并通过角色权限与群聊权限交集生成 EffectiveAccessScope，避免先生成通用答案再做后置过滤。
- 实现受控的 **ProjectLens Tool Envelope**，向 Agent 暴露 `search_context`、`read_project_file`、`query_graph`、`authorized_evidence`、`list_knowledge_gaps` 五类只读工具；所有调用统一经过 ACL、路径白名单、工具白名单和审计记录，禁止直接访问知识库、图谱存储或文件系统。
- 将飞书项目自然语言问题接入可注入的 LLM/tool loop，保留 `/project` 作为 debug/smoke 入口；设计 HermesLoopRunner 边界，避免 Feishu adapter 承担项目业务逻辑，并在运行时保留 tool call、citation、evidence ref、audit ref 和 `allow_apply=false`。
- 设计 **Evidence-first Answer Contract** 和 CitationVerifier：将回答拆分为 facts、inferences、unknowns、next actions 和 citations；无证据支持的事实自动降级为 unknown/hypothesis，降低模型幻觉和跨项目泄露风险。
- 设计 L0-L5 五层上下文机制：不可压缩权限锚点、近期对话、滚动摘要、任务 Scratchpad、按需证据和人工审批后的长期 ProjectMemory；通过确定性压缩保留 traceback、文件路径、commit、evidence 等关键引用。
- 建立只读优先的安全边界和回放测试：Apply、PR、deploy、rollback、restart 等写操作默认关闭，工具调用和上下文装配可审计、可验证、可回放。

### 技术栈

`Python 3.12` `FastAPI` `Pydantic v2` `Feishu Webhook` `MCP/Plugin Tooling` `OpenAI-compatible Tool Calling` `BM25/Hybrid Retrieval` `Graph Evidence` `SQLite` `pytest` `ruff`

### 当前验证情况

- 已完成 ProjectSpace/RolePolicy、飞书自然语言入口、ProjectLens 只读工具目录、MCP/插件发现与调用、Hermes 风格 tool-loop bridge 的接口和测试闭环。
- 定向回归测试：`69 passed`；静态检查：`ruff check src tests` 通过。
- 真实模型调用依赖运行环境中的 Hermes provider 和模型 API 配置；本地测试使用 Fake runner/Stub provider 验证工具选择、权限和证据契约。

## 三、最终版简历升级版本

待真实模型、项目连接器和生产治理能力完成后，可以将项目描述升级为：

> 构建面向多项目团队的可插拔 Project Agent：以通用 Agent Runtime 承载多轮对话和自主工具循环，以 ProjectLens Kernel 统一管理 ProjectSpace、RAG/GraphRAG、代码与文档证据、角色策略、项目记忆和引用校验。Agent 能根据自然语言问题自主选择检索代码、文档、提交、图谱、任务和时间窗口运维信号，并按照用户角色生成不同深度和表达风格的答案；所有事实绑定 Evidence，所有工具调用经过权限、审计和预算控制，写操作遵循 Explain → Propose → Validate → Apply 且默认关闭。

最终版个人工作可补充：

- 完成 Git、飞书文档、任务系统和运维信号的 ProjectSpace connector，实现多来源证据统一建模和时间有效性管理。
- 完成 Hermes session/profile 与 ProjectLens conversation context 的映射，支持多群聊、多项目、多角色的会话恢复。
- 建立检索质量、引用完整性、权限隔离、工具预算和回答可用性的离线评测与线上回放体系。
- 在人工审批和隔离 worktree 下实现代码修复建议、测试验证和 ActionProposal；生产 Apply、发布和回滚仍由审批系统控制。

## 四、面试时的 60 秒介绍

我做了一个叫 ProjectLens 的飞书项目协作 Agent。它不是把几个固定 Skill 拼成问答机器人，而是把项目作为一个可插拔的 ProjectSpace：同一个 Agent 可以切换到不同代码仓库、文档和任务空间。通用 Agent Runtime 负责多轮对话、模型调用和工具循环，ProjectLens Kernel 负责项目边界、角色权限、RAG/图谱检索、Evidence 和引用校验。用户问一个没有预置 Skill 的问题时，模型可以自主选择项目只读工具；工具调用必须经过 ACL 和审计，最终回答中的事实必须有引用，没有依据的内容会被标记为未知。当前先做只读协作问答，代码修改和生产操作保留审批边界。

## 五、如何表述底层 Runtime

### 推荐说法

简历中使用：

> 基于可插拔 Agent Runtime 设计并实现 ProjectLens Kernel 与安全工具适配层。

面试追问时如实说明：

> 我采用了一个开源 Agent Runtime 作为对话和工具循环基座，重点工作不是重写通用 runtime，而是设计 ProjectLens 的项目内核、工具协议、权限边界、证据契约、Feishu 适配和测试治理，并通过 runner/plugin 接口与 runtime 解耦。

### 不建议写法

- “从零实现了完整 Agent Runtime”。
- “自研了 Hermes 的 session、模型调用和工具循环”。
- “系统已经支持生产自动修复、自动部署或自动回滚”。
- “已经接入真实公司全部数据源”或任何没有实际指标支持的性能数字。

不在简历标题中主动突出具体开源项目名称是正常的简历取舍；但被问到基础设施来源时必须准确说明，并重点讲清你对上层架构和安全边界的实质贡献。

## 六、面试追问准备

### 为什么不直接把通用 Agent 接到飞书？

通用 Agent 解决“怎么对话和调用工具”，但不知道哪个项目是当前上下文、哪些文件对当前用户可见、哪些内容是可信证据，也不能天然处理跨项目隔离和引用校验。ProjectLens 的价值是把这些项目知识和治理能力封装成可插拔 Kernel，让通用 Agent 在不同项目中安全复用。

### 为什么权限要在生成前生效？

如果先把所有资料交给模型再过滤答案，敏感内容已经进入模型上下文，存在泄露风险。ProjectLens 在工具执行前计算 EffectiveAccessScope，只把允许读取的结果返回给模型；角色差异影响可读数据、工具和回答深度，而不只是影响卡片展示。

### 为什么事实必须引用 Evidence？

项目回答需要可复核。模型可以提出推断，但不能把没有来源的推断写成事实。Verifier 会检查引用是否存在、是否属于当前 ProjectSpace、是否在角色可见范围内，并把无法验证的内容降级为 unknown 或 hypothesis。

### 为什么先关闭写操作？

读错一次通常是低风险，写错代码、发布或重启服务可能造成真实事故。因此先把只读检索、解释和协作问答做成可审计闭环，再按 Explain → Propose → Validate → Apply 逐步开放，并要求人工审批和隔离 worktree。

## 七、简历投递建议

优先投递版本使用“推荐简历版本”，不要把未来规划全部写成已完成成果。面试中可以主动展示最终架构图、工具调用链和一条带 Evidence 引用的真实问答轨迹；这比堆叠“支持几十种 Skill”更能体现 Agent 工程能力。
