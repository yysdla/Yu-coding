# ProjectLens 上线测试配置指南

更新时间：2026-08-03

本文档说明如何用 `.env` 跑本地 Stub、Safe Live LLM、飞书测试群灰度。  
产品方向以 `docs/project-agent-final-plan.md` 为准；**这里只讲可执行配置**。

相关文件：

- 环境变量模板：`.env.example`
- Settings 定义：`src/project_lens/config.py`
- 飞书联调：`docs/feishu-setup.md`

---

## 1. 硬约束（所有试点版本共用）

```text
大模型只做表达增强，不决定事实。
Feishu adapter 不直接查知识库、不拼 prompt。
Claim 必须引用 Evidence / GraphEvidence。
ProjectMemory 必须审批。
Apply / 自动 PR / deploy / rollback / restart 关闭。
API key 与 Feishu secret 只放本机 `.env`，不进仓库、不进文档正文。
```

默认应保持：

```text
PROJECT_LENS_MODEL_PROVIDER=stub
PROJECT_LENS_MODEL_LIVE=false
PROJECT_LENS_MODEL_FALLBACK_TO_STUB=true
PROJECT_LENS_CONVERSATION_STORE=sqlite
PROJECT_LENS_DATABASE_PATH=<持久化文件路径，不是 :memory:>
```

---

## 2. 快速开始

```powershell
Copy-Item .env.example .env
# 编辑 .env：至少改 FEISHU_VERIFICATION_TOKEN；灰度时再开 OpenAI

& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pip install -e ".[dev]"
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m ruff check src tests
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest -q
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m uvicorn project_lens.main:app --reload
```

健康检查：

```text
GET /api/v1/integrations/feishu/status
GET /docs
```

飞书回调：

```text
POST /api/v1/feishu/events
```

---

## 3. 配置剖面

### V0.1 本地 Stub 演示（默认）

目标：演示项目介绍、项目地图、变更、缺口、负责人、故障；不接真实大模型。

| 变量 | 建议值 |
|---|---|
| `PROJECT_LENS_DATABASE_PATH` | `project_lens.db` 或绝对路径 |
| `PROJECT_LENS_CONVERSATION_STORE` | `sqlite` |
| `PROJECT_LENS_MODEL_PROVIDER` | `stub` |
| `PROJECT_LENS_MODEL_LIVE` | `false` |
| `PROJECT_LENS_LOCAL_PROJECT_REGISTRY` | 使用 `.env.example` 中的 payment demo |
| `PROJECT_LENS_FEISHU_APP_ID` / `SECRET` | 可留空（recording messenger） |

可问：

```text
介绍一下这个项目
项目地图
最近变更
知识库缺什么
负责人是谁
最近故障
给产品看的版本
展开技术细节
```

本地 Stub 完整 smoke（飞书回调路径，强制 stub / 非 live）：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_stub_pilot_smoke.py -q
```

该用例覆盖上述 8 类问题、多轮会话、卡片分区与 `allow_apply=False`。

### V0.2 本地 OpenAI Safe Live

目标：验证真实模型表达增强、timeout / retry / fallback / audit；不对真实用户开放。

在 V0.1 基础上增加：

```powershell
$env:PROJECT_LENS_MODEL_PROVIDER="openai"
$env:PROJECT_LENS_MODEL_LIVE="true"
$env:PROJECT_LENS_MODEL_OPENAI_API_KEY="你的 key"
$env:PROJECT_LENS_MODEL_OPENAI_MODEL="gpt-4o-mini"
$env:PROJECT_LENS_MODEL_TIMEOUT_SECONDS="30"
$env:PROJECT_LENS_MODEL_MAX_RETRIES="1"
$env:PROJECT_LENS_MODEL_FALLBACK_TO_STUB="true"
```

或写入 `.env`（勿提交）：

```text
PROJECT_LENS_MODEL_PROVIDER=openai
PROJECT_LENS_MODEL_LIVE=true
PROJECT_LENS_MODEL_OPENAI_API_KEY=
PROJECT_LENS_MODEL_OPENAI_MODEL=gpt-4o-mini
PROJECT_LENS_MODEL_FALLBACK_TO_STUB=true
```

观察：

- Audit 卡片里 provider / live_effective / usage
- 失败时 fallback stub，飞书链路不崩
- 无证据事实不会变成 FACT
- `allow_apply` 始终为 false

本地 Safe Live smoke（mock transport，不打真实网；CI 默认可跑）：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_safe_live_pilot_smoke.py -q
```

覆盖：表达增强生效、无证据 FACT 降级、provider 失败 fallback、Audit 不泄露 API key、`allow_apply=False`。

真实 key 手工验证（可选，不进 CI）：按上方环境变量启动 uvicorn 后问「介绍一下这个项目」，看 Audit 的 provider / usage。

关闭 LLM（随时可回退）：

```text
PROJECT_LENS_MODEL_PROVIDER=stub
PROJECT_LENS_MODEL_LIVE=false
```

### V0.3 飞书测试群灰度

目标：2–5 人测试群，只读，绑定一个测试项目。

额外要求：

| 变量 | 说明 |
|---|---|
| `PROJECT_LENS_FEISHU_VERIFICATION_TOKEN` | 与飞书后台一致 |
| `PROJECT_LENS_FEISHU_SIGNING_SECRET` | 建议开启签名校验 |
| `PROJECT_LENS_FEISHU_APP_ID` / `APP_SECRET` | 真实 outbound |
| `PROJECT_LENS_FEISHU_PROJECT_BINDINGS` | 绑定测试群 → 唯一项目 |
| `PROJECT_LENS_LOCAL_PROJECT_REGISTRY` | 注册同一项目 |
| Model | 默认 stub；表达增强稳定后再开 Safe Live |

