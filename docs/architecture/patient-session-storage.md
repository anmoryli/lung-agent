# 患者会话保存与恢复

## 数据放在哪里

服务端在 `AGENT_STORAGE_PATH/patient-sessions.sqlite3` 保存会话目录、显示标题、对话文本、最后一次 Agent 状态和待确认项。Docker Compose 将 `agent-service/storage` 挂载到容器的 `/app/storage`，所以容器重建或服务重启后数据还在。

`patient-checkpoints.sqlite3` 由 LangGraph SQLite checkpointer 保存 Agent 执行状态。继续同一个会话时必须复用原 `threadId`，调用仍通过 AG-UI `RunAgentInput`，消息和 `resume` 字段遵循原调用方式。

两份 SQLite 各有职责：会话目录用于列出、显示和切换；checkpointer 用于恢复 Agent 的图状态、TODO 和 HITL 中断点。只复制会话目录中的聊天文字，不会造出缺失的 checkpoint。

同一个 `patient-sessions.sqlite3` 还保存当前调试模型选择及其能力元数据。模型列表从配置的模型服务 `/models` 读取，只有服务端明确返回 `context_window` 与 `max_output_tokens` 的模型才可选。切换模型后，下一轮按该模型的窗口配置 DeepAgents 摘要阈值；运行状态会在 AG-UI `STATE_SNAPSHOT` 中带上模型窗口及输入 token 估算。

实时 Agent 轨迹通过 AG-UI `ACTIVITY_SNAPSHOT` 推送，包含主/专科 Agent 的运行状态、模型标识和流式首片段耗时。状态轨迹不包含模型的隐藏推理文本；只将主 Agent 的最终可见文本作为 `TEXT_MESSAGE_CONTENT` 输出。

## 模型窗口与上下文压缩

模型列表来自当前服务商的 `/models` 能力元数据；没有明确 `context_window` 和 `max_output_tokens` 的模型不会开放选择。页面同时显示服务端提供的总窗口、最大输出，以及 `总窗口 - 最大输出` 得出的保守输入预算。当前输入用量由 LangChain 近似 token 计数器估算，页面明确标为估算值。

运行时把该输入预算传入 DeepAgents 模型 profile。当前安装版本在可读到模型输入预算时，会在 85% 预算处触发摘要，保留最近 10% 的上下文，并在压缩旧消息前裁剪过大的工具参数；摘要会把被移出的消息存入后端会话历史。若服务商仍返回上下文超限错误，中间件会摘要并重试。模型切换后，下一轮会按新模型元数据重建模型与对应的预算。

这套行为是 DeepAgents 当前版本的自动摘要中间件策略。前端估算不代替服务商的实际 token 计量；每轮的真实请求量以模型 API 用量字段为准，目前页面尚未展示服务商返回的精确用量。

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

页面不使用 `localStorage` 或 `sessionStorage` 保存会话。打开页面时从服务端会话目录加载最近会话；新会话的对话、Agent 状态和待确认项都由服务端 SQLite 保存，刷新后可从会话列表重新加载。

## 部署边界

单机迁移时停服务并复制整个 `storage/` 目录。多副本部署不能把 SQLite 文件当共享数据库并发写；应改用支持并发访问的持久化存储，并让 checkpointer 与会话目录一起迁移。
