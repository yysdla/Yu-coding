# ProjectLens 软件研发项目助手 MVP 需求说明

版本：v0.2  
日期：2026-08-20  
产品入口：飞书  
目标用户：软件研发项目团队

## 1. 产品定义

ProjectLens 是一个运行在飞书中的软件研发项目全生命周期助手。

它围绕一个或多个项目群工作，读取飞书项目、多维表格、文档、会议纪要、群聊以及 GitHub 数据，为团队提供：

1. 自然语言项目问答；
2. 项目风险和阻塞的主动提醒；
3. 基于证据的项目状态解释；
4. 面向不同角色的权限控制和回答表达；
5. 风险确认、纠正、延期和升级闭环。

MVP 只读优先。机器人可以提出建议、生成待办或请求确认，但不自动修改代码、创建 PR、发布、回滚或重启生产服务。

## 2. 已确认的产品范围

### 2.1 使用入口

- 普通用户只需要使用飞书私聊或项目群，不需要记忆 `/project` 命令。
- 一个企业安装一个 ProjectLens，可以服务多个项目群。
- 每个项目群绑定一个独立的 `ProjectSpace`。
- 私聊机器人时，先识别用户可访问的项目；如果存在多个项目，要求用户选择或根据上下文确认。

### 2.2 数据来源


| 来源     | 主要职责                          |
| ------ | ----------------------------- |
| 飞书项目   | 任务、负责人、开始/截止时间、状态、依赖、执行进度     |
| 飞书多维表格 | 需求池、优先级、里程碑、项目总览、业务目标         |
| 飞书文档   | 方案、设计、验收标准、规范、项目说明            |
| 飞书会议纪要 | 讨论结论、决策、待确认事项、变更原因            |
| 飞书群聊   | 实时进展、临时阻塞、协作信号、状态冲突信号         |
| GitHub | 仓库、分支、Commit、PR、评审、CI、测试和发布证据 |


正式状态不能被群聊或模型自动覆盖。聊天和会议内容只能作为证据或风险信号，发现冲突时必须让负责人确认。

### 2.3 MVP 主动提醒

提醒策略：

- 高风险事件：立即私聊负责人；
- 普通风险：每天固定时间汇总给项目负责人；
- 未确认风险：按照负责人选择的时间再次提醒；
- 严重风险：在私聊中确认后，才允许升级给项目经理或项目群。

首批风险类型：

1. 任务即将逾期或已经逾期；
2. 前置任务或跨团队依赖阻塞；
3. 需求变更后，研发任务和验收标准没有同步；
4. 代码进度与任务状态不一致；
5. PR 长时间无人评审或测试失败；
6. 群聊中出现“等待、卡住、无法推进”等明确阻塞信号。

## 3. 核心用户体验

### 3.1 项目群问答

用户在项目群中自然提问，例如：

> 这次需求变更会影响哪些模块？现在最大的风险是什么？

机器人应返回：

- 一句话结论；
- 业务或技术影响；
- 已确认事实；
- 推断和不确定项；
- 建议下一步；
- 可折叠的证据来源；
- 当前项目、当前角色和权限范围的摘要。

### 3.2 负责人私聊提醒

提醒必须包含：

- 风险标题和等级；
- 为什么判断为风险；
- 可能影响的任务、需求、模块或里程碑；
- 证据来源和时间；
- 负责人可执行的操作。

MVP 操作：

- 确认风险；
- 不是风险；
- 稍后提醒；
- 更新当前进展；
- 请求协助或升级。

负责人也可以直接用自然语言回复。机器人要把回复转换为结构化反馈，并保留原始证据和操作记录。

### 3.3 每日风险摘要

每日摘要只发送给项目负责人或项目管理角色，内容包括：

- 当前待确认风险数量；
- 高风险和跨团队阻塞；
- 风险负责人；
- 变化趋势；
- 今日需要确认的事项；
- 已被判定为误报或暂缓的事项。

## 4. 角色识别与权限模型

### 4.1 设计原则

角色识别、权限判断和回答风格必须分成三个层次：

```text
可信身份
-> 项目成员角色
-> 私聊/群聊可见范围
-> 本次有效访问范围
-> 允许使用的工具和证据
-> 回答内容与表达风格
```

模型不能自行判断用户身份，也不能接受用户在消息中声明的 `role`、`permissions`、`tenant_id` 或 `decided_by` 作为授权依据。

### 4.2 身份识别来源

优先级从高到低：

1. 飞书事件中的可信 `tenant_key`、`chat_id`、`open_id/user_id`；
2. 企业目录或 SSO 映射的内部员工身份；
3. 项目成员目录或项目角色配置；
4. 项目管理员经过审批的临时角色覆盖；
5. 未识别身份只能进入拒绝或公开资料模式，不能自动获得完整项目读取权限。

身份识别结果必须记录：

- tenant；
- user/actor；
- chat；
- project；
- 身份来源；
- 角色来源；
- 生效时间和过期时间；
- 策略版本。

### 4.3 私聊和群聊的区别

#### 私聊

私聊可以提供更详细的回答，但仍受用户项目权限限制。

```text
Feishu user identity
∩
User project role
∩
Selected ProjectSpace
=
Private effective scope
```

私聊默认可以使用该用户角色允许的完整只读工具，但不得突破项目文件白名单、敏感来源和数据分级策略。

#### 群聊

群聊必须额外应用聊天可见策略，防止在混合群中泄露技术细节、个人信息或敏感事故信息。

```text
Feishu user identity
∩
User project role
∩
Project chat binding
∩
Chat visibility policy
=
Group effective scope
```

群聊中需要同时处理两个不同问题：

1. **这条回答允许在当前群展示什么**：由群聊可见策略控制；
2. **这条回答应该怎样对当前提问者表达**：由当前发言者在该项目中的角色控制。

因此，群聊回答不是统一的“群角色答案”，而是：

```text
回答内容范围 = 当前提问者权限 ∩ 当前群可见范围 ∩ 项目策略
回答表达方式 = 当前提问者角色风格，并受群聊风格上限约束
```

例如，在同一个项目群里询问“支付需求现在有什么风险”：

- 产品经理提问：先回答用户影响、需求范围、进度、阻塞和需要谁确认；
- 开发人员提问：先回答关联模块、PR、CI、依赖和技术处理路径；
- 测试人员提问：先回答验收标准、回归范围、测试失败和未覆盖场景；
- 项目经理提问：先回答风险等级、里程碑影响、负责人和决策点。

这几种回答使用同一份已验证事实和证据，只改变信息排序、深度、术语和建议动作。

当群内另一个角色继续追问时，Agent 必须重新解析本次发言者的角色，切换回答风格，不能沿用上一位成员的角色上下文。

群聊的有效权限永远不能高于用户私聊权限。即使提问者本人有更高权限，也不能在群里展示超出群可见范围的内容。需要敏感细节时，Agent 应提示转到私聊继续，而不是在群里直接展开。

#### 群聊中的发言者识别

每条群消息都必须独立解析：

```text
tenant_key + chat_id + sender_open_id
-> ProjectSpace
-> sender project role
-> ChatVisibilityPolicy
-> EffectiveAccessScope
-> role-aware answer style
```

会话需要保存项目和问题上下文，但不能把上一位发言者的角色、权限或个性化风格继承给下一位发言者。

如果一条消息同时 `@` 多名成员，角色识别仍以实际发送者为主；被 `@` 成员只作为协作对象，不能把其权限授予发送者。

如果无法识别发送者在当前项目中的角色：

