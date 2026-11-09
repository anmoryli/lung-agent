# Agent Service TODO

## 目标

搭好 ASaaS 后端骨架，并实现四个角色主 Agent 的最小可运行版。

## 已完成

- [x] 阅读 PRD 第 0 章和 Agent 相关约束。
- [x] 搭建 API、Gateway、运行时、共享合同、外部集成和审计目录。
- [x] 按四个主 Agent 建立子 Agent 目录。
- [x] 写出四个角色 DeepAgents 主 Agent 和职责提示词。
- [x] 由 Gateway 统一路由，并用稳定 taskKey 缓存重复请求。
- [x] 加入配置示例、HTTP 服务和 Docker 一键启动文件。
- [x] 更新使用说明并进行静态检查。

## 边界

- Gateway 是唯一任务编排入口；主 Agent 不直接调度彼此或子 Agent。
- 医疗判断由医生确认；四个 Agent 当前只根据请求和上下文生成文字。
- 当前没有登录鉴权、FHIR/MCP 工具、知识检索和 ServiceCase 持久化；使用合成数据。
- taskKey 缓存会把 Agent 回复保存到本地 SQLite；storage 目录不进入 Git。
- [x] 提交本次实现。

## AG-UI 文档镜像

- [x] 根据官方文档源码和站点索引确定完整页面清单。
- [x] 将官方文档源码和配套资源保存到 docs/references/ag-ui/。
- [x] 附上官方 MIT 许可证、来源链接和页面清单。
- [x] 加入可重复运行的同步脚本；核对 174 篇页面、202 个文件。
- [x] 单独提交文档镜像。

## LangSmith 监控与代码整理

- [x] 保留现有 DeepAgents 模型调用方式，不增加 HTTP 请求模型层。
- [x] 配置 DeepSeek V4.1 Flash 模型、API 地址和本地 `.env` 示例。
- [x] 用环境变量接入可选 LangSmith 自动追踪。
- [x] 更新 Docker 配置和启动说明。
- [x] 做静态语法与配置检查，核对改动范围并提交。

## 患者 Agent 实施

- [x] 按 PRD 第 0 章及 FR-001、004–017、022、025–026、028、030、040 划定患者端 MVP 规则和边界。
- [x] 写清患者主 Agent 的意图路由、安全分流、澄清和确认流程与提示词。
- [x] 实现 8 个患者子 Agent 的隔离提示词、职责、路由条件和最小工具集。
- [x] 核对现有 FHIR MCP 服务的传输、工具合同和写入确认规则，接入按角色筛选的 MCP 工具。
- [x] 实现线程上下文与患者沟通偏好记忆；当前接口没有登录身份，只允许合成患者演示编号。
- [x] 补充安全分流、患者 ID 限制、子 Agent 工具范围和 MCP 写参数绑定测试。
- [x] 更新患者 Agent 与架构文档；Docker 重建与合成患者只读对话验证通过。
- [x] 复核 PRD 对照、确认本地 `.env` 未纳入 Git，并完成最小粒度提交。

## 患者 Agent 注册、记忆与 AG-UI 服务

- [x] 将 8 个专科 Agent 定义放入既有专科目录，新增每轮发现的注册中心。
- [x] 给主 Agent 注册专科发现、会话向量检索和非临床长期记忆工具；FHIR 工具按被选子 Agent 懒加载。
- [x] 用 BGE Small 中文 + sqlite-vec 分开存长期记忆和会话归档，默认读取/归档。
- [x] 用 AG-UI `RunAgentInput` 和官方 Python 编码器提供 SSE；增加生成取消、同线程继续和标准 `resume` 确认。
- [x] 更新 AG-UI 调用、CORS、LangSmith 和本地 Docker 说明。
- [ ] 对最新代码重新构建容器，检查健康状态、AG-UI 请求模型和事件编码。
- [ ] 复核目录与文档，并提交本次代码。
