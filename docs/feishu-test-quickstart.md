# 飞书测试快速上手

更新时间：2026-09-09

给**拿到本仓库、想在飞书群里试问一轮**的人用。按顺序做即可。

产品边界见 [`projectlens-product-document.md`](projectlens-product-document.md)。更细的配置表见 [`pilot-launch-config.md`](pilot-launch-config.md)；飞书权限与回调清单见 [`feishu-setup.md`](feishu-setup.md)。Hermes profile 样例见 [`../config/hermes/README.md`](../config/hermes/README.md)。

---

## 0. 你需要准备什么

| 项 | 是否必须 | 说明 |
|---|---|---|
| Python 3.12+ | 必须 | 跑 ProjectLens API / MCP |
| 飞书企业自建应用 | 必须（真机联调） | App ID / App Secret / Verification Token |
| 一个测试群 | 必须 | 把机器人拉进群 |
| 测试者的飞书 `open_id` | 强烈建议 | 写入 ProjectSpace `members`，否则只能看 `public_sources` |
| OpenAI 兼容 API Key | 真机自然语言问答时必须 | DeepSeek / OpenAI / 内网网关均可 |
| Hermes Agent 仓库 | 推荐 | 当前推荐用 **WebSocket 网关**收飞书消息 |
| 公网 HTTPS 回调 | 仅 HTTP 事件模式需要 | WebSocket 模式可本地跑，不必先配公网 |

当前推荐联调路径：

```text
飞书群 @机器人
  -> Hermes Gateway（WebSocket）
  -> ProjectLens MCP / HTTP（权限 · 检索 · 证据）
  -> 回复到飞书
```

备选：飞书事件直接回调 `POST /api/v1/feishu/events`（需要公网 URL 或内网穿透）。新手优先走 WebSocket。

---

## 1. 克隆并安装 ProjectLens

```powershell
git clone https://github.com/lyly-RMB/Yu-coding.git
cd Yu-coding
Copy-Item .env.example .env
py -3.12 -m pip install -e ".[dev,mcp]"
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
2. 打开「事件订阅」，记下 **Verification Token**；建议开启签名校验相关 Secret（若后台提供）。
3. 权限：至少能收发群消息（按当前飞书后台「机器人」相关权限申请并发布/测试安装）。
4. 启用机器人能力，把应用安装到你的测试企业。
5. 在测试群里添加该机器人。

### 2.1 怎么拿到 `chat_id`

任选一种：

- 用飞书开放平台「调试工具 / API 调试台」查群列表；
- 或先让机器人收到一条消息，从事件 payload 里读 `chat_id`（形如 `oc_...`）。

`tenant_key` 必须与事件里的租户一致，否则绑不上项目。

### 2.2 怎么拿到用户 `open_id`（写入 members）

权限按 **飞书 `open_id`** 识别用户，不是花名或邮箱。

常见取法：

1. 飞书开放平台 API 调试台调用通讯录/用户信息接口；
2. 从机器人收到的消息事件里读 `sender.open_id` / `sender_id.open_id`（形如 `ou_...`）；
3. 企业管理员在应用后台查看测试用户 ID（视租户能力而定）。

拿到后写入 `config/projects/<project>.json`：

```json
"members": [
  {"actor_id": "ou_你的open_id", "roles": ["developer"]}
]
```

角色建议：`developer` / `product` / `manager` / `qa` 等（与 manifest 策略一致）。

**未列入 `members` 的用户**只能访问 `public_sources`（guest）。这是预期行为，不是 bug。

demo `payment` 里有 `u1`、`u_dev` 等本地测试 ID；真机飞书联调时，请把你自己的 `ou_...` 加进去，或临时把测试账号写进 `members`。

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
# 可选：Hermes 仓库绝对路径（启动脚本会读）
PROJECT_LENS_HERMES_REPO=
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

新手建议先绑仓库自带 demo **`payment`**（见 `.env.example` 的 `PROJECT_LENS_LOCAL_PROJECT_REGISTRY`）。

同时改 `config/projects/payment.json`：

1. `feishu_chat_bindings[].chat_id` = 你的群；
2. `members` 加入你的 `ou_...`。

可选：在 bindings 里限制白名单用户：

```json
"allowed_users": ["ou_xxx"]
```

---

## 4. 配置 Hermes（推荐路径）

### 4.1 准备 Hermes 仓库

将 Hermes Agent 克隆到本机任意目录（示例：与 ProjectLens 同级的 `hermes-agent-main`），并按其文档安装到当前 Python 环境，保证：

```powershell
py -3.12 -m hermes_cli.main --help
```

可用。

在 ProjectLens `.env` 中设置：

```text
PROJECT_LENS_HERMES_REPO=D:/path/to/hermes-agent-main
```

或设置环境变量 `HERMES_ROOT`。

### 4.2 安装 `projectlens-safe` profile

按 [`../config/hermes/README.md`](../config/hermes/README.md)：

1. 合并 `projectlens-safe.config.yaml` 到 Hermes 配置；
2. 拷贝 `SOUL.projectlens-safe.md`；
3. 把 MCP `command` / `cwd` 改成你的 Python 与 ProjectLens 路径；
4. 把 `chat_id` 改成真实测试群。

**不要**对项目测试群使用带 terminal/file 的完整 `hermes-feishu` 预设。

### 4.3 启动

终端 1（可选手动；脚本也会尝试拉起 API）：

```powershell
cd <ProjectLens 根目录>
py -3.12 -m uvicorn project_lens.main:app --host 127.0.0.1 --port 8000
```

检查：

```text
GET http://127.0.0.1:8000/api/v1/integrations/feishu/status
```

期望：`configured=true`，`binding_count >= 1`。

终端 2：

```powershell
cd <ProjectLens 根目录>
.\scripts\start-hermes-feishu-projectlens.ps1 `
  -HermesRoot "D:\path\to\hermes-agent-main" `
  -ProjectId "payment"
