# 患者端 AG-UI 接口

本文档覆盖患者聊天页面使用的整套 HTTP 接口：AG-UI 对话流、文件识别、会话读取与导入、运行取消、模型切换和患者资料。文件识别是其中一个辅助接口，识别出的文本会随 AG-UI 对话请求交给 Agent。

## 通用约定

- 本地服务地址：`http://localhost:8010`。下文路径都相对于服务地址。
- 当前服务没有登录认证，只接受 `demo-patient-0001` 格式的合成患者编号；不要传真实患者数据。
- JSON 请求使用 UTF-8，通用 JSON 请求体上限为 1 MB。文件识别接口使用独立的 20 MB 上限。
- 返回 JSON 的接口使用 `application/json; charset=utf-8`；对话接口返回 `text/event-stream`。
- 支持通过 `AGENT_CORS_ORIGINS` 配置跨域来源，默认允许任意来源。
- 出错时 JSON 接口通常返回 `{"error":"说明文字"}`。AG-UI 流开始后发生的错误会以 `RUN_ERROR` 事件返回。

## 接口总览

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/agents/patient/ag-ui` | 发送一轮 AG-UI 对话并接收 SSE 事件 |
| `POST` | `/api/v1/agents/patient/files/extract` | 识别上传文件，返回文本供对话使用 |
| `GET` | `/api/v1/agents/patient/sessions?patientId=...` | 列出患者会话 |
| `GET` | `/api/v1/agents/patient/sessions/{threadId}?patientId=...` | 读取一条会话 |
| `POST` | `/api/v1/agents/patient/sessions/import` | 导入旧版页面的会话文本 |
| `DELETE` | `/api/v1/agents/patient/runs/{runId}?threadId=...` | 取消运行中的一轮对话 |
| `GET` | `/api/v1/agents/patient/models` | 获取可选模型与当前选择 |
| `POST` | `/api/v1/agents/patient/model` | 切换患者聊天使用的模型 |
| `GET` | `/api/v1/agents/patient/profile?patientId=...` | 获取患者演示资料、会话和记忆调试信息 |
| `GET` | `/health` | 检查服务是否启动 |

## 1. 发送 AG-UI 对话

`POST /api/v1/agents/patient/ag-ui`

请求体遵循 AG-UI `RunAgentInput`。每轮必须使用新的 `runId`；同一会话继续对话时复用 `threadId`，并在 `messages` 中带上对话历史。`threadId` 和 `runId` 只能包含字母、数字、下划线、点、冒号和短横线，长度最多 128 个字符。最后一条用户消息最多 20000 字；附加上传文件内容后，这条消息最多 32000 字。

```json
{
  "threadId": "patient-demo-thread-01",
  "runId": "run-001",
  "messages": [
    {"id": "msg-001", "role": "user", "content": "帮我看一下这份检查报告"}
  ],
  "tools": [],
  "context": [
    {"description": "patient_id", "value": "demo-patient-0001"}
  ],
  "forwardedProps": {}
}
```

请求头：

```http
Content-Type: application/json
Accept: text/event-stream
```

响应是 SSE。常见事件包括：

| 事件 | 内容 |
| --- | --- |
| `RUN_STARTED` | 本轮开始 |
| `TEXT_MESSAGE_START`、`TEXT_MESSAGE_CONTENT`、`TEXT_MESSAGE_END` | 助手回复及其增量文本 |
| `STATE_SNAPSHOT` | Agent 运行状态和进度 |
| `ACTIVITY_SNAPSHOT` | 主 Agent、专科 Agent 的运行轨迹 |
| `RUN_FINISHED` | 本轮结束；若等待用户确认，`outcome.type` 为 `interrupt` |
| `RUN_ERROR` | 流启动后的运行错误 |

可用 `curl` 查看原始事件流：

```bash
curl -N "http://localhost:8010/api/v1/agents/patient/ag-ui" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  --data-binary @run-input.json
```

### AG-UI context

服务端当前识别这些 context 项：

| `description` | `value` | 用途 |
| --- | --- | --- |
| `patient_id` | `demo-patient-0001` 格式的患者编号 | 选择演示患者；省略时使用 `PATIENT_DEMO_DEFAULT_ID` 或默认编号 |
| `memoryConsent` | 字符串 `"true"` 或 `"false"` | 允许或关闭该患者的记忆读取与归档 |
| `approvePendingWrite` | 字符串 `"true"` 或 `"false"` | 标记用户是否批准待确认的写入操作；实际恢复还需附带 `resume` |
| `uploadedFiles` | JSON 序列化后的文件数组字符串 | 附加文件识别结果，格式见“文件识别”一节 |

未知的 context 项会被忽略。待确认操作需要把 `RUN_FINISHED.outcome.interrupts` 返回的 interrupt ID 放入下一轮 `resume`，并使用新 `runId`。批准示例：

```json
{
  "threadId": "patient-demo-thread-01",
  "runId": "run-002",
  "messages": [
    {"id": "msg-001", "role": "user", "content": "帮我预约复诊"},
    {"id": "msg-002", "role": "assistant", "content": "请确认预约信息"}
  ],
  "tools": [],
  "context": [
    {"description": "patient_id", "value": "demo-patient-0001"},
    {"description": "approvePendingWrite", "value": "true"}
  ],
  "resume": [
    {"interruptId": "interrupt-id-from-previous-run", "status": "resolved", "payload": {"approved": true}}
  ],
  "forwardedProps": {}
}
```

拒绝时将 `approved` 改为 `false`。恢复请求仍需携带原 `threadId` 和完整对话历史。

**状态码：**`200` 表示 SSE 流已建立；`400` 表示请求体或 ID 无效；`409` 表示该会话已有运行中的任务；`503` 表示模型窗口信息不可用；`500` 表示保存会话失败。流建立后发生的 Agent 错误通过 `RUN_ERROR` 事件返回。

## 2. 文件识别

`POST /api/v1/agents/patient/files/extract`

请求类型为 `multipart/form-data`，文件字段建议命名为 `files`，可重复添加多个文件。

```bash
curl -X POST "http://localhost:8010/api/v1/agents/patient/files/extract" \
  -F "files=@report.pdf" \
  -F "files=@results.xlsx"
