# 飞书测试快速上手

更新时间：2026-09-09

给**拿到本仓库、想在飞书群里试问一轮**的人用。按顺序做即可。

产品边界见 [`projectlens-product-document.md`](projectlens-product-document.md)。更细的配置表见 [`pilot-launch-config.md`](pilot-launch-config.md)；飞书权限与回调清单见 [`feishu-setup.md`](feishu-setup.md)。

---

## 0. 你需要准备什么

| 项 | 是否必须 | 说明 |
|---|---|---|
| Python 3.12+ | 必须 | 跑 ProjectLens API |
| 飞书企业自建应用 | 必须（真机联调） | App ID / App Secret / Verification Token |
| 一个测试群 | 必须 | 把机器人拉进群 |
| OpenAI 兼容 API Key | 真机自然语言问答时必须 | DeepSeek / OpenAI / 内网网关均可 |
| Hermes Agent 仓库 | 推荐 | 当前推荐用 **WebSocket 网关**收飞书消息 |
| 公网 HTTPS 回调 | 仅 HTTP 事件模式需要 | WebSocket 模式可本地跑，不必先配公网 |

当前推荐联调路径：

```text
飞书群 @机器人
  -> Hermes Gateway（WebSocket）
  -> ProjectLens HTTP API（只读工具 / 权限 / 证据）
  -> 回复到飞书
```

备选：飞书事件直接回调 `POST /api/v1/feishu/events`（需要公网 URL 或内网穿透）。新手优先走 WebSocket。

---

## 1. 克隆并安装 ProjectLens

```powershell
git clone https://github.com/lyly-RMB/Yu-coding.git
cd Yu-coding
Copy-Item .env.example .env
py -3.12 -m pip install -e ".[dev]"
# 若要用 MCP / Hermes 插件工具，再装：
# py -3.12 -m pip install -e ".[mcp]"
```

先确认 API 能起来：

```powershell
py -3.12 -m uvicorn project_lens.main:app --host 127.0.0.1 --port 8000
```

浏览器打开：

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/api/v1/health
```

---

## 2. 创建飞书应用

在 [飞书开放平台](https://open.feishu.cn/app) 创建企业自建应用：

1. 记下 **App ID**、**App Secret**。
2. 打开「事件订阅」，记下 **Verification Token**；建议开启 **Encrypt Key / Signing Secret**（若后台提供签名校验）。
3. 权限：至少能收发群消息（按当前飞书后台「机器人」相关权限申请并发布/测试安装）。
4. 启用机器人能力，把应用安装到你的测试企业。
5. 在测试群里添加该机器人。

### 怎么拿到 `chat_id`

任选一种：

- 用飞书开放平台「调试工具 / API 调试台」查群列表；
- 或先让机器人收到一条消息，从事件 payload 里读 `chat_id`（形如 `oc_...`）。

`tenant_key` 来自企业租户信息；本地 demo 也可用你在 bindings 里写死的值，但**必须与事件里的租户一致**，否则绑不上项目。

---

## 3. 填写 `.env`（最少必填）

编辑仓库根目录 `.env`（**不要提交到 Git**）。

### 3.1 基础

```text
PROJECT_LENS_ENV=development
PROJECT_LENS_DATABASE_PATH=project_lens.db
PROJECT_LENS_CONVERSATION_STORE=sqlite
PROJECT_LENS_AGENT_MODE=hermes
PROJECT_LENS_SERVICE_TOKEN=请换成一串足够长的随机字符
```

### 3.2 飞书凭证

```text
PROJECT_LENS_FEISHU_APP_ID=cli_xxx
PROJECT_LENS_FEISHU_APP_SECRET=xxx
PROJECT_LENS_FEISHU_VERIFICATION_TOKEN=xxx
PROJECT_LENS_FEISHU_SIGNING_SECRET=
```

### 3.3 模型（真机问答）

```text
PROJECT_LENS_MODEL_PROVIDER=openai
PROJECT_LENS_MODEL_LIVE=true
PROJECT_LENS_MODEL_OPENAI_BASE_URL=https://api.deepseek.com/v1
PROJECT_LENS_MODEL_OPENAI_API_KEY=sk-xxx
PROJECT_LENS_MODEL_OPENAI_MODEL=deepseek-chat
PROJECT_LENS_MODEL_FALLBACK_TO_STUB=true

