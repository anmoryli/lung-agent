# API v1

backend/api/v1/handler.py 提供第一版 HTTP 路由，统一调用路径为：

    POST /api/v1/agents/{role}/invoke

角色为 patient、doctor、management、nursing。请求 JSON 必须包含稳定 taskKey 和 message，可附带 context 对象。Gateway 校验角色并调用对应主 Agent；相同任务重试会复用 SQLite 中的第一次回复。
