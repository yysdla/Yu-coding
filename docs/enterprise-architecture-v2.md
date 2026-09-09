# ProjectLens 企业版目标架构

本文描述的不是当前代码的现状图，而是要把 ProjectLens 真正做成企业产品时，每一层最终应该长成什么样。

## 1. 总体架构

```mermaid
flowchart TB
    subgraph Users["使用者"]
        DEV["研发工程师"]
        QA["测试工程师"]
        OPS["值班 / 运维"]
        PM["产品 / 业务"]
        MGR["管理者 / 新人"]
    end

    subgraph Feishu["飞书协作层"]
        CHAT["项目群 / 私聊"]
        CARD["交互卡片"]
        FEEDBACK["反馈与审批"]
    end

    subgraph Edge["企业接入与身份层"]
        GATEWAY["飞书事件网关"]
        IDENTITY["组织身份 / 角色映射"]
        BINDING["群聊 / 项目绑定"]
        POLICY["ACL / RolePolicy"]
    end

    subgraph Runtime["Agent 运行层"]
        LOOP["调查 Agent Loop"]
        PLANNER["问题理解与调查计划"]
        TOOLS["工具调度"]
        SESSION["会话与上下文压缩"]
        FALLBACK["模型失败降级"]
    end

    subgraph Kernel["ProjectLens 内核"]
        PROJECT_SPACE["ProjectSpace Registry"]
        SCOPE["Effective Access Scope"]
        GATE["Read / Tool Gateway"]
        LEDGER["Evidence Ledger"]
        VERIFIER["Citation Verifier"]
        MEMORY["Project Memory"]
    end

    subgraph Knowledge["项目知识层"]
        CONNECTORS["真实系统连接器"]
        INGEST["增量摄取 / 归一化"]
        INDEX["Evidence Index"]
        VECTOR["向量索引"]
        GRAPH["项目知识图谱"]
        RERANK["混合检索与重排序"]
    end

    subgraph Systems["企业数据源"]
        GIT["Git / GitHub / GitLab"]
        DOC["飞书文档"]
        TASK["Jira / Linear / 任务系统"]
        RELEASE["发布系统"]
        OPS_SYS["CI / APM / 日志"]
    end

    subgraph Ops["质量与运营层"]
        EVAL["评测集 / Replay"]
        JUDGE["LLM Judge"]
        OBSERVE["可观测性 / 审计"]
        COST["成本与配额"]
        DASHBOARD["效果与治理看板"]
    end

    Users --> CHAT
    CHAT --> GATEWAY
    GATEWAY --> IDENTITY
    GATEWAY --> BINDING
    IDENTITY --> POLICY
    BINDING --> PROJECT_SPACE
    POLICY --> SCOPE

    LOOP --> PLANNER
    LOOP --> TOOLS
    LOOP --> SESSION
    LOOP --> FALLBACK
    SCOPE --> TOOLS

    TOOLS --> GATE
    GATE --> INDEX
    GATE --> VECTOR
    GATE --> GRAPH
    GATE --> RERANK
    INDEX --> LEDGER
    VECTOR --> LEDGER
    GRAPH --> LEDGER
    RERANK --> LEDGER

    LEDGER --> VERIFIER
    VERIFIER --> MEMORY
    VERIFIER --> LOOP
    LOOP --> CARD
    CARD --> FEEDBACK
    FEEDBACK --> MEMORY
    FEEDBACK --> EVAL

    CONNECTORS --> INGEST
    INGEST --> INDEX
    INGEST --> VECTOR
    INGEST --> GRAPH
    GIT --> CONNECTORS
    DOC --> CONNECTORS
    TASK --> CONNECTORS
    RELEASE --> CONNECTORS
    OPS_SYS --> CONNECTORS

    EVAL --> JUDGE
    JUDGE --> DASHBOARD
    OBSERVE --> DASHBOARD
    COST --> DASHBOARD
```

## 2. 一次项目问题的目标工作流

