# Gateway

系统级总入口与唯一编排中枢。orchestration/gateway.py 根据 API 提供的角色选择主 Agent，并通过共享任务存储复用稳定 taskKey 的结果。

当前版本由 backend/app.py 接收 HTTP 请求，Gateway 不让主 Agent 直接调用其他 Agent。后续新增子 Agent 时，仍由 Gateway 决定和执行调用。
