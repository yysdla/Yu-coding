# Feishu 联调清单

更新时间：2026-09-09

飞书群聊是 ProjectLens 的工作现场。Hermes 负责消息接入、意图与工具循环；ProjectLens 负责 ProjectSpace 绑定、权限交集、证据检索与回答校验。

相关文档：

- 产品总纲：[`projectlens-product-document.md`](projectlens-product-document.md)
- 配置剖面：[`pilot-launch-config.md`](pilot-launch-config.md)
- 项目接入：[`projectspace-onboarding-guide.md`](projectspace-onboarding-guide.md)

## 1. 服务端配置

1. 复制 `.env.example` 为 `.env`（密钥只放本机，勿提交）。
2. 填入飞书 `APP_ID` / `APP_SECRET`。
3. 设置 `PROJECT_LENS_FEISHU_VERIFICATION_TOKEN`；建议同时配置 `PROJECT_LENS_FEISHU_SIGNING_SECRET`。
4. 在 `PROJECT_LENS_FEISHU_PROJECT_BINDINGS` 中配置租户、群聊与**唯一**项目映射。
5. 确认 `PROJECT_LENS_DATABASE_PATH` 为持久化文件，`PROJECT_LENS_CONVERSATION_STORE=sqlite`。
6. 生产决策路径保持：

```text
PROJECT_LENS_AGENT_MODE=hermes
```

7. 配置 Hermes 模型（`PROJECT_LENS_FEISHU_HERMES_*`；可复用 OpenAI 兼容网关）。试点/生产内部 API 需配置 `PROJECT_LENS_SERVICE_TOKEN`。
8. 启动后检查：

```text
GET /api/v1/integrations/feishu/status
```

确认 `configured=true`、`outbound_mode=http`，并且 `binding_count` 大于 0。

## 2. 事件订阅

回调地址：

```text
https://<your-domain>/api/v1/feishu/events
```

顺序：

1. 完成 URL Verification。
2. 订阅群聊 / 私聊消息事件（按飞书应用权限范围开通）。
3. 将机器人拉入已绑定的目标群。

服务会校验 verification token、可选签名和 `event_id` 去重。未映射的群聊在创建运行前返回 `403`。

## 3. 权限与可见性

权限在检索前进入 Agent 上下文，不是回答后再过滤：

```text
用户角色权限 ∩ 群聊可见范围 ∩ 项目资源权限
```

实践建议：

- 开发阶段开通机器人收发消息相关权限，并发布或测试安装到目标企业。
- 生产环境只安装到需要 ProjectLens 的项目群。
- 群 ↔ 项目关系以 `PROJECT_LENS_FEISHU_PROJECT_BINDINGS` 与 ProjectSpace `feishu_chat_bindings` 为准。
- 混合业务/研发群应使用更窄的 `ChatVisibilityPolicy`；敏感日志与内部调用细节默认不进业务群。

## 4. 本地测试

未配置 App ID / App Secret 时，使用 recording adapter，不会向外网发消息。可用：

```powershell
py -3.12 -m pytest tests/test_feishu_http_and_acl.py tests/test_feishu_integration.py tests/test_hermes_runtime.py -q
```

验证 token、消息格式、ACL 拦截与 Hermes 运行时。

## 5. 群置顶说明（建议）

```text
ProjectLens 是只读项目协作 Copilot（默认）。
可以问：项目介绍 / 当前状态 / 需求与实现是否一致 / 负责人与阻塞 / 会议结论与待办
回答会区分：已确认事实、推断、冲突、未知和下一步
暂不支持：自动改代码、自动 PR、自动部署回滚、无审批写正式项目记忆
```

## 6. 常见问题

**飞书没回消息？**  
未配 App ID/Secret 时只走 recording adapter。真实群需要 outbound 凭证，并确认 bindings、回调 URL 与机器人在群内。

**越权或看不到内容？**  
检查成员角色、群聊可见性与 ProjectSpace `public_sources` / `readable_sources` 交集。

**会话追问丢上下文？**  
确认 `CONVERSATION_STORE=sqlite` 且 `DATABASE_PATH` 指向同一持久化文件。
