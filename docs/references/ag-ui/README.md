# AG-UI 官方文档镜像

本目录保存 AG-UI 官方仓库 docs/ 下的完整文档源码和资源，包含 174 篇 Markdown/MDX 页面、共 202 个文件。同步时间：2026-11-09T02:06:48+00:00。

- 官方文档站：<https://docs.ag-ui.com>
- 官方站页面清单：<https://docs.ag-ui.com/llms.txt>
- 官方源码：<https://github.com/ag-ui-protocol/ag-ui>
- 本地文档和图片：official-docs/
- 抓取版本、文件清单和大小：manifest.json
- MIT 许可：LICENSE

页面、导航配置和配套资源按上游文件保留；完整协议文本以 official-docs/spec/1.0/ 为准，草案放在 official-docs/spec/draft/。

在 agent-service 根目录重复同步最新版：

    python scripts/sync_agui_docs.py
### SSE 接入相关页面

- 服务端快速开始：official-docs/quickstart/server.mdx
- SSE 传输规范：official-docs/spec/1.0/basic/transports/http-sse.mdx
- 事件总览：official-docs/spec/1.0/events/index.mdx
- 运行输入：official-docs/spec/1.0/basic/run-input.mdx