- 不允许模型根据称呼、发言内容或技术术语猜测角色；
- 使用 `guest` 或项目配置的公开回答策略；
- 提示项目管理员补充成员角色映射；
- 不展示代码、事故、人员或敏感项目细节。

### 4.4 角色定义


| 角色                  | 主要目标            | 默认可读内容                  | 回答风格                        |
| ------------------- | --------------- | ----------------------- | --------------------------- |
| `developer` 开发      | 理解代码、变更和技术风险    | 代码、架构、PR、CI、任务、文档       | 具体、技术化、给出模块、文件、变更和排查路径      |
| `ops` 值班/SRE        | 定位故障、判断影响和负责人   | 运行信号、事故、发布、代码、任务、处理记录   | 先给影响和当前状态，再给证据、时间线和处理建议     |
| `qa` 测试             | 判断回归范围和验收风险     | 需求、验收标准、任务、PR、CI、缺陷、文档  | 强调影响范围、测试建议、未覆盖场景和验收条件      |
| `product` 产品/业务     | 理解业务影响、范围和协作动作  | 需求、里程碑、任务摘要、验收标准、会议决策   | 少讲实现细节，强调用户影响、进度、风险、负责人和下一步 |
| `manager` 项目负责人/管理者 | 获得风险、阻塞、资源和决策信息 | 项目摘要、风险、依赖、里程碑、负责人、状态趋势 | 结论优先，突出严重度、影响、责任人、截止时间和决策点  |
| `onboarding` 新成员    | 快速理解项目和学习路径     | 项目介绍、模块地图、术语、公开文档、推荐阅读  | 用背景、术语解释、模块关系和学习顺序表达，避免敏感细节 |
| `guest` 未知用户        | 仅获得有限公开信息       | 公开项目介绍和已批准的公开文档         | 简短说明访问范围，不展示内部代码、事故或人员信息    |


角色不是互斥的组织职务，而是**项目内的访问与表达策略**。同一个人在不同项目中可以拥有不同角色。

### 4.5 多角色用户

一个用户可能同时是开发和项目负责人。处理规则：

- 权限使用项目管理员批准的角色集合交集或显式主角色，不由模型推断；
- 用户可以要求“用产品视角解释”，这只能改变表达风格，不能扩大权限；
- 如果用户需要更高权限，必须通过项目管理员或企业目录变更角色；
- 机器人应在敏感场景中说明“当前按哪个项目角色回答”。

### 4.6 群聊个性化回答规则

群聊里的个性化发生在每一条回复上，而不是为整个群固定一种角色。


| 当前发言者    | 首屏优先内容                 | 默认省略或折叠内容    | 推荐动作                    |
| -------- | ---------------------- | ------------ | ----------------------- |
| 开发       | 根因、模块、PR、CI、依赖、技术未知项   | 业务背景和管理汇总    | 查看代码证据、推进评审、修复测试、确认技术方案 |
| 值班/SRE   | 当前影响、时间线、最近变更、负责人、止损建议 | 长篇需求背景       | 联系负责人、核实发布、继续调查、升级事故    |
| 测试       | 验收条件、回归范围、失败用例、环境差异    | 低层实现细节       | 补充测试、确认验收、阻断发布、请求开发协助   |
| 产品/业务    | 用户影响、需求范围、进度、阻塞、待确认事项  | 文件路径、堆栈、底层实现 | 确认范围、补验收标准、协调负责人、调整优先级  |
| 项目经理/管理者 | 风险等级、里程碑、责任人、截止时间、决策点  | 具体代码和日志正文    | 指派负责人、协调资源、确认升级、调整计划    |
| 新成员      | 背景、术语、模块关系、负责人、推荐阅读    | 敏感事故和复杂实现细节  | 阅读资料、联系模块负责人、继续按模块追问    |


回答生成过程：

```text
同一份 Evidence Ledger
-> 同一份 ProjectAnswer 事实核心
-> 根据当前发言者选择 AudienceView
-> 应用群聊可见范围和脱敏规则
-> 生成本条飞书回复
```

不能为不同角色重新“编造一套事实”。如果产品和开发在同一个群里先后询问同一问题，两次回答的事实、状态和引用必须一致。

## 5. 角色策略数据结构

现有 `ProjectMemberRolePolicy` 和 `ChatVisibilityPolicy` 继续作为核心模型，目标字段如下：

```text
ProjectMemberRolePolicy
- actor_id
- project_id
- role
- readable_sources
- allowed_tools
- forbidden_sources
- answer_depth
- answer_style
- evidence_visibility
- escalation_rules
- expires_at
- policy_version
```

```text
ChatVisibilityPolicy
- tenant_key
- chat_id
- project_id
- visibility_level
- allowed_roles
- readable_sources
- allowed_tools
- forbidden_sources
- answer_depth
- answer_style
- allow_private_details
- policy_version
```

```text
EffectiveAccessScope
- actor_id
- chat_id
- project_id
- resolved_role
- readable_sources
- allowed_tools
- forbidden_sources
- answer_depth
- answer_style
- visibility_level
- identity_source
- policy_version
```

计算原则：

```text
effective_scope = user_role_scope ∩ chat_visibility_scope ∩ project_policy_scope
```

权限交集必须在检索前执行。答案生成后只能做格式化和脱敏复核，不能用卡片过滤代替权限控制。

## 6. 回答风格契约

所有角色共享同一份事实核心，但输出结构不同：

```text
ProjectAnswer
- conclusion
- facts
- inferences
- unknowns
- impact
- next_actions
- citations
- visibility_scope
- policy_used
```

角色只影响：

- 信息深度；
- 术语解释程度；
- 重点排序；
- 推荐下一步；
- 是否展示技术细节。

角色不能影响：

- 事实是否成立；
- 项目和证据来源；
- 引用内容；
- 权限边界；
- 未知项是否被隐藏。

## 7. 冲突与拒答规则

以下情况必须拒绝或降级回答：

1. 无法确认飞书用户或租户身份；
2. 群聊没有绑定 ProjectSpace；
3. 用户角色不在该群允许角色中；
4. 证据来源不在当前有效访问范围；
5. 用户要求读取敏感来源或执行写操作；
6. 同一问题的关键事实互相冲突且没有负责人确认；
7. 模型生成的内容没有对应 Evidence。

拒答应该说明：

- 当前识别到的项目或身份范围；
- 缺少什么授权或资料；
- 用户可以联系谁或下一步怎么申请；
- 不泄露被拒绝来源的内容。

## 8. 主动风险提醒中的角色处理

- 风险首先发给结构化责任人，而不是直接发群；
- 责任人的角色决定提醒中展示的证据深度；
- 产品角色收到业务影响和协作动作，不默认看到完整代码或日志；
- 开发角色收到 PR、文件、CI 和模块关联；
- 项目负责人收到严重度、里程碑影响、责任人和升级建议；
- 群升级前必须重新执行群聊可见策略；
- 负责人反馈“不是风险”后，记录反馈原因并降低重复提醒；
- 反馈不能自动改变正式项目状态，除非后续经过明确授权的写入流程。

## 9. MVP 验收标准

### 身份与权限

- 未知用户默认拒绝或只能访问公开资料；
- 请求体不能覆盖服务端解析出的用户、租户、角色和权限；
- 同一用户在不同项目中可以拥有不同角色；
- 私聊和群聊使用不同的有效访问范围；
- 群聊回答不超过最小共同可见范围；
- 所有工具调用都记录 actor、project、chat、role 和 policy version。

### 体验与质量

