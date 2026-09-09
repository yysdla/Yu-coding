# Hermes `projectlens-safe` 配置样例

这些文件在 **ProjectLens** 仓库内，供复制到本机 Hermes，**不会**自动写入 `~/.hermes`，也**不会**改 Hermes 源码。

飞书联调总流程：[`docs/feishu-test-quickstart.md`](../../docs/feishu-test-quickstart.md)

## 文件

| 文件 | 用途 |
|------|------|
| `projectlens-safe.config.yaml` | MCP server、飞书工具白名单、禁用危险 toolset、群路由 stub |
| `SOUL.projectlens-safe.md` | Hermes 人格：必须走 ProjectLens MCP，禁止编造项目事实 |

## 前置

1. 已安装 ProjectLens（含 MCP extra）：

```powershell
cd <ProjectLens 仓库根目录>
py -3.12 -m pip install -e ".[dev,mcp]"
```

2. 本机已有可用的 Hermes Agent 仓库，并能运行：

```powershell
py -3.12 -m hermes_cli.main --help
```

3. ProjectLens API 可访问（默认 `http://127.0.0.1:8000`）。

验证 MCP stdio（会阻塞在 stdin，Ctrl+C 结束）：

```powershell
py -3.12 -m project_lens.integrations.mcp.server
```

## 拷贝到 Hermes（手动）

1. 创建 profile 目录（按你的 Hermes 版本）：  
   `%LOCALAPPDATA%\hermes\profiles\projectlens-safe\`  
   或 `~/.hermes/profiles/projectlens-safe/`
2. 将 `projectlens-safe.config.yaml` 中的键合并进该 profile 的 `config.yaml`（或当前激活的 `%LOCALAPPDATA%\hermes\config.yaml`）。
3. 将 `SOUL.projectlens-safe.md` 内容写入 profile 的 `SOUL.md`（或等价人格文件）。
4. **必须改路径**：
   - `mcp_servers.projectlens.command` → 你的 Python 绝对路径（推荐）
   - `mcp_servers.projectlens.cwd` → ProjectLens 仓库绝对路径
5. 将 `profile_routes[].chat_id` 换成真实飞书群 `oc_...`。
6. 重启 Hermes gateway，确认 MCP 工具发现成功。

样例 YAML 里的路径是占位符，不要直接用。

## 为什么不用默认 `hermes-feishu`？

`hermes-feishu` 常带 terminal / file 等完整工具。项目群若用该预设，可能绕过 ProjectLens Gateway。  
`projectlens-safe` 只白名单：

- `mcp-projectlens`（工具名形如 `mcp__projectlens__projectlens_ask_project`）
- `clarify`

并用 `agent.disabled_toolsets` 做纵深防御。

## 验收（手测）

1. 工具列表出现 `mcp__projectlens__projectlens_ask_project`
2. 项目问题走该工具，而不是 terminal/file
3. ProjectLens run 有工具审计；`allow_apply=false`
4. 改代码 / 部署 / 重启类请求被拒绝

自动化：`pytest tests/test_hermes_phase2_profile.py -q`

## 一键起网关

配置好 `.env` 与 Hermes 后，可用：

```powershell
.\scripts\start-hermes-feishu-projectlens.ps1 -HermesRoot "<你的 Hermes 目录>" -ProjectId "payment"
```

详见飞书测试快速上手文档。
