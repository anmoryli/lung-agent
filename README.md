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

响应包含 taskKey、role、reply 和 cached。

## 目录与边界

- backend/app.py：HTTP 服务启动入口。
- backend/api/v1/handler.py：HTTP 路由和参数校验。
- backend/gateway/orchestration/gateway.py：按角色选主 Agent，统一执行任务幂等。
- backend/agents/{patient,doctor,management,nursing}/agent.py：四个 DeepAgents 角色主 Agent。
- backend/shared/tasking/idempotency_store.py：SQLite taskKey 回复缓存。
- docs/architecture/agent-topology.md：请求流程和架构边界。

主 Agent 目前只根据请求和传入上下文生成文字回复，没有接入子 Agent、FHIR、MCP、知识检索、登录授权或 ServiceCase 持久化。此阶段请使用合成数据。

## AG-UI 文档

AG-UI 官方文档源码快照放在 docs/references/ag-ui/，包含 1.0 和 draft 规范、SDK 页面及配套资源；页面清单、来源版本和 MIT 许可也一并保留。SSE 服务端接入重点见 docs/references/ag-ui/README.md。