- 用户不需要输入 `/project` 才能提问；
- 事实、推断、未知、建议和引用可区分；
- 角色只改变表达，不改变事实核心；
- 高风险即时私聊，普通风险每日汇总；
- 风险支持确认、误报、稍后提醒、进展更新和请求协助；
- 用户反馈可被保存并进入后续评测和知识修订流程。

### 安全边界

- MVP 不自动 Apply、PR、部署、回滚或重启；
- 未经确认不把群聊或会议纪要状态写回正式任务系统；
- 所有敏感来源访问均经过 ACL 和项目白名单；
- 不能用“角色卡片过滤”替代生成前权限控制。

## 10. 后续演进

### MVP 之后

- 接入企业通讯录、SSO/SCIM 和项目成员组；
- 增加管理员接入向导、权限预览、索引进度和连接器健康状态；
- 增加有用/无用、错误、资料过期、引用不相关等反馈入口；
- 将负反馈转成知识修订任务；
- 引入持久任务队列、PostgreSQL、审计导出和成本配额。

### 长期

- 会议总结和决策沉淀；
- 自动生成协作待办和跟进计划；
- 经过审批后更新飞书任务或多维表格；
- 受控生成 patch plan、测试计划和 PR 建议；
- 在权限、审批、回滚和审计完整后，再评估有限写操作。

## 11. 一句话产品原则

> ProjectLens 必须先知道“谁在什么项目、什么聊天场景里提问，以及他能看什么”，再决定“查什么、怎么回答、是否主动提醒”；同一事实可以换一种说法，但不能因为角色不同而变成另一套事实。

---

# 12. Cursor 实施计划

本章是给 Cursor 或其他开发 Agent 执行的工程规格。必须按照阶段顺序执行，不得跳过前置阶段直接开发主动提醒、真实模型或写操作。

## 12.1 开发总原则

### 执行方式

每个任务必须遵循：

```text
读取现有实现
-> 先写失败测试
-> 运行失败测试确认测试有效
-> 写最小实现
-> 运行聚焦测试
-> 运行相关回归测试
-> 更新文档和变更记录
-> 提交一个小而完整的 commit
```

### 代码边界

- `src/project_lens/domain/`：跨模块领域契约；不依赖 FastAPI、飞书 SDK 或具体存储。
- `src/project_lens/project_space/`：项目、成员角色、群聊策略、有效访问范围。
- `src/project_lens/context/`：Evidence、索引、检索、数据源和知识状态。
- `src/project_lens/agent/`：只读调查、角色化上下文、草稿和验证。
- `src/project_lens/application/`：应用服务、风险检测、反馈和调度用例。
- `src/project_lens/integrations/feishu/`：飞书事件、身份、卡片、私聊和群聊适配；不得直接读底层索引。
- `src/project_lens/runtime/`：工具网关、策略、安全、事件和执行边界。
- `tests/`：每个新行为必须有单元测试、契约测试或集成测试。

### 绝对禁止

- 不得接受请求体中的 `role`、`permissions`、`tenant_id`、`decided_by` 作为可信授权信息。
- 不得把未知用户默认成拥有完整项目读取权限的 `guest`。
- 不得让 Hermes 直接访问文件系统、SQLite、EvidenceIndex、图存储或飞书 API。
- 不得在未完成身份、权限、审计和回滚设计前打开 Apply、PR、deploy、rollback、restart。
- 不得用“生成完整答案后再按卡片隐藏”替代生成前权限控制。
- 不得为了通过旧测试而恢复旧的固定 Skill 主流程；正式入口仍然是自然语言项目问题。
- 不得在自动化测试中意外调用真实模型、真实飞书或真实 GitHub。

## 12.2 阶段总览与依赖


| 阶段  | 名称        | 目标                          | 依赖    | 必须交付                           |
| --- | --------- | --------------------------- | ----- | ------------------------------ |
| 0   | 基线冻结      | 让代码、配置、测试可重复运行              | 无     | 测试基线、配置隔离、版本记录                 |
| 1   | 可信身份与项目角色 | 每条消息得到可信 actor/project/role | 0     | 身份上下文、角色目录、deny-by-default     |
| 2   | 私聊/群聊有效范围 | 计算用户权限与群可见范围交集              | 1     | EffectiveAccessScope、审计字段、拒答策略 |
| 3   | 角色化问答垂直切片 | 同一问题按不同发言者角色表达              | 2     | 统一事实核心、AudienceView、飞书回复       |
| 4   | MVP 风险识别  | 识别 6 类研发风险和阻塞               | 0、1、2 | 风险规则、证据、去重和状态机                 |
| 5   | 主动提醒闭环    | 高风险即时私聊、普通风险每日摘要            | 4     | 提醒、反馈、延期、升级和审计                 |
| 6   | 真实数据接入    | 飞书项目、多维表格、文档、会议和 GitHub     | 1–5   | 连接器、同步游标、数据新鲜度                 |
| 7   | 试点与生产化    | 让一个真实项目可灰度使用                | 0–6   | 部署、监控、评测、Runbook、灰度报告          |


阶段 3 是第一个必须先跑通的产品垂直切片。阶段 4 和阶段 3 可以在代码层并行，但阶段 5 必须等待阶段 4 的风险数据契约稳定。

---

## 12.3 阶段 0：基线冻结与测试隔离

### 目标

建立可重复的本地开发基线，修复当前新旧 workflow 期望不一致，确保测试永远不会意外调用真实模型或真实外部服务。

### 主要文件

- 修改：`pyproject.toml`
- 修改：`.env.example`
- 修改：`src/project_lens/config.py`
- 修改：`src/project_lens/main.py`
- 修改：`src/project_lens/integrations/feishu/identity.py`
- 修改：`tests/test_pilot_config_defaults.py`
- 修改：`tests/test_api.py`
- 新建：`tests/test_test_mode_isolation.py`
- 新建：`docs/projectlens-change-log.md`

### 具体步骤

#### 0.1 记录当前基线

- 执行 `git status --short`、`git diff --stat`、`git log -5 --oneline`。
- 记录 Python、pytest、FastAPI、Pydantic 版本。
- 执行完整测试并保存首个失败、失败数量和耗时。
- 不得修改测试期望之前先确认失败来自产品行为变化还是代码回归。

#### 0.2 固定测试模式

在 `src/project_lens/config.py` 增加明确配置：

```python
test_mode: bool = False
allow_external_calls: bool = False
```

规则：

- 当 `PYTEST_CURRENT_TEST` 存在或 `test_mode=true` 时，强制 `model_live=false`。
- 测试模式下禁止 HTTP Feishu messenger、GitHub client 和 Hermes live loop。
- 如果测试代码尝试建立外部连接，直接抛出包含服务名的明确异常。

#### 0.3 修复默认身份配置

- `PROJECT_LENS_FEISHU_PROJECT_BINDINGS` 为空时不得自动创建 `demo/chat-1` 默认绑定。
- 仅在显式 `development` 且调用本地 fixture API 时允许 demo fallback。
- 生产、灰度和测试模式下，无绑定直接拒绝。
- `.env.example` 明确区分 `development`、`test`、`pilot`、`production`。

#### 0.4 处理旧测试契约

若旧测试仍断言 `incident_diagnosis`，必须检查当前 `RunService` 是否已经切换到 `project_investigation`。正式行为应统一为：

```text
自然语言 Project Agent -> project_investigation
/project debug workflow -> 保留旧 skill 字段兼容
```

不要通过在所有路径强行写回旧 Skill 名称来掩盖入口分层问题。

#### 0.5 阶段测试

