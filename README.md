# Agent Service

OpenClaw 的 Agent as a Service 后端项目目录。当前是架构骨架，业务代码和运行配置尚未接入。

- `backend/api`：对外 HTTP 接口。
- `backend/gateway`：唯一的任务路由与 Agent 编排入口。
- `backend/agents`：患者、医生、管理、护理四类主 Agent 及其子 Agent。
- `backend/shared`：跨角色共享的任务、服务闭环单、权限和审计合同。
- `backend/integrations`：FHIR、MCP、BM25、向量与图检索适配器。
- `backend/runtime`：Agent 注册、执行和事件生命周期。
- `docs/architecture`：架构边界与目录说明。

前端仍保留在现有 `frontend/`。后端入口文件保留在 `backend/app.py`。
