# Agent Service 架构骨架

## 请求路径

```text
HTTP API → Gateway → 角色主 Agent → 子 Agent → 共享合同/外部适配器
                 ↑                                  ↓
                 └──────── ServiceCase / taskKey ───┘
```

## 目录职责

- `backend/api` 只定义对外接口，不自行选择或调用子 Agent。
- `backend/gateway` 是唯一的意图路由和任务编排入口。
- `backend/agents/{patient,doctor,management,nursing}` 对应四个角色主 Agent；各自子目录对应一个窄职责子 Agent。
- `backend/shared` 保存 `taskKey`、`ServiceCase`、权限、审计和跨角色数据合同。
- `backend/runtime` 执行 Gateway 下发的 Agent 任务，不建立 Agent 之间的隐式调度。
- `backend/integrations` 隔离 FHIR、MCP、BM25、向量和图检索实现。

## 关键边界

1. 主 Agent 和子 Agent 都通过 Gateway 的任务上下文运行；子 Agent 不能直接改写其他角色任务。
2. 同一业务动作使用稳定 `taskKey`，跨角色状态落在同一个 `ServiceCase`。
3. AI 仅负责抽取、摘要、检索和草稿；医疗判断、外部写回、支付/权益等副作用必须经过相应确认。
4. 患者端只接收可理解、可执行的结果，不暴露内部风险等级、Agent 调用和 GraphRAG 证据链。

本目录仅为后续服务实现预留结构，当前没有运行时、API 或模型代码。