```powershell
py -3.12 -m pytest -q tests/test_pilot_config_defaults.py tests/test_test_mode_isolation.py
py -3.12 -m pytest -q tests/test_api.py::test_health tests/test_api.py::test_create_and_get_run
```

### 阶段 0 验收

- 测试模式不会产生真实模型或飞书请求。
- 空绑定不会在非 development 模式下开放 demo 项目。
- 首个失败测试已定位并有明确兼容策略。
- 运行两次聚焦测试结果一致。
- `docs/projectlens-change-log.md` 记录本阶段配置和行为变化。

---

## 12.4 阶段 1：可信身份、项目成员和角色目录

### 目标

每条飞书消息都能得到不可由用户伪造的 `tenant_key`、`chat_id`、`actor_id`、`ProjectSpace` 和项目角色。

### 主要文件

- 修改：`src/project_lens/integrations/feishu/models.py`
- 修改：`src/project_lens/integrations/feishu/identity.py`
- 修改：`src/project_lens/integrations/feishu/service.py`
- 修改：`src/project_lens/project_space/models.py`
- 修改：`src/project_lens/project_space/policies.py`
- 修改：`src/project_lens/project_space/registry.py`
- 修改：`src/project_lens/project_space/validation.py`
- 修改：`src/project_lens/config.py`
- 新建：`src/project_lens/domain/identity.py`
- 新建：`src/project_lens/project_space/member_directory.py`
- 新建：`tests/test_feishu_identity_context.py`
- 新建：`tests/test_project_member_roles.py`
- 修改：`tests/test_project_space_policies.py`
- 修改：`tests/test_feishu_http_and_acl.py`

### 具体步骤

#### 1.1 定义可信身份对象

在 `src/project_lens/domain/identity.py` 创建不可变模型：

```python
@dataclass(frozen=True)
class ActorContext:
    tenant_key: str
    actor_id: str
    chat_id: str
    chat_type: Literal["p2p", "group"]
    source: Literal["feishu_event", "service_token", "test_fixture"]
    authenticated: bool
```

该对象只能由飞书 ingress 或受信服务创建。HTTP 请求体不得覆盖它。

#### 1.2 扩展飞书事件模型

保证 `sender.sender_id.open_id`、`header.tenant_key`、`event.message.chat_id`、`event.message.chat_type` 在进入应用服务时已解析。缺任一关键字段时：

- 记录拒绝事件；
- 不创建 AgentRun；
- 返回飞书要求的安全错误响应。

#### 1.3 建立项目成员目录

在 `ProjectSpace` 中增加成员声明或成员目录引用。MVP 先支持 JSON manifest：

```json
{
  "members": [
    {"actor_id": "u-dev-1", "roles": ["developer"]},
    {"actor_id": "u-product-1", "roles": ["product"]},
    {"actor_id": "u-manager-1", "roles": ["manager"]}
  ]
}
```

角色必须来自 `RoleKind` 枚举。空角色或非法角色在 ProjectSpace 加载时失败。

#### 1.4 改为 deny-by-default

`default_member_role_policy()` 不得再返回完整 `file_allowlist` 和全部只读工具。改为：

- 未匹配成员：`guest`；
- `guest` 只能读取显式公开来源；
- 未配置公开来源：直接拒绝；
- 不得读取代码、事故、日志、完整任务详情或人员敏感信息。

#### 1.5 禁止请求方自报身份

对 `/api/v1/project-agent/ask`、`/project-agent/tools/call`、memory 和 approval API：

- 保留 body 字段用于本地测试兼容，但在 production/test gateway 中忽略或拒绝；
- 业务服务从 `request.state.actor_context` 获取 actor、tenant 和 chat；
- 若没有可信上下文，返回 401/403，而不是使用 body 中的身份。

#### 1.6 阶段测试

必须新增以下测试：

- 未知用户无法读取内部代码；
- 用户把 body 中 `user_id` 改成管理员 ID 不会提升权限；
- 同一用户在 payment 和 crm 项目拥有不同角色；
- 缺少 sender/open_id 的飞书事件不会创建 run；
- p2p 和 group 的 `chat_type` 被正确保存。

```powershell
py -3.12 -m pytest -q tests/test_feishu_identity_context.py tests/test_project_member_roles.py tests/test_project_space_policies.py tests/test_feishu_http_and_acl.py
```

### 阶段 1 验收

- 每个 AgentRun 都有可信 actor、tenant、chat 和身份来源。
- 未知身份默认拒绝或公开资料模式。
- 角色只来自项目成员目录、企业目录或测试 fixture。
- 请求体中的身份字段无法越权。
- 项目 manifest 错误角色、重复成员和空公开范围会 fail-fast。

---

## 12.5 阶段 2：私聊、群聊与 EffectiveAccessScope

> 实施状态：**已完成（2026-08-26）**。`AgentRun` 已持久化 audit-safe runtime scope；Project Agent/read-agent 在检索、图查询及文件工具执行前强制校验当前消息的 actor/chat/project scope；搜索结果会在进入模型前按来源范围过滤。阶段专项测试 40 passed，关联回归 68 passed。Apply、PR、deploy、rollback、restart 保持关闭。

### 目标

把“用户能看什么”和“当前聊天能展示什么”计算成一个不可绕过的有效范围，并在每次工具调用前使用。

### 主要文件

- 修改：`src/project_lens/project_space/policies.py`
- 修改：`src/project_lens/project_space/models.py`
- 修改：`src/project_lens/runtime/read_gateway.py`
- 修改：`src/project_lens/application/project_agent_tools.py`
- 修改：`src/project_lens/agent/investigation.py`
- 修改：`src/project_lens/agent/read_tools.py`
- 修改：`src/project_lens/domain/models.py`
- 修改：`src/project_lens/runtime/events.py`
- 新建：`tests/test_effective_access_scope.py`
- 新建：`tests/test_group_visibility_intersection.py`
- 修改：`tests/test_read_gateway.py`
- 修改：`tests/test_project_agent_tools_api.py`

### 具体步骤

#### 2.1 明确私聊与群聊策略

在 `ChatVisibilityPolicy` 增加：

```python
chat_type: Literal["p2p", "group"]
allow_private_details: bool = False
public_sources: tuple[str, ...] = ()
```

规则：

- p2p 可以使用用户角色允许的完整范围；
- group 必须再与群的 readable_sources、allowed_tools、forbidden_sources 求交集；
- group 永远不能提升用户权限；
- group 不能展示 private-only Evidence；
- 同一群不同发言者的 `answer_style` 可以不同，但 `visibility_level` 取更保守值。

#### 2.2 扩展 EffectiveAccessScope

增加：

```python
identity_source: str
policy_version: str
chat_type: str
allow_private_details: bool
```

`effective_scope_to_audit_dict()` 必须包含这些字段，但不得包含内容正文、prompt 或 secret。

#### 2.3 在检索网关强制使用 scope

`ReadContextGateway.search_context()`、`read_project_file()`、`query_graph()` 和 `authorized_evidence()` 必须接收 `EffectiveAccessScope`，不能只接收 `ProjectRef`。

每个工具调用按以下顺序执行：

```text
校验 tool_name
-> 校验 scope.allowed_tools
-> 校验 project tenant/project
-> 校验 source readable/forbidden
-> 执行检索或读取
-> 清洗结果
-> 写入 InvestigationLedger
```

#### 2.4 防止上一位用户角色泄漏

每次飞书消息都新建 `MessageAccessContext`。会话只能复用项目和问题上下文，不得复用上一条消息的 actor、role、scope 或 answer style。

