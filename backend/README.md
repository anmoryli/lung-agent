# 后端

`app.py` 保留为后续 ASGI 服务入口。接口入口进入 Gateway，由 Gateway 唯一负责意图路由、权限检查、任务幂等和子 Agent 调度。

## 静态检查

在 Agent Service 目录安装开发依赖：

```powershell
python -m pip install -r requirements-dev.txt
```

检查代码规范和患者 MCP 工具类型：

```powershell
ruff check backend
mypy
```
