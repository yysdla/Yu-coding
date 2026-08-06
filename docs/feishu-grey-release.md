# ProjectLens 飞书测试群灰度 Runbook（V0.3）

更新时间：2026-08-03

本文件是 Step 6 操作手册：把 2–5 个可信用户放进**一个**测试群做只读灰度。  
配置剖面见 `docs/pilot-launch-config.md`；事件联调见 `docs/feishu-setup.md`；产品方向见 `docs/project-agent-final-plan.md`。

```text
灰度目标：验证「问得懂、答得住、有证据、可追问、出错可关」。
不是目标：扩功能、开 Apply、接生产监控、写长期记忆自动化。
```

建议周期：**3–5 天**。

---

## 0. 灰度前必须先绿

在本机或部署机先跑：

```powershell
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m ruff check src tests
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest -q
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_stub_pilot_smoke.py tests/test_safe_live_pilot_smoke.py -q
```

全部通过后再进真实飞书群。

---

## 1. 范围边界（写进群约定）

| 开 | 关 |
|---|---|
| 一个测试项目 | 多项目混绑 |
| 只读问答 / 卡片 | Apply / 自动 PR |
| Stub 或单一 Safe Live provider | 多模型并行实验 |
| 项目介绍、地图、变更、缺口、负责人、故障 | deploy / rollback / restart |
| 多轮：给产品看的版本、展开技术细节 | 未审批写 ProjectMemory |

LLM 建议节奏：

1. 第 1 天：`MODEL_PROVIDER=stub`（先验证主链路）。
2. 表达与延迟可接受后：再开 `openai` + `MODEL_LIVE=true`，并保持 `FALLBACK_TO_STUB=true`。
3. 一有异常：立刻关回 stub（见第 6 节）。

---

## 2. Day-0 配置 Checklist

### 服务与数据

- [ ] `.env` 由 `.env.example` 复制，**未提交** secret
- [ ] `PROJECT_LENS_DATABASE_PATH` 为持久化文件（非 `:memory:`）
- [ ] `PROJECT_LENS_CONVERSATION_STORE=sqlite`
- [ ] `PROJECT_LENS_LOCAL_PROJECT_REGISTRY` 只注册**一个**测试项目
- [ ] `PROJECT_LENS_FEISHU_PROJECT_BINDINGS` 只绑定**一个**测试群 → 该项目
- [ ] 默认 `PROJECT_LENS_MODEL_PROVIDER=stub`、`MODEL_LIVE=false`
- [ ] `PROJECT_LENS_MODEL_FALLBACK_TO_STUB=true`

### 飞书应用

- [ ] `FEISHU_APP_ID` / `APP_SECRET` 已填（outbound=http）
- [ ] `FEISHU_VERIFICATION_TOKEN` 与后台一致
- [ ] 建议配置 `FEISHU_SIGNING_SECRET`
- [ ] 回调 URL：`https://<your-domain>/api/v1/feishu/events`
- [ ] URL Verification 已通过
- [ ] 已订阅群聊消息事件；机器人已进测试群
- [ ] 应用仅安装到测试企业 / 测试群，不进真实业务群

### 启动后自检

```text
GET /api/v1/integrations/feishu/status
```

期望：

- `configured=true`（或等价字段表示凭证可用）
- `outbound_mode=http`
- `binding_count >= 1`

在群里先由负责人自问一遍「介绍一下这个项目」：应收到进度文本 + 最终卡片，且含 **项目简介视图** / Audit。

---

## 3. 群置顶文案（直接复制）

```text
【ProjectLens 测试群说明｜只读试点】

ProjectLens 是项目协作 Agent，不是故障自动修复机器人。
当前版本只读：会引用 Evidence / 关系图，不会自动改代码、上线、回滚或重启。

可以直接问：
1. 介绍一下这个项目
2. 项目地图
3. 最近变更
4. 知识库缺什么
5. 负责人是谁
6. 最近故障

多轮可追问：
- 给产品看的版本
- 展开技术细节
- 那是谁改的？ / 影响哪里？

暂不支持：
- 自动改代码 / 自动创建 PR
- 自动上线 / 回滚 / 重启
- 自动写入长期项目记忆

使用约定：
- 请在本测试群提问；未绑定群不会回答。
- 结论以卡片中的证据与 Audit 为准；不确定会标「未知」。
- 遇到明显胡说或重复失败，请 @管理员并附 message 截图。
```