#### 2.5 拒答结构

新增统一拒答结果：

```python
AccessDeniedAnswer(
    reason_code="unknown_actor|chat_not_bound|source_forbidden|tool_not_allowed",
    safe_message=str,
    next_step=str,
)
```

拒答中不得透露被拒绝文件、事故或成员详情。

#### 2.6 阶段测试

- 同一用户 p2p 可以读取代码，group 不能读取 private-only 文件；
- 产品角色在群里只能看到业务摘要，开发私聊可以看到 PR 详情；
- 高权限用户在普通群中不能扩大群可见范围；
- 群里换发言人后 scope 和 answer style 都发生变化；
- 工具调用审计包含 actor、project、chat、role、policy_version。

```powershell
py -3.12 -m pytest -q tests/test_effective_access_scope.py tests/test_group_visibility_intersection.py tests/test_read_gateway.py tests/test_project_agent_tools_api.py
```

### 阶段 2 验收

- 没有 scope 的工具调用无法执行。
- 权限检查发生在检索前。
- 群聊不会泄露个人私聊可见内容。
- 每条拒答可解释但不泄露敏感信息。
- 角色切换不会污染会话。

### 阶段 2 实施结果

- `RunService.create()` 将每条消息解析出的 `runtime_access` 保存到 `AgentRun`，不再只写生命周期事件。
- `effective_scope_from_audit_dict()` 校验 tenant、project、actor、chat、role、identity source、policy version 和 chat type，防止 scope 被替换或跨会话重用。
- `ProjectInvestigationAgent` 缺少有效 scope 时 fail closed，并在创建工具注册表前完成校验。
- `ReadContextGateway(require_scope=True)` 用于 Project Agent；旧 deterministic workflow 保留兼容模式。
- `search_context`、`authorized_evidence`、`list_knowledge_gaps`、`query_graph` 在 Evidence 进入 Agent 前执行来源过滤。
- `read_project_file`、`list_project_files`、`grep_project_code`、`search_project_code`、`read_project_file_range` 均执行工具和路径范围校验。
- 生命周期与工具审计包含 actor、project、chat、role、identity_source、policy_version、chat_type，且不保存 Evidence 正文、prompt 或 secret。

---

## 12.6 阶段 3：角色化问答垂直切片

> 实施状态：**已完成（2026-08-26）**。统一事实核心只生成一次，developer/product/qa/manager/ops 通过同一个 `AudienceAnswer` 进行安全排序和展示；API JSON、RoleView Markdown 与飞书卡片共享该投影。阶段验收 34 passed，完整相关回归 153 passed。写操作仍全部关闭。

### 目标

先完成最关键的产品证明：同一个项目群中，产品、开发、测试和负责人分别询问同一问题时，事实和引用一致，但首屏重点、术语和建议不同。

### 主要文件

- 修改：`src/project_lens/domain/models.py`
- 修改：`src/project_lens/agent/draft.py`
- 修改：`src/project_lens/agent/investigation.py`
- 修改：`src/project_lens/agent/verifier.py`
- 修改：`src/project_lens/application/answer_envelope.py`
- 修改：`src/project_lens/application/project_agent_ask.py`
- 修改：`src/project_lens/integrations/feishu/views.py`
- 修改：`src/project_lens/integrations/feishu/cards.py`
- 修改：`src/project_lens/integrations/feishu/service.py`
- 新建：`src/project_lens/application/audience_views.py`
- 新建：`tests/test_role_aware_answering.py`
- 新建：`tests/test_same_facts_different_audience.py`
- 修改：`tests/test_feishu_answer_views.py`
- 修改：`tests/test_feishu_integration.py`

### 具体步骤

#### 3.1 固化统一事实核心

`ProjectAnswer` 增加并固定：

```python
conclusion: str
facts: tuple[Claim, ...]
inferences: tuple[Claim, ...]
unknowns: tuple[str, ...]
impact: tuple[str, ...]
next_actions: tuple[str, ...]
citations: tuple[EvidenceRef, ...]
policy_used: str
```

Verifier 只允许从 Ledger 中认领 citation。角色渲染器不能新增 Claim 或 Evidence。

#### 3.2 建立 AudienceView

创建 `src/project_lens/application/audience_views.py`，提供：

```python
def render_audience_view(
    answer: ProjectAnswer,
    *,
    role: RoleKind,
    chat_type: str,
    scope: EffectiveAccessScope,
) -> AudienceAnswer:
    ...
```

角色排序规则：

- developer：technical impact -> modules/files -> PR/CI -> next actions；
- ops：current impact -> timeline -> recent change -> owner -> mitigation；
- qa：acceptance -> regression scope -> failed cases -> missing coverage；
- product：user impact -> scope -> progress -> blockers -> coordination；
- manager：severity -> milestone impact -> owner -> deadline -> decision.

#### 3.3 角色化系统提示

角色信息必须在 Agent 调查开始前进入 `ContextPrompt`，至少包含：

```text
Current project
Current actor role
Chat type
Readable sources
Forbidden sources
Allowed tools
Answer depth/style
```

不得把整个 ProjectSpace 或隐藏来源列表以会泄露敏感信息的方式发送给模型。

#### 3.4 飞书卡片和纯文本都走同一渲染器

禁止在 `cards.py` 内再次推断角色。`views.py` 或 `audience_views.py` 先生成安全的 `AudienceAnswer`，卡片、纯文本和 API JSON 都只负责展示。

#### 3.5 垂直切片测试

准备同一组 fixture 问题和 Evidence：

```text
问题：RQ-204 的需求变更会影响哪些地方？当前风险是什么？
```

分别以 developer、product、qa、manager 运行，断言：

- `facts`、`citations`、项目 ID 一致；
- 首屏段落关键词符合角色；
- product 不出现未授权文件正文；
- developer 可以出现允许的 PR/模块摘要；
- manager 有风险等级、负责人和截止时间；
- 未确认内容仍在 unknowns，不得被角色渲染隐藏。

```powershell
py -3.12 -m pytest -q tests/test_role_aware_answering.py tests/test_same_facts_different_audience.py tests/test_feishu_answer_views.py tests/test_feishu_integration.py
```

### 阶段 3 验收

- 同一问题不同角色回复明显不同，但事实核心和引用一致。
- 群中每次换发言者都会重新解析角色。
- 角色化渲染器不能新增事实或绕过 ACL。
- 纯文本、卡片和 API 返回使用同一份安全答案。
- 这是第一个允许进入真实内部测试群的产品切片。

### 阶段 3 实施结果

- `ProjectAnswer` 已包含 `conclusion`、`facts`、`inferences`、`impact`、`next_actions`、`citations`、`policy_used`，旧调用通过默认值保持兼容。
- `EvidenceRef` 不携带 Evidence 正文，只保留引用 ID、类型、来源 URI 和安全来源标签。
- Verifier 只允许 Ledger 中存在的 Evidence 成为 FACT citation；结论和影响从通过引用校验的事实派生。
- `render_audience_view()` 是无检索、无文件访问、无模型调用的纯函数，不能新增 Claim 或 Evidence。
- developer 首屏优先技术影响、模块/文件、PR/CI；ops 优先当前影响、时间线、负责人和缓解；qa 优先验收、回归和缺失覆盖；product 优先用户影响、需求范围、进度和阻塞；manager 优先风险、里程碑、负责人和决策点。
- Agent 调查开始前收到安全角色上下文：当前项目、actor role、chat type、readable/forbidden sources、allowed tools、answer depth/style。
- 飞书自动使用当前消息的 actor role；显式角色切换只回放同一 run，不重新调查、不改变真实 actor role 或 ACL。
- API JSON、RoleView Markdown 和飞书卡片共同使用 `AudienceAnswer`，facts、citations、unknowns 在不同角色间保持一致。

