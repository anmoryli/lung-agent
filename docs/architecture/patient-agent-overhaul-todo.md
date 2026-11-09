# 患者 Agent 与 AG-UI 整理 TODO

## 目标

保持 Gateway、角色主 Agent、专科 Agent 和 integrations 分层，让患者服务具备动态发现、按需工具、语义会话记忆和 AG-UI SSE。

## 执行项

- [x] 查 AG-UI 官方 Python SDK、RunAgentInput、SSE 编码、运行中断和恢复合同。
- [x] 查 DeepAgents 官方 harness 与委派方式，继续用框架原生 `task` 工具执行子 Agent。
- [x] 按现有目录分层整理，把每个患者专科定义放回自己的专科目录。
- [x] 增加患者专科注册/发现中心，每轮请求读取注册表。
- [x] 用 LangChain `@tool` 注册主 Agent 的专科发现和历史会话检索工具。
- [x] MCP 工具仍按专科白名单加载；主 Agent 只拿职责目录，不拿完整工具 schema。
- [x] 用 BGE Small 中文和 sqlite-vec 单独存储、召回患者会话；默认读取/归档，明确关闭时清除。
- [x] 加 AG-UI RunAgentInput SSE 入口、文本事件、线程续接、取消和用户中断记录。
- [x] 把写入确认映射到 AG-UI `resume`，服务端校验同一会话全部待确认项。
- [x] 更新调用说明、目录说明、LangSmith 和 Docker 配置。
- [ ] 做依赖安装、语法检查、SSE/恢复/向量库核心行为检查。
- [ ] 复核所有文档与变更范围，提交本次变更。

## 约束

- Gateway 仍是外部路由唯一入口。
- 患者 ID 仅接受合成演示编号；现有服务没有用户身份认证。
- 新建预约、申请或其他写入必须经患者明确确认；临床判断由医生确认。
- 向量记忆放 `patient-memory-vectors.sqlite3`，不与 FHIR、知识向量库或 checkpoint 混用。
