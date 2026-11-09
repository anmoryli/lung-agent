# Agent Service 架构骨架

## 请求路径

    HTTP 请求 → API v1 → Gateway → 角色主 Agent → 回复
                              ↘ SQLite taskKey 缓存

## 目录职责

- backend/api/v1/handler.py 接收请求、校验字段并返回 JSON。
- backend/gateway/orchestration/gateway.py 是唯一的角色路由和任务编排入口。
- backend/agents/{patient,doctor,management,nursing}/agent.py 分别创建四个 DeepAgents 主 Agent。
- backend/agents/patient/subagents.py 定义患者主 Agent 的 8 个隔离专科子 Agent。
- backend/agents/base.py 统一创建 DeepAgents 图并整理模型回复。
- backend/shared/tasking/idempotency_store.py 按稳定 taskKey 保存回复，重试时不重复调用模型。
- backend/integrations/patient_mcp.py 连接 FHIR MCP，并固定合成患者 ID、taskKey 和写入确认。

## 当前边界

1. 患者主 Agent 在 Gateway 工作流内委派专科子 Agent；子 Agent 不互相调用，Gateway 仍是唯一服务编排入口。
2. 相同 taskKey 和相同请求复用首次回复；相同 key 配不同请求会被拒绝。
3. 患者 Agent 不诊断、不改医嘱；医生 Agent 只生成辅助摘要和草稿，医疗判断由医生确认。
4. 管理 Agent 和护理 Agent 只整理信息与草稿，不代表外部操作已经执行。
5. 患者会话 checkpoint 与同意后的沟通偏好存入本地 SQLite；长期偏好按合成患者编号分区，不保存临床事实。
6. 患者 FHIR MCP 写工具先走 HITL 确认。当前没有登录认证，正式患者身份与授权必须由认证服务提供。