---

## 12.7 阶段 4：MVP 风险识别规则引擎

### 目标

从飞书项目、多维表格、群聊、会议纪要和 GitHub Evidence 中识别已确认的 6 类风险，并输出可解释、可去重、可追踪的 RiskFinding。

### 主要文件

- 新建：`src/project_lens/domain/risk.py`
- 新建：`src/project_lens/application/risk_engine.py`
- 新建：`src/project_lens/application/risk_rules.py`
- 新建：`src/project_lens/application/risk_store.py`
- 修改：`src/project_lens/context/engine.py`
- 修改：`src/project_lens/context/models.py`
- 修改：`src/project_lens/runtime/events.py`
- 新建：`tests/test_risk_rules.py`
- 新建：`tests/test_risk_engine.py`
- 新建：`tests/test_risk_deduplication.py`

### 具体步骤

#### 4.1 定义 RiskFinding

```python
class RiskType(StrEnum):
    OVERDUE_TASK = "overdue_task"
    BLOCKED_DEPENDENCY = "blocked_dependency"
    UNSYNCED_REQUIREMENT = "unsynced_requirement"
    CODE_STATUS_MISMATCH = "code_status_mismatch"
    PR_REVIEW_OR_CI = "pr_review_or_ci"
    CHAT_BLOCKER = "chat_blocker"

@dataclass(frozen=True)
class RiskFinding:
    risk_id: str
    project: ProjectRef
    risk_type: RiskType
    severity: Literal["low", "medium", "high"]
    title: str
    summary: str
    owner_ids: tuple[str, ...]
    affected_refs: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    detected_at: datetime
    state: Literal["open", "acknowledged", "snoozed", "dismissed", "resolved"]
```

#### 4.2 实现六条规则

每条规则必须是纯函数或无副作用 evaluator，输入授权 Evidence，输出 0 或多个 Finding：

- `overdue_task_rule`：截止时间早于当前时间且状态不是完成/取消；
- `blocked_dependency_rule`：前置任务或外部依赖为 blocked/waiting，且影响未完成任务；
- `unsynced_requirement_rule`：需求版本时间晚于关联研发/测试任务更新时间，或验收标准缺失；
- `code_status_mismatch_rule`：任务显示进行中/完成，但关联分支、PR、Commit 或 CI 证据不匹配；
- `pr_review_or_ci_rule`：PR 超过配置时长无人评审，或 CI 失败且任务仍准备发布；
- `chat_blocker_rule`：群聊中命中明确阻塞词，并能关联项目、任务或责任人。

规则必须返回证据 ID，不得只返回自然语言判断。

#### 4.3 去重和变化检测

Risk ID 使用稳定指纹：

```text
hash(project_id + risk_type + primary_ref + owner_ids)
```

同一风险再次检测时：

- 证据没有变化：更新 `last_seen_at`，不重复发即时提醒；
- 严重度上升：产生新事件；
- 负责人反馈已处理：转 resolved 或 acknowledged；
- 证据消失或任务完成：转 resolved。

#### 4.4 阶段测试

每条规则至少 3 个测试：命中、边界、不命中。另加：

- 同一 Evidence 多次同步不会产生重复风险；
- 任务状态变为完成后风险自动 resolved；
- 风险严重度升级会产生事件；
- 无责任人的风险必须进入项目负责人队列。

```powershell
py -3.12 -m pytest -q tests/test_risk_rules.py tests/test_risk_engine.py tests/test_risk_deduplication.py
```

### 阶段 4 验收

- 六类风险均能从 fixture Evidence 复现。
- 每个 RiskFinding 至少包含一个可验证证据。
- 重复同步不会重复轰炸用户。
- 风险有稳定状态和状态变更事件。
- 规则不直接修改飞书项目、GitHub 或文档。

### 阶段 4 实施结果

- 已新增 `domain/risk.py`，定义 `RiskType`、`RiskSeverity`、`RiskState`、`RiskFinding`、`RiskStateEvent` 和稳定 SHA-256 风险指纹。
- 风险指纹使用 tenant/project、risk type、primary ref 和排序去重后的 owner IDs；owner 顺序变化不会产生新风险。
- 已实现 6 条无副作用规则：逾期任务、阻塞依赖、需求未同步、代码状态不匹配、PR 评审或 CI、群聊明确阻塞。
- 每条规则只消费调用方传入的 Evidence 和固定 `now`，每个 Finding 至少保留一个真实 Evidence UUID，不调用模型、不读取文件、不修改外部系统。
- 已实现 `RiskEngine`：首次发现、材料变化、严重度升级、人工状态转换、证据重新出现和自动 resolved 均有独立事件。
- 同一 Evidence 重复扫描只刷新 `last_seen_at`，不产生事件，也不进入 `notify_risk_ids`；严重度升级和材料变化才重新进入通知候选。
- 无 owner 的 Finding 使用 `routing_queue=project_owners`，不猜测具体负责人。
- 已实现内存与 SQLite RiskStore；`risk_findings` 和 `risk_state_events` 可跨适配器实例恢复。
- `ContextEngine.scan_risks()` 在规则运行前执行 tenant/project/service/environment/permission ACL 过滤。
- 低权限成员的非完整授权快照不会把隐藏 Evidence 对应的既有风险误关闭；后台全量扫描可使用完整快照执行“证据消失即 resolved”。
- TaskRecord/TaskIndexer 已兼容截止时间、负责人、依赖、需求、PR、分支、验收标准和 `requires_code` 风险元数据，旧 fixture 保持可用。
- 阶段 4 规定验收：29 passed；任务索引与 Context 关联回归合计 50 passed；阶段 2/3 关键回归 58 passed；Ruff 全部通过。
- 完整测试套件仍存在项目变更日志已记录的历史基线失败；首个失败为 `test_context_pack.py` 的旧飞书入口 400，与阶段 4 风险链路无关。
- Apply、PR、deploy、rollback、restart 继续全部关闭。

---

## 12.8 阶段 5：主动提醒、反馈与每日摘要

### 目标

高风险即时私聊负责人，普通风险每日摘要；负责人可以确认、误报、稍后提醒、更新进展或请求协助。

### 主要文件

- 新建：`src/project_lens/domain/risk_feedback.py`
- 新建：`src/project_lens/application/risk_notification_service.py`
- 新建：`src/project_lens/application/risk_feedback_service.py`
- 新建：`src/project_lens/application/daily_risk_digest.py`
- 修改：`src/project_lens/integrations/feishu/service.py`
- 修改：`src/project_lens/integrations/feishu/cards.py`
- 修改：`src/project_lens/integrations/feishu/adapter.py`
- 修改：`src/project_lens/persistence/sqlite.py`
- 新建：`tests/test_risk_notifications.py`
- 新建：`tests/test_risk_feedback.py`
- 新建：`tests/test_daily_risk_digest.py`
- 新建：`tests/test_feishu_risk_cards.py`

### 具体步骤

#### 5.1 定义反馈状态机

```python
class RiskFeedbackAction(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    DISMISS = "dismiss"
    SNOOZE = "snooze"
    UPDATE_PROGRESS = "update_progress"
    REQUEST_HELP = "request_help"
```

所有按钮和自然语言回复都必须归一到上述动作，并记录：

