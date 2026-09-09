# ProjectLens 上线测试配置指南

更新时间：2026-09-09

本文档只讲可执行 `.env` 配置。产品边界以 [`projectlens-product-document.md`](projectlens-product-document.md) 为准。

相关文件：

- 环境变量模板：`.env.example`
- Settings：`src/project_lens/config.py`
- 飞书联调：[`feishu-setup.md`](feishu-setup.md)
- 项目接入：[`projectspace-onboarding-guide.md`](projectspace-onboarding-guide.md)

---

## 1. 硬约束（所有试点共用）

```text
Hermes 负责意图、模型与工具循环；ProjectLens 负责权限、证据、校验与审计。
没有 Evidence 的内容不能作为已确认事实输出。
ProjectMemory / 正式知识写入必须审批。
Apply / 自动 PR / deploy / rollback / restart 关闭。
API key 与 Feishu secret 只放本机 .env，不进仓库、不进文档正文。
pilot / production 的 Project Agent 与 Hermes 内部 API 必须配置 service token。
```

默认应保持：

```text
PROJECT_LENS_AGENT_MODE=hermes
PROJECT_LENS_MODEL_PROVIDER=stub
PROJECT_LENS_MODEL_LIVE=false
PROJECT_LENS_MODEL_FALLBACK_TO_STUB=true
PROJECT_LENS_CONVERSATION_STORE=sqlite
PROJECT_LENS_DATABASE_PATH=<持久化文件路径，不是 :memory:>
PROJECT_LENS_FEISHU_HERMES_ADVANCED_TOOLS=false
PROJECT_LENS_FEISHU_HERMES_RISK_REVIEW_ENABLED=false
PROJECT_LENS_FEISHU_HERMES_EXECUTION_ENABLED=false
```

---

## 2. 快速开始

```powershell
Copy-Item .env.example .env
# 编辑 .env：至少设置 FEISHU_VERIFICATION_TOKEN；接 Hermes 时再配模型与 service token

py -3.12 -m pip install -e ".[dev]"
py -3.12 -m ruff check src tests
py -3.12 -m pytest -q
py -3.12 -m uvicorn project_lens.main:app --reload
```

健康检查：

```text
GET /api/v1/health
GET /api/v1/ready
GET /api/v1/release-gate
GET /api/v1/integrations/feishu/status
GET /docs
```

飞书回调：

```text
POST /api/v1/feishu/events
```

---

## 3. 配置剖面

### V0.1 本地 Stub（默认）

目标：不接真实大模型，验证 ProjectSpace、权限、飞书录制链路与只读工具边界。

| 变量 | 建议值 |
|---|---|
| `PROJECT_LENS_AGENT_MODE` | `hermes` |
| `PROJECT_LENS_DATABASE_PATH` | `project_lens.db` 或绝对路径 |
| `PROJECT_LENS_CONVERSATION_STORE` | `sqlite` |
| `PROJECT_LENS_MODEL_PROVIDER` | `stub` |
| `PROJECT_LENS_MODEL_LIVE` | `false` |
| `PROJECT_LENS_LOCAL_PROJECT_REGISTRY` | 使用 `.env.example` 中的 demo 项目 |
| `PROJECT_LENS_FEISHU_APP_ID` / `SECRET` | 可留空（recording messenger） |

可问（MVP 场景）：

```text
介绍一下这个项目
项目当前状态怎么样
这个需求现在做到哪一步了
负责人是谁，阻塞点在哪
知识库还缺什么
```

自动化验收：

```powershell
py -3.12 -m pytest tests/test_feishu_integration.py tests/test_feishu_http_and_acl.py tests/test_hermes_runtime.py tests/test_feishu_answer_views.py -q
```

### V0.2 Hermes Safe Live（本地）

目标：验证真实模型工具循环、timeout / retry / fallback / audit；不对真实用户开放。

在 V0.1 基础上配置 OpenAI 兼容网关，并打开 Hermes 模型：

