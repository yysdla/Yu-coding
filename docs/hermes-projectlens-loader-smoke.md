# Hermes ProjectLens Plugin Loader Smoke

更新日期：2026-08-04  
阶段：Phase 3 continuation / Hermes loader verification

## 结论

ProjectLens 插件已经不只是“可导出、可注册”，而是已经通过了 Hermes 自己的 plugin manager 验证。

关键发现：

```text
Hermes plugin manager can scan:
  C:\Users\Administrator\Desktop\hermes-agent-main\plugins\projectlens\plugin.yaml

But Hermes skips it unless:
  plugins.enabled contains projectlens
```

也就是说，如果真实 Hermes CLI 里看不到 `/project`，第一优先级不是怀疑 ProjectLens ask API，也不是怀疑插件代码，而是检查 Hermes 的启用配置。

## 已验证链路

使用临时 `HERMES_HOME/config.yaml` 启用：

```yaml
plugins:
  enabled:
    - projectlens
```

然后通过 Hermes 自己的 loader 验证：

```text
get_plugin_commands()
  -> project
  -> project-gaps
  -> project-map
  -> project-role

get_plugin_command_handler("project")
  -> handler exists

handler("What is this project?")
  -> returned a ProjectLens answer
```

这证明：

- Hermes 可以扫描到导出的 ProjectLens 插件；
- Hermes 可以加载 ProjectLens 插件；
- Hermes 可以注册 `/project` 等命令；
- Hermes 的 command handler 可以真实调用 ProjectLens ask API；
- 返回结果仍包含 ProjectLens answer envelope 渲染结果；
- `allow_apply=false` 边界仍然保留。

## 为什么会被跳过

Hermes 插件默认是 opt-in。

日志里可见：

```text
Parsed manifest: key=projectlens name=projectlens kind=standalone source=bundled
Skipping 'projectlens' (not in plugins.enabled)
```

所以只把插件导出到：

```text
C:\Users\Administrator\Desktop\hermes-agent-main\plugins\projectlens
```

还不够。还必须在 Hermes 当前使用的 `HERMES_HOME/config.yaml` 里启用。

默认 Hermes Home 通常是：

```text
C:\Users\Administrator\AppData\Local\hermes
```

对应配置通常是：

```text
C:\Users\Administrator\AppData\Local\hermes\config.yaml
```

## 下一步真实 Hermes CLI 测试步骤

1. 确认 ProjectLens API 在运行：

```text
http://127.0.0.1:8000/docs
```

2. 确认插件已导出：

```text
C:\Users\Administrator\Desktop\hermes-agent-main\plugins\projectlens\__init__.py
C:\Users\Administrator\Desktop\hermes-agent-main\plugins\projectlens\plugin.yaml
```

3. 在 Hermes 的 `HERMES_HOME/config.yaml` 加入：

```yaml
plugins:
  enabled:
    - projectlens
```

如果原来已经有 `plugins.enabled`，只追加 `projectlens`，不要覆盖已有列表。

4. 设置 ProjectLens 环境变量：

```powershell
$env:PROJECTLENS_API_BASE_URL="http://127.0.0.1:8000/api/v1"
$env:PROJECTLENS_DEFAULT_TENANT_ID="demo"
$env:PROJECTLENS_DEFAULT_PROJECT_ID="projectlens"
```

5. 进入 Hermes CLI 测：

```text
/project What is this project?
/project-map
/project-gaps
/project-role business What should non-technical teammates know about this project?
```

## 排错顺序

如果 `/project` 不存在：

1. 检查 `HERMES_HOME` 是否指向你以为的目录；
2. 检查 `HERMES_HOME/config.yaml` 是否包含 `plugins.enabled: [projectlens]`；
3. 开启 `HERMES_PLUGINS_DEBUG=1` 看 Hermes 是否扫描到 `plugins/projectlens`；
4. 检查 `plugin.yaml` 是否存在；
5. 检查 `__init__.py` 是否指向正确的 ProjectLens `src`。

如果 `/project` 存在但调用失败：

1. 检查 ProjectLens API 是否启动；
2. 检查 `PROJECTLENS_API_BASE_URL`；
3. 检查 `tenant_id/project_id` 是否是已注册 ProjectSpace；
4. 查看 ProjectLens API 日志；
5. 再运行：

```powershell
cd C:\Users\Administrator\Desktop\project-lens
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m project_lens.integrations.hermes_plugin.smoke `
  --plugin-dir C:\Users\Administrator\Desktop\hermes-agent-main\plugins\projectlens `
  --api-base-url http://127.0.0.1:8000/api/v1 `
  --tenant-id demo `
  --project-id payment `
  --question "What is this project?"
```

## 当前状态

当前已完成：

```text
ProjectLens plugin unit tests: 15 passed
ProjectLens full pytest: 411 passed
ruff: All checks passed
Hermes manager loader smoke: passed with temporary HERMES_HOME
Hermes real config enabled: C:\Users\Administrator\AppData\Local\hermes\config.yaml
Hermes manager loader smoke: passed with real HERMES_HOME
HermesCLI.process_command("/project What is this project?"): passed
```

注意：`cli.py --query "/project ..."` 不适合验证 slash command。源码显示 `--query` 直接进入 `chat()`，不会先走交互循环里的 `process_command()` slash 分发层，所以它可能卡在 LLM/model 初始化上。验证 slash command 应使用交互 CLI 输入，或测试时直接调用 `HermesCLI.process_command(...)`。

可复用 smoke 命令：

```powershell
cd C:\Users\Administrator\Desktop\project-lens
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' -m project_lens.integrations.hermes_plugin.smoke `
  --hermes-repo C:\Users\Administrator\Desktop\hermes-agent-main `
  --hermes-home C:\Users\Administrator\AppData\Local\hermes `
  --api-base-url http://127.0.0.1:8000/api/v1 `
  --tenant-id demo `
  --project-id payment `
  --command "/project What is this project?"
```

成功时应看到：

```text
### ProjectLens 项目回答
...
Audit: allow_apply=false | tools=... | run_id=... | trace_id=...
process_command_return=true
```

下一步才是正式 Hermes 交互终端和飞书测试群联调。
