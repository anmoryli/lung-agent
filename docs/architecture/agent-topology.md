# Agent Service 架构

## 患者请求路径

```text
AG-UI RunAgentInput
        ↓ SSE
API v1 → Gateway → 患者主 Agent
                      ├─ 专科注册发现工具
                      ├─ DeepAgents 原生 task 委派
                      ├─ 会话向量检索 / 长期记忆
                      └─ 选中专科 Agent → 该专科的 FHIR MCP 工具
```

## 目录职责

- `backend/api/v1/handler.py`：AG-UI SSE、运行取消和原有 JSON API。
- `backend/gateway/orchestration/gateway.py`：唯一角色路由入口。
- `backend/agents/patient/agent.py`：患者主 Agent、提示词、会话状态和 HITL 恢复。
- `backend/agents/patient/registry.py`：每轮发现专科目录，并生成 DeepAgents 子 Agent 注册项。
- `backend/agents/patient/{专科目录}/agent.py`：该专科的注册描述、提示词和 MCP 工具白名单。
- `backend/agents/patient/tools.py`：用 LangChain `@tool` 注册主 Agent 的小工具集。
- `backend/integrations/mcp/patient_fhir.py`：FHIR MCP 连接和患者 ID/写入确认参数绑定。
- `backend/integrations/memory/vector_store.py`：BGE + sqlite-vec 的长期记忆和会话归档。
- `backend/runtime/active_runs.py`、`async_runner.py`：运行 ID、断连取消和异步调用管理。

## 工具如何按需出现

主 Agent 初始只拿注册发现、历史检索和长期记忆工具，加上 DeepAgents 自动提供的 `task` 委派工具。注册中心只把专科名称和职责摘要交给主 Agent。MCP 工具 schema 按专科白名单装进对应子 Agent；模型委派后，子 Agent 才能调用自己的工具。

## 会话、记忆与恢复

- `patient-checkpoints.sqlite3` 保存当前 `threadId` 的 LangGraph 状态，取消生成后续接同一会话。
- `patient-memory-vectors.sqlite3` 保存长期记忆和会话归档；两种内容使用 `kind` 区分，按患者 ID 做 BGE 语义召回。
- 默认读取和归档。患者明确说要记住时，直接保存非临床偏好；明确要求停用或 `memoryConsent: false` 会删除该合成患者的向量记忆。
- 患者写入工具先暂停。AG-UI 以 `RUN_FINISHED` 的 interrupt outcome 返回确认项；同一 `threadId` 的新 run 用 `resume` 传批准或拒绝。
- SSE 断开或 DELETE 取消会停止正在运行的协程；下一轮会将“用户打断上一轮生成”写入 checkpoint，再继续处理新消息。

## 边界

1. Gateway 仍是唯一服务路由入口，子 Agent 彼此不互相委派。
2. 患者数据仅接受 `demo-patient-NNNN` 合成编号；服务没有登录认证，不能接真实患者数据。
3. 创建预约、申请或同步前需患者明确确认；医疗判断由医生确认。
4. LangSmith tracing 从环境变量开启；当前项目只用于合成数据演示。