```text
PROJECT_LENS_AGENT_MODE=hermes
PROJECT_LENS_MODEL_PROVIDER=openai
PROJECT_LENS_MODEL_LIVE=true
PROJECT_LENS_MODEL_OPENAI_API_KEY=
PROJECT_LENS_MODEL_OPENAI_BASE_URL=
PROJECT_LENS_MODEL_OPENAI_MODEL=gpt-4o-mini
PROJECT_LENS_MODEL_FALLBACK_TO_STUB=true
PROJECT_LENS_SERVICE_TOKEN=<本地随机长串>
PROJECT_LENS_FEISHU_HERMES_PROVIDER=openai
PROJECT_LENS_FEISHU_HERMES_MODEL=gpt-4o-mini
# 可选：PROJECT_LENS_FEISHU_HERMES_BASE_URL / API_KEY（空则复用 MODEL_OPENAI_*）
```

观察：

- 回答区分事实 / 推断 / 冲突 / 未知 / 下一步
- 无证据内容不会升格为已确认事实
- provider 失败可 fallback，飞书链路不崩
- 写操作与 Apply 仍关闭

关闭 live：

```text
PROJECT_LENS_MODEL_PROVIDER=stub
PROJECT_LENS_MODEL_LIVE=false
```

### V0.3 飞书测试群灰度

目标：2–5 人测试群，绑定**一个**真实或半真实项目，只读问答。

额外要求：

| 变量 | 说明 |
|---|---|
| `PROJECT_LENS_FEISHU_VERIFICATION_TOKEN` | 与飞书后台一致 |
| `PROJECT_LENS_FEISHU_SIGNING_SECRET` | 建议开启 |
| `PROJECT_LENS_FEISHU_APP_ID` / `APP_SECRET` | 真实 outbound |
| `PROJECT_LENS_FEISHU_PROJECT_BINDINGS` | 测试群 → 唯一项目 |
| `PROJECT_LENS_LOCAL_PROJECT_REGISTRY` | 注册同一项目 |
| `PROJECT_LENS_SERVICE_TOKEN` | 必须配置 |
| Hermes 模型 | 稳定后再开 Safe Live |

群置顶建议文案见 [`feishu-setup.md`](feishu-setup.md)。

---

## 4. 环境变量一览

