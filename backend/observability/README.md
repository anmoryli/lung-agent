# LangSmith 追踪

DeepAgents 使用的 LangChain 会根据环境变量自动发送追踪，不需要额外封装模型请求。

Docker 启动时可在项目根目录的 `.env` 设置：

~~~dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=你的LangSmith密钥
LANGSMITH_PROJECT=lung-agent
~~~

`.env.example` 默认关闭追踪。追踪内容可能包含输入和模型回复；当前阶段只使用合成数据。
