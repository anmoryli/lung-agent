# 患者 Agent

`agent.py` 是患者主 Agent。它每轮都从 `registry.py` 发现专科 Agent，主模型用 DeepAgents 原生 `task` 工具分派任务。`tools.py` 里的 LangChain `@tool` 向主模型注册专科发现、历史检索和长期记忆保存工具；用户明确要求“记住”时，直接保存非临床偏好，不再询问同意。

每个专科的 `agent.py` 放在自己的目录里，里面写清职责、提示词和允许使用的 FHIR MCP 工具。Gateway 初始化时只把所需工具 schema 放进被选中子 Agent；主 Agent 不会收到整套 MCP 工具列表。

会话 checkpoint 负责还原同一会话的完整状态；`backend/integrations/memory/vector_store.py` 用 `BAAI/bge-small-zh-v1.5` 和 `sqlite-vec` 分开保存长期记忆与会话归档，默认跨会话按语义召回。传 `memoryConsent: false` 或对话里要求停止保存，会清除该演示患者的向量记忆。

患者 ID 目前只允许 `demo-patient-NNNN` 合成编号。写操作通过 DeepAgents interrupt 暂停，经 AG-UI `resume` 得到明确批准后才进入 MCP。
