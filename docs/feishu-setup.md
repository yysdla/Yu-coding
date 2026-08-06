# Feishu 联调清单

上线测试配置剖面（Stub / Safe Live / 测试群灰度）见 `docs/pilot-launch-config.md`。  
飞书测试群灰度操作手册（置顶文案、Day-1 冒烟、回滚开关）见 `docs/feishu-grey-release.md`。

## 服务端配置

1. 复制 `.env.example` 为 `.env`。
2. 填入飞书应用的 App ID 和 App Secret。
3. 设置 `PROJECT_LENS_FEISHU_VERIFICATION_TOKEN`。
4. 在 `PROJECT_LENS_FEISHU_PROJECT_BINDINGS` 中配置租户、群聊和项目映射。
5. 确认 `PROJECT_LENS_DATABASE_PATH` 为持久化路径，`PROJECT_LENS_CONVERSATION_STORE=sqlite`。
6. 默认保持 `PROJECT_LENS_MODEL_PROVIDER=stub` 与 `PROJECT_LENS_MODEL_LIVE=false`；灰度表达增强时再开 Safe Live。
7. 启动服务后访问：

```text
GET /api/v1/integrations/feishu/status
```

确认 `configured=true`、`outbound_mode=http`，并且 `binding_count` 大于 0。

## 事件订阅

把事件回调地址配置为：

```text
https://<your-domain>/api/v1/feishu/events
```

先完成 URL Verification，再订阅群聊消息事件。服务会校验 token、可选签名和 `event_id`
去重；未映射的群聊会在创建 AgentRun 前返回 `403`。

## 权限建议

开发阶段至少需要开通机器人收发群消息相关权限，并发布或测试安装应用到目标企业。生产环境中，
只把应用安装到需要 ProjectLens 的项目群，项目访问关系仍以
`PROJECT_LENS_FEISHU_PROJECT_BINDINGS` 为准。

## 本地测试

未配置 App ID / App Secret 时，ProjectLens 使用 recording adapter，不会发送外网请求。可用
`tests/test_feishu_http_and_acl.py` 验证 token 缓存、消息格式和权限拦截。
