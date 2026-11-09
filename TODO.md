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
