# 患者知识检索

患者主 Agent 按需调用 `search_cancer_knowledge`，访问独立知识平台，不读取 FHIR 病历数据。`mode="local"` 面向具体术语和问题；`mode="global"` 遍历全部知识类别社区，返回整体主题摘要和来源证据。

两种查询都保留资料标题、发布机构和原始链接。知识平台未连接时，工具返回明确的不可用状态。知识检索只提供公开科普资料，不用于推断患者个人病情或代替医生判断。

容器部署时用 `KNOWLEDGE_PLATFORM_URL` 指向知识平台。默认值是 `http://127.0.0.1:8020`；Docker Compose 下使用 `http://host.docker.internal:8020`。