```

常用参数：

| 参数 | 含义 | 默认 |
|------|------|------|
| `-ProjectLensRoot` | 本仓库路径 | 脚本所在仓库 |
| `-HermesRoot` | Hermes 仓库 | `HERMES_ROOT` / `.env` 的 `PROJECT_LENS_HERMES_REPO` / 同级 `hermes-agent-main` |
| `-HermesHome` | Hermes 配置目录 | `%LOCALAPPDATA%\hermes` |
| `-PythonExe` | Python 路径 | `PYTHON_EXE` 或 `py -3.12` |
| `-TenantId` / `-ProjectId` | 默认项目 | `demo` / `payment` |
| `-SkipApiStart` | 不自动启动 uvicorn | 关 |

在测试群 **@机器人** 发送：

```text
介绍一下这个项目
```

---

## 5. 备选：HTTP 事件回调（需公网）

1. 用 ngrok / 云主机把本机 `8000` 暴露为 HTTPS。
2. 飞书事件订阅地址填：

```text
https://<你的域名>/api/v1/feishu/events
```

3. 先完成 URL Verification，再订阅消息事件。
4. 启动 `uvicorn` 后，在群里发消息测试。

细节见 [`feishu-setup.md`](feishu-setup.md)。

---

## 6. 建议冒烟问题

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
- 未在 `members` 中的用户只能看到公开资料；
- **不会**自动改代码、开 PR、部署。

---

## 7. 配置对照清单

- [ ] `.env` 已从 `.env.example` 复制，且未提交 Git
- [ ] `PROJECT_LENS_AGENT_MODE=hermes`
- [ ] `PROJECT_LENS_DATABASE_PATH` 不是 `:memory:`
- [ ] 飞书 App ID / Secret / Verification Token 已填
- [ ] `PROJECT_LENS_SERVICE_TOKEN` 已设
- [ ] bindings 的 `chat_id` = 真实测试群
- [ ] bindings 的 `project_id` 在 registry 与 `config/projects/` 中存在
- [ ] ProjectSpace `members` 含测试者 `open_id`（`ou_...`）
- [ ] Hermes `projectlens-safe` 已合并，MCP `cwd`/Python 路径正确
- [ ] 真机问答已开 `MODEL_LIVE=true` 且模型 key 可用
- [ ] `GET /api/v1/integrations/feishu/status` 正常
- [ ] 提问时 @了机器人（默认 `FEISHU_REQUIRE_MENTION=true`）

---

## 8. 常见问题

**Q: status 里 `configured=false`？**  
A: App ID/Secret 为空时走 recording adapter，不会真发飞书。

**Q: 机器人没反应？**  
检查：是否 @机器人、gateway 是否在跑、`chat_id` 是否绑错、Hermes 是否连上 `127.0.0.1:8000`、模型 key、MCP `cwd` 是否指向本仓库。

**Q: 回了但几乎看不到代码细节？**  
你的 `open_id` 可能不在 `members` 里，当前是 guest，只能看 `public_sources`。

**Q: 回了但像在答错项目？**  
bindings / `-ProjectId` / ProjectSpace `feishu_chat_bindings` 不一致。一个群只能绑一个项目。

**Q: 脚本报 HermesRoot not found？**  
传入 `-HermesRoot`，或设置 `HERMES_ROOT` / `.env` 的 `PROJECT_LENS_HERMES_REPO`。

**Q: 只想本地验证、不接飞书？**  
保持 App 凭证为空，跑：

```powershell
py -3.12 -m pytest tests/test_feishu_http_and_acl.py tests/test_hermes_runtime.py -q
```

---

## 9. 下一步

1. 换成自己的项目：[`projectspace-onboarding-guide.md`](projectspace-onboarding-guide.md)
2. 收紧群可见性与成员角色
3. 更多环境变量：[`pilot-launch-config.md`](pilot-launch-config.md)