```text
risk_id, actor_id, action, reason, snoozed_until, created_at, evidence_refs
```

#### 5.2 通知路由

- 高风险：立即私聊结构化 owner；
- 中低风险：进入每日摘要；
- 无 owner：通知项目负责人，不直接在群点名；
- owner 在群聊无权查看细节：提醒转私聊；
- 升级群前重新计算群聊 scope。

#### 5.3 卡片交互

每张风险卡必须显示：

- 风险标题和严重度；
- 影响对象；
- 2–4 条关键证据；
- 检测时间和数据新鲜度；
- 操作按钮；
- “为什么判断为风险？”追问入口。

按钮处理必须幂等：同一个按钮重复点击不得重复创建反馈或任务。

#### 5.4 每日摘要

每日摘要按项目和负责人聚合：

- 新增高风险；
- 未确认风险；
- 责任人已确认但未解决；
- 今日到期；
- 已解决和误报统计。

不得把完整代码、日志正文或私聊内容复制到群摘要。

#### 5.5 阶段测试

- 高风险只发送给负责人私聊；
- 普通风险只出现在摘要；
- 点击确认风险后不再重复即时提醒；
- snooze 到期后只提醒一次；
- 误报原因可保存；
- 群升级会重新执行可见策略；
- 重复 webhook/button 不产生重复反馈。

```powershell
py -3.12 -m pytest -q tests/test_risk_notifications.py tests/test_risk_feedback.py tests/test_daily_risk_digest.py tests/test_feishu_risk_cards.py
```

### 阶段 5 验收

- 负责人能在飞书内完成完整闭环，不需要打开后台。
- 高风险即时私聊、普通风险每日汇总行为可被测试验证。
- 所有反馈有审计记录。
- 机器人不会因为重复同步或重复点击造成消息风暴。

### 阶段 5 实施结果

- 已新增 `domain/risk_feedback.py`，定义 acknowledge、dismiss、snooze、update_progress、request_help 五类反馈及通知审计记录。
- 卡片按钮与“风险 `<risk_id>` 确认/误报/稍后提醒/更新进展/请求协助”自然语言回复统一进入同一反馈状态机。
- 生产反馈权限固定为结构化 owner 或 ProjectSpace manager；群内其他成员不能修改风险状态。
- 每条反馈保存 risk、actor、action、reason、snoozed_until、created_at 和 Evidence UUID 引用；重复 event/button idempotency key 不会重复写入。
- 已新增 SQLite `risk_feedback` 和 `risk_notifications` 表，反馈审计和通知去重可跨进程适配器实例恢复。
- high/open 风险只通过 `open_id` 即时私聊 owner；medium/low 不即时发送，只进入每日摘要。
- 无 owner 风险路由给 ProjectSpace manager；没有 manager 时记录明确跳过原因，不猜测负责人、不在群中点名。
- 通知发送前原子占用幂等键，防止并发同步重复发送；发送失败释放占位，允许后续重试。
- snooze 到期后由系统恢复 open 并私聊一次；重复调度不会再次提醒，发送失败会恢复 snoozed 以便重试。
- request_help 使用项目飞书群绑定，发送前重新调用 `ProjectRuntimeContextResolver` 计算群聊 scope；无权限时拒绝群升级。
- 已新增安全风险卡：显示严重度、状态、影响对象、Evidence 引用、检测时间和数据新鲜度，不携带 Evidence 正文。
- 风险卡提供确认、误报、明天提醒、更新进展、请求协助和“为什么判断为风险？”入口；群安全卡不提供状态修改按钮。
- 已新增每日摘要，按项目和 owner 聚合新增高风险、未确认、已确认未解决、今日到期、今日解决和今日误报统计。
- 摘要只消费 RiskFinding 安全字段，不复制代码正文、日志正文、私聊消息或 Evidence 内容，并按 owner 私聊投递且每日幂等。
- 阶段 5 规定验收及阶段 4 联动测试：56 passed；Ruff 全部通过；阶段 5 相关 diff check 通过。
- 飞书关联回归中仍有旧测试夹具缺少可信 `open_id` 导致的已知 400 基线；未放宽阶段 1 deny-by-default 身份边界。
- Apply、PR、deploy、rollback、restart 继续全部关闭。

---

## 12.9 阶段 6：真实数据连接器与同步状态

> 实施状态：**已完成 fixture 链路（2026-09-01）**。四类只读 connector + `ConnectorSyncService` + SQLite/内存同步状态 + Evidence revoked/新鲜度告警已落地；CI 用注入 records，不访问真实飞书/GitHub。专项测试 13 passed。真实 HTTP transport 可在试点阶段替换 records 源。写操作仍全部关闭。

### 目标

将 MVP 从本地 fixture 连接到一个真实项目：飞书项目、飞书多维表格、文档/会议纪要和 GitHub，并提供同步游标、失败重试、数据新鲜度和删除传播。

### 主要文件

- 新建：`src/project_lens/context/connectors/base.py`
- 新建：`src/project_lens/context/connectors/feishu_project.py`
- 新建：`src/project_lens/context/connectors/feishu_bitable.py`
- 新建：`src/project_lens/context/connectors/feishu_minutes.py`
- 新建：`src/project_lens/context/connectors/github.py`
- 修改：`src/project_lens/application/feishu_doc_sync.py`
- 修改：`src/project_lens/application/feishu_doc_sync_status.py`
- 修改：`src/project_lens/context/indexing/feishu_documents.py`
- 修改：`src/project_lens/context/store.py`
- 修改：`src/project_lens/project_space/models.py`
- 新建：`tests/test_connector_contracts.py`
- 新建：`tests/test_feishu_project_connector.py`
- 新建：`tests/test_feishu_bitable_connector.py`
- 新建：`tests/test_github_connector.py`
- 新建：`tests/test_sync_freshness.py`

### 具体步骤

#### 6.1 统一 Connector 契约

```python
class Connector(Protocol):
    name: str
    async def health(self) -> ConnectorHealth: ...
    async def sync(self, cursor: SyncCursor | None) -> SyncBatch: ...
```

`SyncBatch` 必须包含新增、更新、删除、下一游标、观察时间和错误信息。

#### 6.2 先实现只读 GitHub

最小范围：仓库元数据、分支、Commit、PR、Review、CheckRun、关联 issue/任务引用。不得实现 merge、write comment、create PR 或关闭 issue。

#### 6.3 实现飞书项目和多维表格

将字段映射为统一 Evidence：

- task ID、title、status、owner、start_at、due_at、updated_at、dependencies；
- requirement ID、priority、milestone、acceptance_criteria、version、updated_at。

映射失败的字段必须进入 connector warning，不得静默丢失。

#### 6.4 新鲜度与删除传播

- 每个 connector 记录 `last_success_at`、`last_attempt_at`、`cursor`、`error_count`；
- 删除源数据时，将 Evidence 标记 revoked，不直接物理删除，保证旧回答可审计；
- 检索默认排除 revoked Evidence；
- 超过配置新鲜度阈值时，答案显示“资料可能过期”。

#### 6.5 阶段测试

所有连接器必须使用 HTTP mock/fixture 测试，不在 CI 访问真实服务。测试：分页、限流、重复同步、游标恢复、删除、权限字段、部分失败。

```powershell
py -3.12 -m pytest -q tests/test_connector_contracts.py tests/test_feishu_project_connector.py tests/test_feishu_bitable_connector.py tests/test_github_connector.py tests/test_sync_freshness.py
```

### 阶段 6 验收