```

支持 PDF、DOCX、XLSX、XLS、PNG、JPEG、TXT 和 CSV；一次最多 5 个文件，请求总大小不超过 20 MB，单个文件不超过 15 MB。识别文本合计最多 12000 字，超出时按上传顺序截断。PDF 按页提取，表格转成 Markdown；扫描 PDF 和图片使用简体中文、英文 OCR；Word 表格和电子表格工作表会保留结构边界。

成功响应：

```json
{
  "files": [
    {"name": "report.pdf", "text": "[第 1 页]\n检查结果\n| 项目 | 结果 |", "truncated": false}
  ],
  "combinedText": "[文件名：report.pdf]\n[第 1 页]\n检查结果\n| 项目 | 结果 |",
  "truncated": false
}
```

`files[].text` 是单文件结果，`files[].truncated` 是该文件是否被截断。`combinedText` 按顺序带上文件名并拼接，顶层 `truncated` 表示本次至少有一份文件被截断。聊天页面将文件名和文本放入 AG-UI context 的 `uploadedFiles`，服务端会将其标为用户上传文件内容，附加到当前用户消息并保存到会话。单独调用识别接口只提取文本，不会启动 Agent 对话；原始文件不会单独保存。

`uploadedFiles` 值为 JSON 字符串，元素形如：

```json
[
  {"name": "report.pdf", "text": "提取出的文本"}
]
```

**状态码：**`200` 成功；`400` 请求格式、文件数量、大小或扩展名不符合要求；`422` 文件内容无法解析或 OCR 失败。

扫描 PDF 和图片 OCR 依赖 Tesseract 与 `chi_sim`、`eng` 语言数据。Docker 镜像已包含这些依赖。

## 3. 会话接口

会话数据按 `patientId` 隔离，编号需符合 `demo-patient-0001` 格式。

### 列出会话

`GET /api/v1/agents/patient/sessions?patientId=demo-patient-0001`

返回 `{"sessions":[...]}`。每项包含 `thread_id`、`title`、`status`、`created_at` 和 `updated_at`。

### 读取会话

`GET /api/v1/agents/patient/sessions/{threadId}?patientId=demo-patient-0001`

返回 `{"session":{...}}`，会话对象包含 `thread_id`、`patient_id`、`title`、`messages`、`agent_state`、`pending_interrupts`、`status`、`created_at` 和 `updated_at`。会话不存在时返回 `404`。

### 导入旧会话

`POST /api/v1/agents/patient/sessions/import`

```json
{
  "threadId": "patient-demo-thread-01",
  "patientId": "demo-patient-0001",
  "messages": [
    {"id": "msg-001", "role": "user", "content": "旧会话内容"},
    {"id": "msg-002", "role": "assistant", "content": "旧回复"}
  ]
}
```

只保留 `user`、`assistant` 角色的文本消息，成功返回 `{"threadId":"...","status":"imported"}`。

## 4. 取消一轮运行

`DELETE /api/v1/agents/patient/runs/{runId}?threadId={threadId}`

取消匹配到的运行中任务，成功返回 HTTP `202` 和 `{"status":"cancelled","threadId":"..."}`。运行不存在或 `threadId` 不匹配时返回 `404`。断开 SSE 连接也会触发取消。

## 5. 模型接口

### 获取模型

`GET /api/v1/agents/patient/models`

返回模型列表和当前选择：

```json
{
  "models": [
    {"id": "model-id", "name": "模型名称", "context_window": 64000, "max_output_tokens": 8000, "input_budget_tokens": 56000}
  ],
  "selected": {"model_id": "model-id", "display_name": "模型名称", "context_window": 64000, "max_output_tokens": 8000}
}
```

列表来自配置的模型服务 `/models` 接口，仅包含服务提供上下文窗口和最大输出 token 信息的模型。

### 切换模型

`POST /api/v1/agents/patient/model`

```json
{"modelId": "model-id"}
```

成功返回 `{"selected":{...}}`。模型 ID 必须存在于当前可选模型列表，否则返回 `400`；模型服务不可用时返回 `502`。

## 6. 患者资料与健康检查

### 患者演示资料

`GET /api/v1/agents/patient/profile?patientId=demo-patient-0001`

返回 `patientId`、FHIR 查询结果、记忆列表与概况、记忆提取状态、检索记录、会话和存储位置等调试数据。FHIR MCP 未启用或调用失败时返回 `502`。

### 服务健康检查

`GET /health`

成功返回 `{"status":"ok"}`。
