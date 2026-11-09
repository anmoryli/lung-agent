# 患者 Agent 状态与工具整理 TODO

- [x] 阅读用户博客与 AG-UI、DeepAgents 官方资料，确认长期记忆、状态事件和中断恢复语义。
- [x] 梳理运行时 checkpoint、SSE 断开取消、HITL 暂停与恢复分别存在哪里、迁移机器要带哪些文件。
- [x] 对照当前代码，把主 Agent、专科注册中心、specialist 描述层和专科 Agent 的职责/工具权限列清楚。
- [x] 说明长期记忆何时提取、何时检索、何时归档，并指出现有代码与目标设计的差异。
- [x] 给 AG-UI 状态和活动推送做可配置实现，避免暴露隐藏推理内容。
- [x] 更新架构文档和入口 README；核对文件引用、语法与 Git 状态。
- [ ] 清理 MCP 仓库的迁移记录（根目录 AGENTS.md 要求删除文件前二次确认；已核实目标仅为 `mcp-service/docs/architecture-migration-todo.md`）。