- 一个真实项目可以完成首次 backfill 和增量同步。
- 同步失败可看到原因并可重试。
- 风险规则能使用真实任务、需求和 GitHub 数据。
- 每条回答能显示数据更新时间。
- 连接器没有任何写操作。

### 阶段 6 实施结果

- 已统一 `Connector` / `SyncBatch` / `SyncCursor` / `ConnectorHealth` 契约。
- 已实现 `feishu_project`、`feishu_bitable`、`feishu_minutes`、`github` 四类只读 fixture connector：支持游标恢复与 `deleted` 删除列表；GitHub 写方法显式拒绝。
- Evidence 增加 `revoked` / `revoked_at`；`InMemoryEvidenceIndex.all()` 默认排除 revoked，删除不物理抹除以便审计。
- 已实现 `ConnectorSyncService` 与 SQLite/内存 `ConnectorSyncStateStore`：记录游标、`last_attempt_at` / `last_success_at`、错误计数；失败保留游标可重试。
- 新鲜度阈值由 `connector_freshness_max_age_seconds` 控制，超时返回“资料可能过期”提示。
- 阶段 6 规定验收测试：13 passed；CI 不访问真实飞书或 GitHub。
- 本地继续以 `payment` fixture records 验证链路；真实 HTTP transport 可在试点阶段替换 records 源。
- Apply、PR、deploy、rollback、restart 继续全部关闭。

---

## 12.10 阶段 7：试点、生产化和质量门禁

### 目标

让一个真实软件研发项目在 8–15 人测试群中稳定运行，形成可复制的部署和评测材料。

### 主要文件

- 新建：`Dockerfile`
- 新建：`docker-compose.yml`
- 新建：`.github/workflows/ci.yml`
- 新建：`requirements.lock` 或等价锁定文件
- 新建：`deploy/README.md`
- 新建：`docs/pilot-runbook.md`
- 新建：`docs/security-baseline.md`
- 新建：`docs/evaluation-gate.md`
- 修改：`src/project_lens/api/routes.py`
- 修改：`src/project_lens/persistence/sqlite.py`（MVP 可先增强，不得宣称 HA）
- 修改：`src/project_lens/api/project_agent_routes.py`
- 修改：`src/project_lens/integrations/feishu/status_routes.py`
- 新建：`tests/test_readiness.py`
- 新建：`tests/test_cross_project_isolation.py`
- 新建：`tests/test_concurrency_idempotency.py`

### 具体步骤

#### 7.1 先完成 API 认证边界

在真实试点前必须：

- 接入企业 API gateway/OIDC 或可信服务 token；
- 从认证 claims 创建 ActorContext；
- body 身份字段仅用于 test fixture；
- run、event、memory、approval、tool call 都做资源级授权；
- 未认证请求全部返回 401；无项目权限返回 403。

#### 7.2 健康检查和可观测性

`/health` 只表示进程存活；新增 `/ready` 检查：数据库、项目 registry、索引、连接器状态和模型 provider 配置。

所有日志必须包含：`trace_id`、`run_id`、`tenant_id`、`project_id`、`actor_id`、`role`、`connector`。不得记录 prompt、token、文件正文或 secret。

#### 7.3 并发和幂等

- AgentRun 增加版本或状态 CAS；
- webhook event 使用唯一 event_id；
- 风险通知使用 `risk_id + channel + notification_window` 幂等键；
- 同一个 run 不能同时执行两次；
- 失败任务有明确 retry 次数和终态。

#### 7.4 质量门禁

建立至少 100 条真实匿名问题，覆盖：

- developer、product、qa、manager；
- 群聊和私聊；
- 正常问题、拒答、过期资料、冲突事实、越权尝试；
- 六类风险规则；
- 角色化同事实回答。

发布前必须达到：

- 关键事实引用覆盖率 100%；
- 越权泄漏 0；
- 写操作执行 0；
- 主要问题成功返回率 >= 98%；
- 人工事实正确率 >= 90%；
- 重复通知率 < 1%；
- fallback/degraded 率 < 5%。

#### 7.5 灰度流程

```text
本地 stub
-> staging mock connectors
-> staging safe-live
-> 8–15 人测试群
-> 2 周人工复核
-> 达到质量门槛后扩大项目范围
```

灰度期间：

- 默认只读；
- 每日查看错误、拒答、误报、过期数据和成本；
- 每周将负反馈加入 golden set；
- 任意越权事件立即关闭机器人群绑定。

```powershell
py -3.12 -m pytest -q tests/test_readiness.py tests/test_cross_project_isolation.py tests/test_concurrency_idempotency.py
py -3.12 -m pytest -q
```

### 阶段 7 验收

- 可以从干净环境启动并完成健康检查。
- CI 执行 lint、测试、依赖和 secret scan。
- 一个真实项目能稳定运行两周并导出审计与质量报告。
- 具备停止、回滚配置和禁用群绑定的 Runbook。
- 未达到质量门槛时不得扩展到第二个项目。

---

## 12.11 Cursor 每次执行任务必须输出的内容

Cursor 执行本计划中的任意任务后，必须在回复中报告：

1. 本次完成的任务编号；
2. 修改和新增的精确文件；
3. 新增或修改的接口、数据结构和行为；
4. 运行过的测试命令及结果；
5. 未完成项和风险；
6. 是否改变了权限、飞书事件、模型调用或写操作边界；
7. 下一步只能执行哪个任务。

如果测试失败，不得直接进入下一阶段，必须先定位并修复，或明确记录阻塞原因。

## 12.12 最终端到端验收场景

### 场景 A：同群不同角色回答

1. 将 developer、product、qa、manager 四个用户绑定到同一项目群。
2. 四人分别发送同一问题：`RQ-204 的变更会影响什么？`。
3. 断言四次回答的项目、事实、Evidence ID 一致。
4. 断言首屏重点、术语和下一步分别符合角色。
5. 断言产品回答不泄露未授权代码正文。

### 场景 B：风险发现与私聊闭环

1. 任务截止时间临近，PR 超过评审阈值，CI 有失败。
2. 风险引擎生成一个稳定 RiskFinding。
3. ProjectLens 私聊负责人，展示风险、影响和证据。
4. 负责人点击“确认风险”。
5. 系统记录反馈并停止重复即时通知。
6. 每日摘要显示该风险为“已确认、处理中”。

### 场景 C：群聊敏感信息保护

1. developer 私聊可以读取 PR 详情。
2. 同一 developer 在普通项目群追问完整日志。
3. 群策略禁止日志正文。
4. Agent 返回安全摘要，并提示转私聊。
5. 审计记录拒绝原因，但不记录日志正文。

### 场景 D：需求状态冲突

1. 会议纪要说需求范围已改变。
2. 飞书项目任务仍显示旧范围。
3. Agent 生成“状态冲突”风险，不自动改任务。
4. 私聊负责人确认真实状态。
5. 只有经过明确授权的后续写流程才允许更新正式任务。

## 12.13 完成定义

只有同时满足以下条件，才可以称为 MVP 完成：

- 阶段 0–5 全部通过；
- 阶段 6 至少完成一个真实项目的只读同步；
- 阶段 7 的认证、审计、测试和灰度门槛通过；
- 同群不同角色回答场景通过；
- 风险提醒和反馈闭环通过；
- 跨项目、跨角色、私聊/群聊隔离测试通过；
- 任何 Apply、PR、deploy、rollback、restart 仍然关闭；
- 用户反馈已经可以进入 golden set 和知识修订流程。

在此之前，项目只能称为“内部技术试点”，不能对外宣称为生产级企业项目管理 Agent。
