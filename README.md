# Agent Service

OpenClaw 的 Agent as a Service 最小可运行版。DeepAgents 提供四个角色主 Agent，Gateway 是统一 HTTP 入口和唯一调度点。

## 本地启动

需要 Python 3.10 或更高版本，以及 DeepSeek API Key。

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:OPENAI_API_KEY = "填写模型服务密钥"
$env:OPENAI_BASE_URL = "https://api.deepseek.com"
$env:DEEPAGENTS_MODEL = "openai:deepseek-flash"
python -m backend.app
~~~

`DEEPAGENTS_MODEL` 直接传给 `create_deep_agent(model=...)`。DeepAgents 通过现有的 `langchain-openai` 接入 DeepSeek 的 OpenAI 兼容 API。

## LangSmith 追踪

LangSmith 是可选项。当前运行容器已经启用 tracing，并将项目写入 `lung-agent`。在 [LangSmith](https://smith.langchain.com/) 的 Tracing Projects 中打开 `lung-agent` 查看每轮主模型和子 Agent 调用。Docker 启动时会读取 `.env`；需要更换项目时配置以下变量：

~~~powershell
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=你的LangSmith密钥
LANGSMITH_PROJECT=lung-agent
~~~

追踪会记录模型调用内容。当前项目使用合成数据，接入真实患者数据前要先确定数据处理和隐私规则。

服务启动后，健康检查地址是 <http://localhost:8010/health>。

## Docker 启动

复制 .env.example 为 .env，填入 DeepSeek API Key，然后执行：

~~~powershell
docker compose up -d --build
~~~

查看运行状态和日志：

~~~powershell
docker compose ps
docker compose logs -f agent-service
~~~

服务只绑定到本机 127.0.0.1:8010。SQLite 调用缓存保存在 storage/。

## 调用主 Agent

统一入口：

    POST /api/v1/agents/{role}/invoke

role 可填 patient、doctor、management 或 nursing。请求示例：

~~~powershell
$body = @{
  taskKey = "demo-patient-001"
  message = "帮我整理一下这次复诊前需要准备的问题"
  context = @{}
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://localhost:8010/api/v1/agents/patient/invoke" -ContentType "application/json" -Body $body
~~~

相同 taskKey 和相同请求会从 SQLite 返回第一次的回复；同一个 taskKey 搭配不同请求会返回 409。请求字段如下：

- taskKey：稳定任务标识，必填。
- message：用户问题，必填。
- context：Gateway 提供的补充业务上下文，可省略。

患者对话可以在 `context` 中提供 `conversationId`，同一会话每轮使用新的 `taskKey`，SQLite checkpoint 会续上对话状态，并按患者编号隔离。`patient_id` 仅接受 `demo-patient-0001` 这种合成编号。长期记忆默认读取并向量归档当前患者的会话，检索模型是 BGE Small 中文版；传 `memoryConsent: false` 会停止读取、停止归档并清除该演示患者的记忆。当前接口没有登录认证，不能接真实患者数据。

患者写入工具会先暂停。接口返回确认提示后，用同一 `conversationId` 和新 `taskKey` 再发请求，并在 `context` 传 `approvePendingWrite: true` 才批准；传 `false` 会拒绝。当前确认适用于本地演示，正式环境要由身份认证和授权服务提供患者身份、同意记录及审计。

## AG-UI SSE 调用

AG-UI 患者入口：`POST /api/v1/agents/patient/ag-ui`。它接收 AG-UI 的 `RunAgentInput`，返回 `text/event-stream`，以 `RUN_STARTED`、文本增量事件和 `RUN_FINISHED` 表示一次对话。请求里 `threadId` 是会话号，每轮用新的 `runId`；同一个 `threadId` 会恢复 SQLite 会话。首次调用若 BGE 模型缓存不存在，会自动下载到 `AGENT_MODEL_DIR`；默认复用旁边知识平台的 `models/` 目录。

浏览器跨域调用可在 `.env` 设置 `AGENT_CORS_ORIGINS`，用逗号分隔允许的页面地址；本地默认 `*` 且不启用 Cookie 凭据。LangSmith 追踪仍由 `LANGSMITH_TRACING=true`、`LANGSMITH_API_KEY` 和 `LANGSMITH_PROJECT` 控制，模型与子 Agent 调用会出现在该项目的 Traces 页面。

PowerShell 用 `curl.exe -N` 读取事件流：

~~~powershell
$body = @'
{"threadId":"patient-demo-thread-01","runId":"run-001","messages":[{"id":"msg-001","role":"user","content":"帮我准备下次复诊的问题"}],"tools":[],"context":[{"description":"patient_id","value":"demo-patient-0001"}],"forwardedProps":{}}
'@
curl.exe -N "http://localhost:8010/api/v1/agents/patient/ag-ui" -H "Content-Type: application/json" -H "Accept: text/event-stream" --data-binary $body
~~~

停止生成可直接关闭 SSE 请求，服务会取消对应的 LangGraph 异步任务。也可以调用 `DELETE /api/v1/agents/patient/runs/{runId}?threadId={threadId}`。接着继续时复用原 `threadId`、换新的 `runId` 并提交最新用户消息；服务会把“用户已打断上一轮生成”的记录写进会话后再处理。浏览器接 AG-UI 客户端时可直接把该 SSE 地址作为 agent endpoint。

写入工具会以 AG-UI `RUN_FINISHED` 的 `outcome.type="interrupt"` 返回待确认项。确认后新建一个 run，带同一个 `threadId` 和 `resume` 数组，例如：

~~~json
{
  "threadId": "patient-demo-thread-01",
  "runId": "run-002",
  "messages": [],
  "tools": [],
  "context": [],
  "forwardedProps": {},
  "resume": [{"interruptId": "上次事件返回的 id", "status": "resolved", "payload": {"approved": true}}]
}
~~~

拒绝时将 `approved` 设为 `false`。服务端会校验该会话全部待确认项，再恢复写入节点。

响应包含 taskKey、role、reply 和 cached。

## 目录与边界

- backend/app.py：HTTP 服务启动入口。
- backend/api/v1/handler.py：HTTP 路由和参数校验。
- backend/gateway/orchestration/gateway.py：按角色选主 Agent，统一执行任务幂等。
- backend/agents/{patient,doctor,management,nursing}/agent.py：四个 DeepAgents 角色主 Agent。
- backend/agents/patient/registry.py：发现患者专科目录并生成 DeepAgents 子 Agent 注册项。
- backend/agents/patient/{symptom_safety_triage,report,exam_order,consultation_prep,psychological_support,insurance_cost,transport_companion,home_care_request}/agent.py：每个患者专科 Agent 的提示词、职责和 MCP 白名单。
- backend/agents/patient/tools.py：主 Agent 的注册中心发现和历史会话检索工具。
- backend/integrations/mcp/patient_fhir.py：FHIR MCP 工具筛选与患者/确认参数绑定。
- backend/integrations/memory/vector_store.py：和知识库分离的患者对话向量归档。
- backend/shared/tasking/idempotency_store.py：SQLite taskKey 回复缓存。
- docs/architecture/agent-topology.md：请求流程和架构边界。

Gateway 是 HTTP 和任务编排唯一入口。每次患者请求都会读取专科注册表；DeepAgents 原生 `task` 工具负责按需委派，主 Agent 只看到职责摘要，不会一次拿到所有 FHIR 工具 schema。被选中的子 Agent 只获得自己的 FHIR MCP 工具。会话 checkpoint、调用缓存和患者向量归档分别保存在独立 SQLite 文件。写工具由 DeepAgents HITL 暂停，患者确认后才发给 FHIR MCP。

当前只接入本地合成患者。医保只查询已有 Coverage，不计算账单或报销；上门护理只有资料核对和待核实草稿，没有正式申请与派单状态机。没有登录认证，因此不得接入真实患者数据。

## AG-UI 文档

AG-UI 官方文档源码快照放在 docs/references/ag-ui/，包含 1.0 和 draft 规范、SDK 页面及配套资源；页面清单、来源版本和 MIT 许可也一并保留。SSE 服务端接入重点见 docs/references/ag-ui/README.md。