| 变量 | 默认 | 说明 |
|---|---|---|
| `PROJECT_LENS_ENV` | `development` | 运行环境标签 |
| `PROJECT_LENS_LOG_LEVEL` | `INFO` | 日志级别 |
| `PROJECT_LENS_API_PREFIX` | `/api/v1` | HTTP 前缀 |
| `PROJECT_LENS_DATABASE_PATH` | `project_lens.db` | SQLite；试点勿用 `:memory:` |
| `PROJECT_LENS_CONVERSATION_STORE` | `sqlite` | `sqlite` \| `memory` |
| `PROJECT_LENS_AGENT_MODE` | `hermes` | 生产决策运行时 |
| `PROJECT_LENS_MODEL_PROVIDER` | `stub` | `stub` \| `openai` \| … |
| `PROJECT_LENS_MODEL_LIVE` | `false` | 是否允许真实网络推理 |
| `PROJECT_LENS_MODEL_FALLBACK_TO_STUB` | `true` | 失败回退 stub |
| `PROJECT_LENS_MODEL_OPENAI_API_KEY` | 空 | OpenAI 兼容 key |
| `PROJECT_LENS_MODEL_OPENAI_BASE_URL` | 空 | 兼容网关 |
| `PROJECT_LENS_MODEL_OPENAI_MODEL` | `gpt-4o-mini` | 模型名 |
| `PROJECT_LENS_SERVICE_TOKEN` | 空 | pilot/production 内部 Bearer |
| `PROJECT_LENS_HERMES_REPO` | 空 | 可选 Hermes 仓库路径 |
| `PROJECT_LENS_FEISHU_HERMES_PROVIDER` | `openai` | Hermes 模型提供方 |
| `PROJECT_LENS_FEISHU_HERMES_MODEL` | 见 `.env.example` | Hermes 模型 |
| `PROJECT_LENS_FEISHU_HERMES_BASE_URL` / `API_KEY` | 空 | 空则复用 MODEL_OPENAI_* |
| `PROJECT_LENS_FEISHU_HERMES_ADVANCED_TOOLS` | `false` | 高级提案/校验工具 |
| `PROJECT_LENS_FEISHU_HERMES_RISK_REVIEW_ENABLED` | `false` | 风险后台复查 |
| `PROJECT_LENS_FEISHU_HERMES_EXECUTION_ENABLED` | `false` | 隔离执行（默认关） |
| `PROJECT_LENS_FEISHU_VERIFICATION_TOKEN` | local | 回调校验 |
| `PROJECT_LENS_FEISHU_SIGNING_SECRET` | 空 | 可选 HMAC |
| `PROJECT_LENS_FEISHU_APP_ID` / `SECRET` | 空 | outbound；空则 recording |
| `PROJECT_LENS_FEISHU_PROJECT_BINDINGS` | 空 JSON | 群 ↔ 项目 |
| `PROJECT_LENS_LOCAL_PROJECT_REGISTRY` | 空 | 本地项目注册 |
| `PROJECT_LENS_FEISHU_DOC_SYNC_ON_STARTUP` | `false` | 启动同步文档 |
| `PROJECT_LENS_FEISHU_DOC_SYNC_INTERVAL_SECONDS` | `0` | 周期同步；0=关闭 |
| `PROJECT_LENS_GITHUB_TOKEN` | 空 | 只读 GitHub；公库可空 |
| `PROJECT_LENS_EMBEDDING_PROVIDER` | `none` | 语义记忆；默认关 |

完整示例见仓库根目录 `.env.example`。

---

## 5. 上线前 Checklist

- [ ] `PROJECT_LENS_AGENT_MODE=hermes`
- [ ] `PROJECT_LENS_DATABASE_PATH` 持久化，非 `:memory:`
- [ ] `PROJECT_LENS_CONVERSATION_STORE=sqlite`
- [ ] Feishu verification / signing / app id+secret 已配置（V0.3）
- [ ] bindings 已绑定测试群与唯一项目
- [ ] ProjectSpace manifest 含 `members` 与 `public_sources`
- [ ] `PROJECT_LENS_SERVICE_TOKEN` 已配置（pilot/production）
- [ ] 默认 stub；仅灰度时 `MODEL_LIVE=true` + Hermes 模型
- [ ] API key 未写入代码与文档
- [ ] ruff + pytest 通过

功能向手测（对齐产品 MVP）：

- [ ] 项目介绍 / 新人理解
- [ ] 项目当前状态
- [ ] 需求范围与实现状态核对
- [ ] 负责人与阻塞
- [ ] 带来源回答；冲突或未知被显式标出
- [ ] 不同角色或群聊可见性符合策略

---

## 6. 常见问题

**Q: 配了 OpenAI key 但没调用？**  
A: 需要同时 `MODEL_PROVIDER=openai` 且 `MODEL_LIVE=true`；Hermes 路径还需配置 `FEISHU_HERMES_*`（或复用 OpenAI 兼容项）。

**Q: 飞书没回消息？**  
A: 未配 App ID/Secret 时只走 recording adapter。真实群需要 outbound 凭证、bindings 与 URL verification。

**Q: 会话追问丢上下文？**  
A: 确认 `CONVERSATION_STORE=sqlite` 且 `DATABASE_PATH` 指向同一文件。

**Q: 会不会自动改代码或上线？**  
A: 不会。产品非目标与代码默认关闭 Apply / PR / deploy / rollback / restart。

**Q: 还要不要看旧的 grey-release / read_agent 文档？**  
A: 不要。生产决策路径以 Hermes + 本指南 + [`feishu-setup.md`](feishu-setup.md) 为准。
