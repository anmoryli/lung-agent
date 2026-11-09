# 患者 Agent

`agent.py` 是 Gateway 内患者流程的唯一编排器。它识别目标后委派一个子 Agent，管理会话 checkpoint、长期沟通偏好和 MCP 工具确认。医疗判断、检查必要性、费用资格和服务履约都不由模型决定。

`subagents.py` 定义 8 个子 Agent 的职责、中文提示词和最小工具白名单。读取工具只会用于 `demo-patient-NNNN` 合成患者；写入工具先由 DeepAgents HITL 暂停，患者明确确认后才传给 FHIR MCP。

本地使用 `context.conversationId` 续接对话，同一会话会按合成患者编号隔离。`memoryConsent: true` 才会保存白名单中的沟通偏好；传 `false` 会清除偏好。临床事实不写入长期记忆。当前 API 没有登录认证，因此只适用于合成数据演示。