PROJECT_LENS_FEISHU_HERMES_PROVIDER=openai
PROJECT_LENS_FEISHU_HERMES_MODEL=deepseek-chat
# BASE_URL / API_KEY 留空则复用上面的 MODEL_OPENAI_*
PROJECT_LENS_FEISHU_HERMES_BASE_URL=
PROJECT_LENS_FEISHU_HERMES_API_KEY=
```

本地只想跑单测、不接真模型时，保持：

```text
PROJECT_LENS_MODEL_PROVIDER=stub
PROJECT_LENS_MODEL_LIVE=false
```

### 3.4 群 ↔ 项目绑定（最容易配错）

`PROJECT_LENS_FEISHU_PROJECT_BINDINGS` 里的 `chat_id` / `project_id` 必须和真实测试群、已注册项目一致：

```text
PROJECT_LENS_FEISHU_PROJECT_BINDINGS={"bindings":[{"tenant_key":"你的tenant_key","chat_id":"oc_你的群","project_id":"payment","service":"order-service","environment":"production"}]}
```

仓库自带 demo 项目 `payment`（见 `.env.example` 的 `PROJECT_LENS_LOCAL_PROJECT_REGISTRY`）。  
新手建议先绑 `payment`，跑通后再换自己的 ProjectSpace（见 [`projectspace-onboarding-guide.md`](projectspace-onboarding-guide.md)）。

对应 `config/projects/payment.json` 里也应有相同 `chat_id` 的 `feishu_chat_bindings`（或你按自己的项目改）。

可选：限制只有白名单用户能问：

```json
"allowed_users": ["ou_xxx"]
```

---

## 4. 启动方式

### 方式 A（推荐）：Hermes WebSocket + ProjectLens API

1. 另开终端，保持 ProjectLens API：

```powershell
cd <本仓库根目录>
py -3.12 -m uvicorn project_lens.main:app --host 127.0.0.1 --port 8000
```

2. 检查飞书配置是否被识别：

```text
GET http://127.0.0.1:8000/api/v1/integrations/feishu/status
```

期望：`configured=true`，且 `binding_count >= 1`。

3. 启动 Hermes Gateway（需要本机已安装/克隆 Hermes，并配置好指向 ProjectLens 的工具或 MCP）。

仓库里有示例脚本 `scripts/start-hermes-feishu-projectlens.ps1`，但路径写死了原作者机器目录。请先改成你的：

- ProjectLens 根目录
- Hermes 根目录
- Python 可执行文件路径
- `PROJECTLENS_DEFAULT_TENANT_ID` / `PROJECTLENS_DEFAULT_PROJECT_ID`

Hermes 侧常见环境变量（由脚本或你手动设置）：

```text
FEISHU_APP_ID / FEISHU_APP_SECRET     # 与 ProjectLens .env 相同
FEISHU_CONNECTION_MODE=websocket
PROJECTLENS_API_BASE_URL=http://127.0.0.1:8000/api/v1
OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
```

Hermes profile 样例见 `config/hermes/`。

4. 在测试群 **@机器人**，发送一句自然语言，例如：

```text
介绍一下这个项目
```

### 方式 B：HTTP 事件回调（需公网）

1. 用 ngrok / 云主机把本机 `8000` 暴露为 HTTPS。
2. 飞书事件订阅地址填：

```text
https://<你的域名>/api/v1/feishu/events
```

3. 先完成 URL Verification，再订阅消息事件。
4. 启动 `uvicorn` 后，在群里发消息测试。

细节见 [`feishu-setup.md`](feishu-setup.md)。

---

## 5. 建议冒烟问题

在已绑定项目的测试群里试：

```text
介绍一下这个项目
项目当前状态怎么样
负责人是谁
知识库还缺什么
```

期望：

- 机器人有回复；
- 能区分事实 / 推断 / 未知（至少不胡编成「已确认」）；
- 未绑定的群没有项目上下文或被拒绝；
- **不会**自动改代码、开 PR、部署。

---

## 6. 配置对照清单（复制勾选）

- [ ] `.env` 已从 `.env.example` 复制，且未提交 Git
- [ ] `PROJECT_LENS_AGENT_MODE=hermes`
- [ ] `PROJECT_LENS_DATABASE_PATH` 不是 `:memory:`
- [ ] 飞书 App ID / Secret / Verification Token 已填
- [ ] `PROJECT_LENS_SERVICE_TOKEN` 已设（pilot 内部 API）
- [ ] bindings 的 `chat_id` = 真实测试群
- [ ] bindings 的 `project_id` 在 `LOCAL_PROJECT_REGISTRY` 与 `config/projects/` 中存在
- [ ] 真机问答已开 `MODEL_LIVE=true` 且模型 key 可用
- [ ] API `GET /api/v1/integrations/feishu/status` 正常
- [ ] 机器人已进群；提问时 @了机器人（若网关要求 mention）

---

## 7. 常见问题

**Q: status 里 `configured=false`？**  
A: 检查 App ID/Secret 是否为空；空凭证会走 local recording，不会真发飞书。

**Q: 机器人没反应？**  
检查：是否 @机器人、WebSocket/回调是否在跑、`chat_id` 是否绑错、Hermes 是否连上 `127.0.0.1:8000`、模型 key 是否有效。

**Q: 回了但像在答错项目？**  
bindings / `PROJECTLENS_DEFAULT_PROJECT_ID` / ProjectSpace `feishu_chat_bindings` 不一致。一个群只能绑一个项目。

**Q: 403 或没权限？**  
成员不在 ProjectSpace `members` 里时只能看 `public_sources`；或群可见性策略过窄。见接入指南。

**Q: 只想本地验证、不接飞书？**  
保持 App 凭证为空，跑：

```powershell
py -3.12 -m pytest tests/test_feishu_http_and_acl.py tests/test_hermes_runtime.py -q
```

---

## 8. 下一步

1. 把 demo `payment` 换成你自己的项目：[`projectspace-onboarding-guide.md`](projectspace-onboarding-guide.md)
2. 收紧群可见性与成员角色（业务群不要暴露敏感日志）
3. 需要文档同步、GitHub 只读 token 时，再看 [`pilot-launch-config.md`](pilot-launch-config.md)
