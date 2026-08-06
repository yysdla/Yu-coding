# ProjectLens（本仓库）

ProjectLens 是项目智能内核：把代码、文档、图谱、Evidence 和 RolePolicy 收成可被 Hermes 调用的只读工具层。

## 产品边界

- Hermes 负责 Agent loop / 飞书网关 / 模型调用
- ProjectLens 负责 ProjectSpace、RAG/GraphRAG、Evidence、ToolGateway、Citation
- 正式工具循环走 `projectlens_*` 只读工具（`GET/POST /api/v1/project-agent/tools[/call]`）
- `/project` ask 与 `projectlens_ask_project` 仅 debug/smoke
- Apply / PR / deploy / rollback / restart 关闭

## 本仓库入口

- 应用入口：`src/project_lens/main.py`
- Tool Envelope：`src/project_lens/application/project_agent_tools.py`
- Hermes 插件：`src/project_lens/integrations/hermes_plugin/`
- ProjectSpace 配置：`config/projects/`
- 规划文档：`docs/project-agent-final-plan.md`

## Demo 项目

`payment` / `crm` 仍保留在 `examples/` 与 `config/projects/`，仅作演示用 ProjectSpace。
