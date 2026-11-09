# 患者会话目录与恢复 TODO

- [x] 查阅 AG-UI `RunAgentInput`、thread、resume 和 LangGraph checkpoint 的官方说明。
- [x] 明确 AG-UI 运行协议与服务自有会话目录接口的边界。
- [x] 新增服务端 SQLite 会话目录，保存消息、状态和待确认项。
- [x] 页面从服务端列出/加载会话；浏览器存储仅保留当前 threadId 和患者演示 ID。
- [x] 首次加载时迁入旧版页面中已有的本地聊天记录。
- [x] 更新 API 与架构文档，完成 Python/JavaScript 静态语法检查。
- [x] 重建并启动 Docker 服务，检查健康接口、会话目录接口和页面。
- [x] 提交本次变更。

参考：AG-UI 官方 [RunAgentInput 与事件说明](https://docs.ag-ui.com/concepts/events)；LangGraph 官方 [持久化与恢复说明](https://docs.langchain.com/oss/python/langgraph/persistence)。
