# 患者会话保存与恢复

## 数据放在哪里

服务端在 `AGENT_STORAGE_PATH/patient-sessions.sqlite3` 保存会话目录、显示标题、对话文本、最后一次 Agent 状态和待确认项。Docker Compose 将 `agent-service/storage` 挂载到容器的 `/app/storage`，所以容器重建或服务重启后数据还在。

`patient-checkpoints.sqlite3` 由 LangGraph SQLite checkpointer 保存 Agent 执行状态。继续同一个会话时必须复用原 `threadId`，调用仍通过 AG-UI `RunAgentInput`，消息和 `resume` 字段遵循原调用方式。

两份 SQLite 各有职责：会话目录用于列出、显示和切换；checkpointer 用于恢复 Agent 的图状态、TODO 和 HITL 中断点。只复制会话目录中的聊天文字，不会造出缺失的 checkpoint。

## 调用方式

服务提供两个会话目录扩展接口：

```text
GET /api/v1/agents/patient/sessions?patientId=demo-patient-0001
GET /api/v1/agents/patient/sessions/{threadId}?patientId=demo-patient-0001
```

这两个接口是本服务的会话资源管理接口。AG-UI 定义 Agent 运行输入、事件流、thread 和恢复调用，没有定义会话目录的 CRUD API。真正发消息或恢复时，继续调用：

```text
POST /api/v1/agents/patient/ag-ui
Accept: text/event-stream
```

请求体沿用 AG-UI `RunAgentInput`，包括 `threadId`、新的 `runId`、累计 `messages`，需要恢复确认时附上 `resume`。第三方调用方也应先按 `patientId` 获取目录，再把选中的 `threadId` 和会话消息交给标准 AG-UI 运行入口。

旧页面 `deepagents.patient-chat.v1` 中的记录会在首次加载时迁入服务端目录。`localStorage` 以后只保留当前选中的 `threadId` 和演示患者 ID，不保存对话正文。

## 部署边界

单机迁移时停服务并复制整个 `storage/` 目录。多副本部署不能把 SQLite 文件当共享数据库并发写；应改用支持并发访问的持久化存储，并让 checkpointer 与会话目录一起迁移。
