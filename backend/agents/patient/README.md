# 患者 Agent 怎么分工

## 目录角色

```text
patient/
├── agent.py                 患者主 Agent：识别诉求、查工具目录、派发一次专科任务
├── tools.py                 主 Agent 自己能用的业务工具
├── specialist.py            专科定义的数据结构；它本身不运行模型
├── registry.py              扫描各专科目录，把定义转换成 DeepAgents 子 Agent 配置
├── symptom_safety_triage/
├── report/
├── exam_order/
├── consultation_prep/
├── psychological_support/
├── insurance_cost/
├── transport_companion/
└── home_care_request/       每个专科目录的 agent.py 声明职责、提示词、读写工具名单
```

`specialist.py` 里的 `PatientSpecialist` 是一份专科配置合同，字段包括名称、职责、提示词和 MCP 读写工具白名单。每个子目录中的 `agent.py` 创建一份 `SPECIALIST` 配置。`registry.py` 找到这些配置后，组装成 DeepAgents 原生子 Agent 描述，再由 DeepAgents 的 `task` 工具运行被选中的专科。这里没有第二个独立 Gateway，也没有 `specialist.py` 自己启动的 Agent。

## 主 Agent 可用工具

主 Agent 在一次请求里能看到：

| 工具 | 用途 |
|---|---|
| `discover_patient_agents` | 查找专科名称和职责摘要，不返回全部 MCP schema |
| `get_current_time` | 获取当前日期、时间和时区；记忆关闭时仍可用 |
| `search_patient_history` | 按语义检索这位合成患者的会话归档 |
| `save_patient_memory` | 用户要求记住信息时，写入长期记忆；停用记忆时不注册 |
| DeepAgents `task` | 把当前任务交给一个已注册的专科子 Agent |
| DeepAgents `write_todos` | 维护当前任务计划；AG-UI 状态推送开启时同步显示计划 |

`task` 和 `write_todos` 是 DeepAgents 的内建工具。FHIR MCP 的查询和写入工具不直接给主 Agent，因此主 Agent 不能自行读写病历。

每次请求都会按 `AGENT_TIMEZONE` 获取一次当前时间，连同相对日期计算规则注入患者主 Agent 和专科 Agent 的提示词；记忆抽取模型也会收到同一时区下的当前时间。主 Agent 与专科 Agent 都可以主动调用 `get_current_time` 刷新时刻。默认时区为 `Asia/Shanghai`。

## 专科子 Agent 的工具白名单

每个专科只拿到表内工具；写工具会在执行前触发 HITL 确认。

| 专科 | 可读 FHIR MCP 工具 | 可写 FHIR MCP 工具 |
|---|---|---|
| 症状与安全分流 | `get_patient_summary`, `search_observations`, `search_conditions`, `search_medication_requests`, `search_allergies` | 无 |
| 报告 | `search_reports`, `get_report`, `search_documents`, `get_document`, `get_report_pdf` | 无 |
| 检查与医嘱 | `search_service_requests`, `search_medication_requests`, `search_appointments`, `search_observations` | `create_service_request_draft`, `create_appointment` |
| 问诊准备 | `get_patient_summary`, `search_encounters`, `search_reports`, `search_documents`, `search_service_requests`, `search_appointments` | 无 |
| 心理支持 | `get_patient_summary` | `create_service_case`, `create_task` |
| 医保与费用 | `search_coverages`, `search_encounters` | `create_service_case`, `create_task` |
| 交通与陪同 | `search_appointments`, `search_healthcare_services`, `search_organizations` | `create_appointment`, `create_service_case`, `create_task` |
| 上门护理申请 | `search_healthcare_services`, `search_care_plans`, `search_service_requests`, `search_consents`, `search_allergies` | `create_service_case`, `create_task` |

白名单定义在各专科目录的 `agent.py`，`registry.py` 会在建图时检查 MCP 服务是否提供这些工具，并只把对应 schema 装进该子 Agent。服务端还会固定绑定患者 ID 和任务键；模型不能通过工具参数替换这些值。

## 状态和持久化位置

- `AGENT_STORAGE_PATH/patient-checkpoints.sqlite3`：LangGraph 会话 checkpoint，包括消息、TODO 状态和 HITL 暂停点。Docker 默认挂载到 `agent-service/storage/`。
- `AGENT_STORAGE_PATH/patient-memory-vectors.sqlite3`：长期记忆事实/短摘要、BGE 会话归档向量和召回审计。长期记忆表与向量归档表分开。
- `AGENT_STORAGE_PATH/patient-sessions.sqlite3`：服务端会话目录与当前调试模型选择、模型能力元数据。
- `backend/runtime/active_runs.py`：当前 SSE 连接、运行 Future、生成取消标记只在进程内存里；进程重启后不会保留正在生成的运行状态。

迁移单机时，停掉服务后复制整个 `storage/`，在新机器上继续使用同一个 `AGENT_STORAGE_PATH` 和 `threadId`。另需配置模型密钥、FHIR MCP 地址与凭据、模型目录。若只迁移 Agent，不迁移 HAPI 数据库，FHIR 资源留在原服务器；整套环境迁移时还要单独迁移 MCP/HAPI 的数据库卷。当前 SQLite checkpoint 适合单机演示，不适合多副本同时共享写入。

## 记忆写入与上下文

患者明确要求“记住”时，`save_patient_memory` 立即写入带重要度、置信度、来源、原话证据、有效期和版本关系的记忆。每轮结束后，用户原话会进入 SQLite 持久队列，由后台 worker 定时抽取稳定事实；服务重启会恢复未完成任务。普通抽取必须通过原文引用校验。每轮开始时维护一段不超过约 900 字符的短摘要，并注入主 Agent 和专科 Agent。个人中心显示抽取队列状态。详细规则见 `docs/architecture/patient-memory-design.md`。

对话归档单独做向量检索，每轮先取 4 条；主 Agent 可以按需再取 5 条。归档不会覆盖长期记忆，也不会直接当作当前事实。模型服务能力清单提供总上下文和最大输出，服务按“总窗口减最大输出”计算保守输入预算，并传给 DeepAgents。当前版本在输入预算的 85% 处摘要旧对话、保留最近 10%，也会在摘要前裁剪较大的旧工具参数；模型超限时还会摘要重试。前端展示模型总窗口、输入预算、摘要线和本轮输入估算。SSE 用 `ACTIVITY_SNAPSHOT` 显示 Agent/模型状态和各模型首个流式片段耗时，只将患者主 Agent 可见文本送到对话消息里。
