# API v1

backend/api/v1/handler.py 提供第一版 HTTP 路由，统一调用路径为：

    POST /api/v1/agents/{role}/invoke

角色为 patient、doctor、management、nursing。请求 JSON 必须包含稳定 taskKey 和 message，可附带 context 对象。Gateway 校验角色并调用对应主 Agent；相同任务重试会复用 SQLite 中的第一次回复。

患者服务另提供会话目录接口：`GET /api/v1/agents/patient/sessions?patientId=demo-patient-0001` 列出会话，`GET /api/v1/agents/patient/sessions/{threadId}?patientId=demo-patient-0001` 加载对话、Agent 状态和待确认项。目录数据保存在 `AGENT_STORAGE_PATH/patient-sessions.sqlite3`。

开发调试页面右上角的“个人中心”调用 `GET /api/v1/agents/patient/profile?patientId=demo-patient-0001`，读取 FHIR 患者资料、该患者的会话全文、长期记忆、归档、最近 100 条向量召回和后台抽取队列状态。只接受 `demo-patient-NNNN` 合成患者编号；当前项目没有登录鉴权，这个接口只用于本地演示调试，不能暴露到公网或用于真实患者资料。向量归档保存在 `patient-memory-vectors.sqlite3` 的 sqlite-vec 表；抽取任务、长期记忆、短摘要和召回审计保存在同一 SQLite 文件的关系表中。

前端从 `GET /api/v1/agents/patient/models` 读取模型服务公开的能力清单，通过 `POST /api/v1/agents/patient/model` 切换默认患者模型。当前模型 ID、总上下文窗口和最大输出长度会落到 `patient-sessions.sqlite3`；模型窗口来自模型服务 `/models` 元数据，缺少窗口信息的模型不会显示为可选项。页面同时显示 `总窗口 - 最大输出` 得出的保守输入预算和 DeepAgents 的摘要触发线。AG-UI 运行时的输入用量是基于模型消息的估算值，不能替代服务商实际计量。

以上目录是服务自己的资源接口。对话运行和恢复仍调用 `POST /api/v1/agents/patient/ag-ui`，使用 AG-UI `RunAgentInput` 的 `threadId`、`runId`、`messages` 和 `resume`。实现与存储边界见 `docs/architecture/patient-session-storage.md`。