```mermaid
sequenceDiagram
    autonumber
    actor U as 飞书成员
    participant F as 飞书网关
    participant I as 身份 / 角色解析
    participant A as Agent Runtime
    participant P as ProjectLens Kernel
    participant K as 检索 / 图谱
    participant V as 验证器
    participant M as 记忆与反馈

    U->>F: 在项目群问“下单链路为什么报错”
    F->>I: 解析用户、群聊、企业身份
    I->>P: 绑定 ProjectSpace 和 Effective Scope
    P-->>A: 返回角色、可读范围、可调工具、回答深度
    A->>A: 理解意图并制定调查计划
    A->>P: 请求只读项目工具
    P->>K: 混合检索 + 图谱扩展 + 重排序
    K-->>P: 返回 Evidence 和关系路径
    P->>V: 生成 AnswerDraft 并校验引用
    V-->>A: 返回事实、推断、未知项、建议动作
    A->>F: 生成当前角色适配的回答
    F-->>U: 展示结论、引用、未知项与协作动作
    U->>M: 反馈“引用不对 / 资料不足 / 已解决”
    M->>P: 生成记忆候选或知识缺口
    M->>K: 触发补数、纠正或知识沉淀
```

## 3. 项目知识持续构建流程

```mermaid
flowchart LR
    subgraph Sources["外部系统"]
        S1["代码仓库"]
        S2["飞书文档"]
        S3["任务 / 需求"]
        S4["发布 / 变更"]
        S5["日志 / 指标 / CI"]
    end

    subgraph Pipeline["知识构建管线"]
        C1["连接器与 ACL"]
        C2["增量同步"]
        C3["解析 / 切分 / 实体提取"]
        C4["关系与图谱构建"]
        C5["索引写入"]
        C6["质量校验"]
    end

    subgraph Stores["知识存储"]
        D1["Evidence 事实库"]
        D2["向量索引"]
        D3["项目图谱"]
        D4["项目记忆"]
    end

    subgraph Consumers["消费方"]
        E1["只读工具"]
        E2["调查 Agent"]
        E3["知识缺口检测"]
        E4["评测与回放"]
    end

    S1 --> C1
    S2 --> C1
    S3 --> C1
    S4 --> C1
    S5 --> C1
    C1 --> C2
    C2 --> C3
    C3 --> C4
    C4 --> C5
    C5 --> C6
    C6 --> D1
    C6 --> D2
    C6 --> D3
    D1 --> E1
    D2 --> E1
    D3 --> E1
    D4 --> E2
    E1 --> E2
    E2 --> E3
    E2 --> E4
```

## 4. 每一层最终要长成什么样

| 层级 | 最终状态 | 判断标准 |
| --- | --- | --- |
| 身份与权限 | 企业身份进入运行时，角色不能由客户端自行声明 | 同一用户在项目群和私聊、不同角色得到真实且一致的权限 |
| ProjectSpace | 通过配置接入真实项目，不修改 Agent 代码 | 一个企业项目能在限定时间内完成 onboarding |
| 知识摄取 | 支持 Git、文档、任务、发布、运维信号增量同步 | 新提交和文档变更可被检索和追踪 |
| 检索 | BM25、向量、图谱联合检索，并做重排序 | 黄金问题命中率持续提升，引用不相关率下降 |
| 图谱 | 从代码调用、依赖、变更、事故中构建可解释关系 | 能回答“谁依赖谁、改了哪里、影响哪里” |
| Agent Runtime | 调查计划由模型决定，ProjectLens 控制权限和验证 | 未预置问题也能进入工具循环，而不是走固定模板 |
| 答案生成 | 同一事实核心按角色生成，事实、推断、未知分离 | 技术、业务、管理、新人第一次看到的内容就不同 |
| 飞书协作 | 不只回答，还支持反馈、审批、负责人协同、记忆沉淀 | 用户完成一次问题闭环不需要离开飞书 |
| 评测运营 | 有真实评测集、回放、LLM Judge、成本和效果看板 | 每次改动都能证明是否让产品变得更有用 |
