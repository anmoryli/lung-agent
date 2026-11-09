# Agent Service 目录骨架 TODO

## 目标

在 `agent-service` 中搭建后续 ASaaS 开发的后端目录骨架，按 Gateway、患者/医生/管理/护理四个主 Agent 和子 Agent 分层；先做目录与说明，不实现业务逻辑。

## 执行清单

- [x] 阅读 PRD 第 0 章和 Agent 相关约束。
- [x] 建立 HTTP API、Gateway、运行时、共享业务合同、外部集成和审计目录。
- [x] 按四个主 Agent 建立子 Agent 目录。
- [x] 写项目结构与职责说明，保留已有 PRD、`backend/app.py` 和 `frontend/`。
- [x] 核对目录树并提交到 `agent-service` 的 Git 仓库。

## 范围边界

- Gateway 是唯一任务编排入口；子 Agent 不互相直接调度。
- 共享目录预留稳定 `taskKey`、`ServiceCase`、权限与审计合同。
- 医疗判断留给医生确认；此轮只搭目录，不接入接口或模型。
