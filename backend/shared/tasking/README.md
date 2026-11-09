# 任务与幂等

idempotency_store.py 使用 SQLite 按稳定 taskKey 缓存主 Agent 回复。请求内容相同则复用结果；同一个 key 对应不同请求时返回冲突，不会再次调用模型。

当前只覆盖主 Agent 文本请求；ServiceCase 和业务副作用幂等仍待后续接入。
