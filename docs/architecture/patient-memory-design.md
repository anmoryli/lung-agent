# 患者长期记忆与对话归档

设计参考：[用户给出的 CSDN 文章](https://blog.csdn.net/Anmory/article/details/170035015)。当前环境无法读取该页面正文，以下实现按仓库已有阅读记录和用户明确提出的独立短摘要、权重、过期、冲突与版本要求落地；不声称逐项复现文章原文。

## 两种数据各自做什么

### 长期记忆

长期记忆不进向量归档表。SQLite 的 `patient_long_term_memories` 保存一条事实及其患者编号、类别、重要度、置信度、来源、证据、创建/更新时间、有效期、状态、替代关系、冲突关系、最近使用时间和访问次数。

每轮运行前会做一次维护，并把最新的短摘要放进主 Agent 和专科 Agent 的系统提示词：

1. 已到 `expires_at` 的条目改为 `expired`，后续不进入提示词。
2. 依据重要度、置信度和距最近确认/修订的时间重算权重：`importance × 20 × confidence × 2^(-ageDays / (30 × importance))`，上限 100。半衰期是 `30 × importance` 天。每轮注入只记录访问次数，不会刷新记忆时间或抬高权重。
3. 权重低于 12 的条目标成 `stale`，留在数据库供个人中心审计，不进入当前摘要。
4. 其余条目按权重从高到低写进不超过 900 个字符的摘要，保存到 `patient_long_term_profiles`。
5. 主 Agent 和被选中的专科 Agent 每次建图时都会收到这段摘要。没有长期记忆时，也会明确注入“暂无已确认的长期偏好或服务安排”。

用户明确要求记住时，`save_patient_memory` 可以设置类别、重要度 1–5 和过期天数。用户明确纠正旧记忆时提供 `supersedes_id`，旧版本保留为 `superseded`；新旧关系无法判断时提供 `conflicts_with_id`，冲突双方标成 `conflicted`，都不进摘要。过期、陈旧、冲突、替代条目都不会被物理删除。

当前写入仍由用户明确表达“记住”触发，服务没有对每条普通对话额外跑一遍自动抽取模型。保存证据字段留存触发记忆的原话，系统也不把症状、报告、诊断或用药存进长期偏好档案。

### 对话归档

用户原话和助手回复分别写入 `patient_memory_vectors` 的 `archive` 类别，用 BGE Small 中文模型生成 512 维向量。每轮开始时，只在同一患者的 `archive` 中做余弦相似度检索，主 Agent取前 4 条；需要更多历史时，`search_patient_history` 可再取前 5 条。归档内容不是当前事实，模型必须结合时间和来源判断。

每次真实检索会记录查询文本、会话编号、命中内容、来源和分数到同一 SQLite 文件的 `patient_memory_retrievals`。个人中心展示长期记忆摘要、全部记忆状态和最近 100 次实际召回。

## 表和文件位置

| 内容 | 存储位置 |
|---|---|
| 长期记忆条目、摘要、状态和权重 | `AGENT_STORAGE_PATH/patient-memory-vectors.sqlite3` 的 `patient_long_term_memories`、`patient_long_term_profiles` |
| 向量化的对话归档 | 同文件的 sqlite-vec 表 `patient_memory_vectors`，仅 `kind=archive` 参与历史向量检索 |
| 召回审计 | 同文件的 `patient_memory_retrievals` |
| 原始会话和图 checkpoint | `patient-sessions.sqlite3`、`patient-checkpoints.sqlite3` |

## 当前边界

目前长期记忆工具只接受明确“记住”意图；普通对话不自动提炼，也没有后台定时反思任务。对话归档用向量相似度，不包含 BM25、图扩展或混合重排。医疗事实继续留在 FHIR 和会话/报告数据里，不塞进患者偏好摘要。
