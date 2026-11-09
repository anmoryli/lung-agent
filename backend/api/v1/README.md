# API v1

backend/api/v1/handler.py 提供第一版 HTTP 路由，统一调用路径为：

    POST /api/v1/agents/{role}/invoke

角色为 patient、doctor、management、nursing。请求 JSON 必须包含稳定 taskKey 和 message，可附带 context 对象。Gateway 校验角色并调用对应主 Agent；相同任务重试会复用 SQLite 中的第一次回复。

患者服务另提供会话目录接口：`GET /api/v1/agents/patient/sessions?patientId=demo-patient-0001` 列出会话，`GET /api/v1/agents/patient/sessions/{threadId}?patientId=demo-patient-0001` 加载对话、Agent 状态和待确认项。目录数据保存在 `AGENT_STORAGE_PATH/patient-sessions.sqlite3`。

以上目录是服务自己的资源接口。对话运行和恢复仍调用 `POST /api/v1/agents/patient/ag-ui`，使用 AG-UI `RunAgentInput` 的 `threadId`、`runId`、`messages` 和 `resume`。实现与存储边界见 `docs/architecture/patient-session-storage.md`。
