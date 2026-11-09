# Agent 运行时

管理 Agent 注册、执行生命周期和标准事件。运行时只接受 Gateway 发出的调度请求。

`AGENT_TIMEZONE` 配置模型使用的显示时区，默认 `Asia/Shanghai`。每次主 Agent 请求都会自动带上带时区的当前时间；患者子 Agent 和长期记忆抽取模型也会收到时间上下文。主 Agent 可调用 `get_current_time` 刷新当前时刻。容器部署时在 `agent-service/.env` 中改此项并重启服务。
