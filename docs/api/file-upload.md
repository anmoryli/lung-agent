# 患者端 AG-UI 的文件识别子接口

> 文件识别是患者端 AG-UI 接口中的一个辅助接口。完整的聊天流、会话、运行取消、模型与资料接口见[患者端 AG-UI 接口总览](ag-ui.md)。本文只保留文件提取接口的详细说明。

该接口接收文件并返回可读文本。患者聊天页面会先调用它，再把返回的文件内容附加到当前问题发送给 Agent。单独调用此接口只负责提取，不会启动对话。

## 基本信息

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/agents/patient/files/extract` |
| 内容类型 | `multipart/form-data` |
| 文件字段 | 建议使用 `files`；服务端会读取请求中所有带文件名的 multipart 部分 |
| 鉴权 | 当前服务没有登录鉴权，仅用于合成数据演示 |

## 支持范围

支持 `.pdf`、`.docx`、`.xlsx`、`.xlsm`、`.xls`、`.png`、`.jpg`、`.jpeg`、`.txt` 和 `.csv`。

一次最多 5 个文件，请求总大小最多 20 MB，单个文件最多 15 MB。返回的识别文本合计最多 12000 字，超过后按文件顺序截断。文件数量、大小超限或扩展名不支持时会返回错误，不会生成部分结果。

PDF 按页提取，表格以 Markdown 表格返回；扫描 PDF 页和图片通过简体中文、英文 OCR 识别。DOCX 按文档顺序提取段落和表格，Excel 按工作表提取表格内容。Docker 镜像已安装 Tesseract 及 `chi_sim`、`eng` 语言包。

## 请求示例

```bash
curl -X POST "http://localhost:8010/api/v1/agents/patient/files/extract" \
  -F "files=@report.pdf" \
  -F "files=@results.xlsx"
```

聊天页面调用成功后，会将 `files` 数组中的文件名和识别文本随 AG-UI 请求发送。服务端把它们标记为“用户上传文件的内容”，附加到当前用户消息，并保存到会话。原始文件不会单独保存。

## 成功响应

```json
{
  "files": [
    {
      "name": "report.pdf",
      "text": "[第 1 页]\n检查结果\n\n| 项目 | 结果 |\n| --- | --- |\n| 血红蛋白 | 120 |",
      "truncated": false
    },
    {
      "name": "results.xlsx",
      "text": "[工作表：检查结果]\n| 项目 | 结果 |\n| --- | --- |\n| 血红蛋白 | 120 |",
      "truncated": false
    }
  ],
  "combinedText": "[文件名：report.pdf]\n...\n\n[文件名：results.xlsx]\n...",
  "truncated": false
}
```

`files[].text` 是单个文件的提取结果；`files[].truncated` 表示该文件内容被截断。`combinedText` 按上传顺序拼接文件名和内容，适合直接用于提示词；顶层 `truncated` 表示本次结果至少有一份文件被截断。

## 错误响应

错误响应统一为 JSON：

```json
{"error": "一次最多上传 5 个文件。"}
```

| 状态码 | 含义 |
| --- | --- |
| `400` | 请求为空、不是 multipart、文件数量/大小超限，或文件类型不支持 |
| `422` | 文件内容无法解析或 OCR 提取失败 |

## 部署说明

本地运行扫描 PDF 或图片 OCR 时，需要安装 Tesseract 和 `chi_sim`、`eng` 语言数据。项目 Dockerfile 已包含这些系统依赖。当前服务没有用户身份认证，请使用合成数据。
