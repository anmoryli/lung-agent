# API v1

backend/api/v1/handler.py 提供第一版 HTTP 路由，统一调用路径为：

    POST /api/v1/agents/{role}/invoke

角色为 patient、doctor、management、nursing。请求 JSON 必须包含稳定 taskKey 和 message，可附带 context 对象。Gateway 校验角色并调用对应主 Agent；相同任务重试会复用 SQLite 中的第一次回复。

患者服务另提供会话目录接口：`GET /api/v1/agents/patient/sessions?patientId=demo-patient-0001` 列出会话，`GET /api/v1/agents/patient/sessions/{threadId}?patientId=demo-patient-0001` 加载对话、Agent 状态和待确认项。目录数据保存在 `AGENT_STORAGE_PATH/patient-sessions.sqlite3`。

开发调试页面右上角的“个人中心”调用 `GET /api/v1/agents/patient/profile?patientId=demo-patient-0001`，读取 FHIR 患者资料、该患者的会话全文、长期记忆、对话归档和最近 100 条向量召回记录。只接受 `demo-patient-NNNN` 合成患者编号；当前项目没有登录鉴权，这个接口只用于本地演示调试，不能暴露到公网或用于真实患者资料。记忆和归档存在 `patient-memory-vectors.sqlite3` 的 sqlite-vec 表；召回查询及实际命中结果存在同库的 `patient_memory_retrievals` 表。

以上目录是服务自己的资源接口。对话运行和恢复仍调用 `POST /api/v1/agents/patient/ag-ui`，使用 AG-UI `RunAgentInput` 的 `threadId`、`runId`、`messages` 和 `resume`。实现与存储边界见 `docs/architecture/patient-session-storage.md`。
