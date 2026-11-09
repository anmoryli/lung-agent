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

LangSmith 是可选项。Docker 启动时会读取 `.env`；需要追踪时配置以下变量：

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

患者对话可以在 `context` 中提供 `conversationId`，同一会话每轮使用新的 `taskKey`，SQLite checkpoint 会续上对话状态，并按患者编号隔离。`patient_id` 仅接受 `demo-patient-0001` 这种合成编号。患者长期记忆只在 `memoryConsent: true` 时保存 `memoryPreference` 中的 `language`、`communication_style`、`accessibility` 三类表达偏好；传 `memoryConsent: false` 会清除该演示患者的偏好。系统不会保存病历事实。当前接口没有登录认证，不能接真实患者数据。

患者写入工具会先暂停。接口返回确认提示后，用同一 `conversationId` 和新 `taskKey` 再发请求，并在 `context` 传 `approvePendingWrite: true` 才批准；传 `false` 会拒绝。当前确认适用于本地演示，正式环境要由身份认证和授权服务提供患者身份、同意记录及审计。

响应包含 taskKey、role、reply 和 cached。

## 目录与边界

- backend/app.py：HTTP 服务启动入口。
- backend/api/v1/handler.py：HTTP 路由和参数校验。
- backend/gateway/orchestration/gateway.py：按角色选主 Agent，统一执行任务幂等。
- backend/agents/{patient,doctor,management,nursing}/agent.py：四个 DeepAgents 角色主 Agent。
- backend/agents/patient/subagents.py：患者 8 个子 Agent 的职责、提示词和工具白名单。
- backend/integrations/patient_mcp.py：FHIR MCP 工具筛选与患者/确认参数绑定。
- backend/shared/tasking/idempotency_store.py：SQLite taskKey 回复缓存。
- docs/architecture/agent-topology.md：请求流程和架构边界。

Gateway 是 HTTP 和任务编排唯一入口。患者主 Agent 在 Gateway 调用内部委派到 8 个隔离子 Agent；子 Agent 各自只拿到工作所需的 FHIR MCP 工具。会话 checkpoint 和患者表达偏好使用 SQLite。写工具由 DeepAgents HITL 暂停，患者确认后才发给 FHIR MCP。

当前只接入本地合成患者。医保只查询已有 Coverage，不计算账单或报销；上门护理只有资料核对和待核实草稿，没有正式申请与派单状态机。没有登录认证，因此不得接入真实患者数据。

## AG-UI 文档

AG-UI 官方文档源码快照放在 docs/references/ag-ui/，包含 1.0 和 draft 规范、SDK 页面及配套资源；页面清单、来源版本和 MIT 许可也一并保留。SSE 服务端接入重点见 docs/references/ag-ui/README.md。
