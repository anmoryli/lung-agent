# 后端

`app.py` 保留为后续 ASGI 服务入口。接口入口进入 Gateway，由 Gateway 唯一负责意图路由、权限检查、任务幂等和子 Agent 调度。
