# Agent Service 架构骨架

## 请求路径

    HTTP 请求 → API v1 → Gateway → 角色主 Agent → 回复
                              ↘ SQLite taskKey 缓存

## 目录职责

- backend/api/v1/handler.py 接收请求、校验字段并返回 JSON。
- backend/gateway/orchestration/gateway.py 是唯一的角色路由和任务编排入口。
- backend/agents/{patient,doctor,management,nursing}/agent.py 分别创建四个 DeepAgents 主 Agent。
- backend/agents/base.py 统一创建 DeepAgents 图并整理模型回复。
- backend/shared/tasking/idempotency_store.py 按稳定 taskKey 保存回复，重试时不重复调用模型。
- backend/integrations 预留 FHIR、MCP、BM25、向量和图检索实现。

## 当前边界

1. 主 Agent 只处理 Gateway 交给它的单次请求，不自行调用子 Agent。
2. 相同 taskKey 和相同请求复用首次回复；相同 key 配不同请求会被拒绝。
3. 患者 Agent 不诊断、不改医嘱；医生 Agent 只生成辅助摘要和草稿，医疗判断由医生确认。
4. 管理 Agent 和护理 Agent 只整理信息与草稿，不代表外部操作已经执行。
5. 当前没有登录鉴权、FHIR/MCP 工具、ServiceCase 持久化和子 Agent 调度；本地运行时使用合成数据。