群置顶说明建议：

```text
ProjectLens 当前是只读项目协作 Agent。
可以问：介绍一下这个项目 / 项目地图 / 最近变更 / 知识库缺什么 / 负责人是谁 / 最近故障
暂不支持：自动改代码、自动上线、自动回滚、自动重启、自动写长期记忆
```

详细事件订阅见 `docs/feishu-setup.md`。  
**灰度 Runbook（置顶文案 / Day-1 手测 / 观察指标 / 紧急回滚）：** `docs/feishu-grey-release.md`。

---

## 4. 环境变量一览

| 变量 | 默认 | 说明 |
|---|---|---|
| `PROJECT_LENS_ENV` | `development` | 运行环境标签 |
| `PROJECT_LENS_LOG_LEVEL` | `INFO` | 日志级别 |
| `PROJECT_LENS_API_PREFIX` | `/api/v1` | HTTP 前缀 |
| `PROJECT_LENS_DATABASE_PATH` | `project_lens.db` | SQLite 文件；试点勿用 `:memory:` |
| `PROJECT_LENS_CONVERSATION_STORE` | `sqlite` | `sqlite` \| `memory` |
| `PROJECT_LENS_MODEL_PROVIDER` | `stub` | `stub` \| `openai` \| `internal` \| `local` |
| `PROJECT_LENS_MODEL_NAME` | `stub-v1` | 逻辑模型名（audit） |
| `PROJECT_LENS_MODEL_LIVE` | `false` | 是否允许真实网络/推理 |
| `PROJECT_LENS_MODEL_FALLBACK_TO_STUB` | `true` | provider 失败回退 stub |
| `PROJECT_LENS_MODEL_TIMEOUT_SECONDS` | `30` | 单次调用超时 |
| `PROJECT_LENS_MODEL_MAX_RETRIES` | `1` | 重试次数 |
| `PROJECT_LENS_MODEL_MAX_TOKENS` | `2048` | 最大输出 token |
| `PROJECT_LENS_MODEL_TEMPERATURE` | `0.0` | 温度 |
| `PROJECT_LENS_MODEL_OPENAI_API_KEY` | 空 | OpenAI key |
| `PROJECT_LENS_MODEL_OPENAI_MODEL` | 空 | 如 `gpt-4o-mini` |
| `PROJECT_LENS_MODEL_OPENAI_BASE_URL` | 空 | 可选兼容网关 |
| `PROJECT_LENS_FEISHU_VERIFICATION_TOKEN` | local token | 回调校验 |
| `PROJECT_LENS_FEISHU_SIGNING_SECRET` | 空 | 可选 HMAC |
| `PROJECT_LENS_FEISHU_APP_ID` / `SECRET` | 空 | outbound；空则 recording |
| `PROJECT_LENS_FEISHU_PROJECT_BINDINGS` | 空 JSON | 群 ↔ 项目 |
| `PROJECT_LENS_LOCAL_PROJECT_REGISTRY` | 空 | 本地项目注册 |
| `PROJECT_LENS_FEISHU_DOC_SYNC_ON_STARTUP` | `false` | 启动同步文档 |
| `PROJECT_LENS_FEISHU_DOC_SYNC_INTERVAL_SECONDS` | `0` | 周期同步；0=关闭 |

完整示例见仓库根目录 `.env.example`。

---

## 5. 上线前 Checklist（配置向）

复制自 launch plan，便于勾选：

- [ ] `PROJECT_LENS_DATABASE_PATH` 持久化，非 `:memory:`
- [ ] `PROJECT_LENS_CONVERSATION_STORE=sqlite`
- [ ] Feishu verification token / signing secret / app id+secret 已配置（V0.3）
- [ ] `PROJECT_LENS_FEISHU_PROJECT_BINDINGS` 已绑定测试群与项目
- [ ] 默认 `MODEL_PROVIDER=stub`；仅灰度时改 openai + `MODEL_LIVE=true`
- [ ] `MODEL_FALLBACK_TO_STUB=true`
- [ ] API key 未写入代码与文档
- [ ] ruff + 全量 pytest 通过

功能向（V0.1 应已具备；可用 `tests/test_stub_pilot_smoke.py` 一键验收）：

- [ ] 介绍一下这个项目
- [ ] 项目地图
- [ ] 最近变更 / 知识库缺什么 / 负责人是谁 / 最近故障
- [ ] 给产品看的版本 / 展开技术细节（多轮追问）

---

## 6. 常见问题

**Q: 配了 OpenAI key 但没调用？**  
A: 需要同时 `MODEL_PROVIDER=openai` 且 `MODEL_LIVE=true`。

**Q: 飞书没回消息？**  
A: 未配 App ID/Secret 时只走 recording adapter（适合单测）。真实群需要 outbound 凭证，并确认 bindings 与 URL verification。

**Q: 会话追问丢上下文？**  
A: 确认 `CONVERSATION_STORE=sqlite` 且 `DATABASE_PATH` 指向同一持久化文件。

**Q: 会不会自动改代码或上线？**  
A: 不会。当前代码路径不打开 Apply；试点配置也不提供此类开关。

---

## 2026-08-05 补充：read_agent 飞书灰度请优先看新 Runbook

如果本次试点目标是验证“飞书自然语言项目问题进入只读调查 Agent”，请优先使用：

```text
docs/read-agent-feishu-grey-release.md
```

该文档覆盖：

- `PROJECT_LENS_AGENT_MODE=read_agent` 怎么配置；
- `/api/v1/integrations/feishu/status` 如何确认 agent_mode / model_live；
- 飞书群内应该问哪些未预置自由项目问题；
- `/project` 如何作为 debug/smoke 而不是正式入口；
- 什么时候才打开真实 LLM；
- 异常时如何回滚到 stub。