可选补充一行（开 Safe Live 时）：

```text
当前开启了表达增强大模型；失败会自动回退到本地 Stub，不影响只读主链路。
```

---

## 4. 灰度 Day-1 冒烟（负责人在群里手测）

按顺序各问一次，并勾选：

| # | 问法 | 期望卡片信号 |
|---|---|---|
| 1 | 介绍一下这个项目 | **项目简介视图**；有定位/服务/入口等 |
| 2 | 项目地图 | **项目地图视图**；核心服务/入口/上下游 |
| 3 | 最近变更 | **变更影响视图** |
| 4 | 知识库缺什么 | **知识库缺口视图** |
| 5 | 负责人是谁 | **负责人视图**；无证据则标未知 |
| 6 | 最近故障 | **故障协作视图**；声明不 Apply |
| 7 | （接故障后）给产品看的版本 | **业务结论** 可读 |
| 8 | 展开技术细节 | **技术细节** 存在 |

每张卡再确认：

- [ ] 有证据来源或关系路径（或明确未知）
- [ ] Audit 含 `allow_apply: False`
- [ ] 无「立即应用」按钮
- [ ] 无 API key / prompt 全文泄露

---

## 5. 观察指标（每天记一页即可）

| 指标 | 怎么记 |
|---|---|
| 成功回答率 | 有完整卡片 / 总提问 |
| 证据引用率 | 卡片含证据或关系路径的比例 |
| 可读性 | 用户是否说「看不懂 / 太长」 |
| 多轮连贯 | 追问是否接上上一轮 |
| 错误率 | 4xx/5xx、无卡片、明显胡说 |
| 延迟体感 | 快 / 可接受 / 慢 |
| LLM usage | Audit `usage`（仅 Safe Live） |
| fallback 次数 | provider 变 stub 或表达未增强 |
| 高频问法 | 用户还想问什么（记原文） |

简易日志模板：

```text
日期：
提问数：
成功卡片：
失败/异常：
最常问：
用户原话反馈：
是否开 Live：是/否
是否回滚到 Stub：是/否
明日动作：
```

---

## 6. 紧急开关与回滚

### 立刻关闭大模型（保留只读问答）

```text
PROJECT_LENS_MODEL_PROVIDER=stub
PROJECT_LENS_MODEL_LIVE=false
```

重启进程后生效。

### 立刻停止飞书回答

任选其一：

1. 在飞书后台禁用事件订阅 / 卸测机器人。
2. 清空或注释 `PROJECT_LENS_FEISHU_PROJECT_BINDINGS` 后重启（未映射群会 403）。
3. 停止 uvicorn / 网关进程。

### 不要做的事

- 不要为了“更好用”临时打开 Apply。
- 不要把测试机器人拉进真实业务大群。
- 不要在灰度期接真实生产监控或自动发布。
- 不要把 API key 贴进群或文档。

---

## 7. 灰度结束后怎么走（Step 7 / 8）

优先修（体验向）：

1. 问法识别不准  
2. 项目介绍不完整  
3. 项目地图不清晰  
4. 知识库缺口不准  
5. 多轮丢上下文  
6. 卡片过长难读  
7. provider 超时 / fallback 体验差  

然后再考虑：

- Step 8：小范围真实项目试用（仍只读）
- 更深的 Agent LLM 规划（需 ToolGateway / Policy / Evaluation 更完整）

---

## 8. 相关命令速查

```powershell
# 配置与手册
# .env.example
# docs/pilot-launch-config.md
# docs/feishu-setup.md

# 验收
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m pytest tests/test_stub_pilot_smoke.py tests/test_safe_live_pilot_smoke.py -q

# 启动（示例）
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m uvicorn project_lens.main:app --host 0.0.0.0 --port 8000
```
