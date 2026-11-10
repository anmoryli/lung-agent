"""连接 FHIR MCP，并把病人和写入确认参数锁在服务端。"""

from __future__ import annotations

from copy import deepcopy
import os
import re
from typing import Any


WRITE_TOOLS = {
    "create_appointment",
    "create_service_case",
    "create_task",
    "create_service_request_draft",
}


def patient_id_from_context(context: dict[str, Any]) -> str | None:
    """演示环境只接受合成患者编号。正式环境要从登录身份取患者编号。"""
    patient_id = context.get("patient_id") or os.getenv("PATIENT_DEMO_DEFAULT_ID", "demo-patient-0001")
    if not isinstance(patient_id, str) or not re.fullmatch(r"demo-patient-\d{4}", patient_id):
        raise ValueError("患者服务当前只接受 demo-patient-0001 格式的合成患者编号。")
    return patient_id


def mcp_url() -> str:
    return os.getenv("FHIR_MCP_URL", "http://127.0.0.1:8001/mcp").strip()


def mcp_transport() -> Any:
    """Docker 用宿主机地址连入，但保留 MCP 服务识别的本机 Host。"""
    from fastmcp.client.transports import StreamableHttpTransport

    headers = {"Host": os.getenv("FHIR_MCP_HOST_HEADER", "localhost:8001")}
    token = os.getenv("MCP_BEARER_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return StreamableHttpTransport(mcp_url(), headers=headers)


def make_patient_tool(raw_tool: Any, patient_id: str, task_key: str) -> Any:
    """去掉模型可填写的患者、确认和幂等键，由服务端统一补上。"""
    from langchain_core.tools import StructuredTool
    from jsonschema import ValidationError, validators

    source_schema = raw_tool.args_schema
    if not isinstance(source_schema, dict):
        source_schema = source_schema.model_json_schema()
    if not isinstance(source_schema, dict):
        raise TypeError(f"MCP 工具 {raw_tool.name} 没有可用的 JSON Schema。")
    if source_schema.get("type") != "object":
        raise ValueError(f"MCP 工具 {raw_tool.name} 的参数 schema 根节点必须是 object。")

    hidden = {"patient_id", "task_key", "confirmed", "doctor_confirmed", "practitioner_id"}
    public_schema = deepcopy(source_schema)
    properties = public_schema.get("properties", {})
    if isinstance(properties, dict):
        public_schema["properties"] = {
            name: value for name, value in properties.items() if name not in hidden
        }
    required = public_schema.get("required", [])
    if isinstance(required, list):
        public_schema["required"] = [name for name in required if name not in hidden]

    _reject_remote_schema_references(source_schema)
    validator_class = validators.validator_for(source_schema)
    validator_class.check_schema(source_schema)
    validator = validator_class(
        source_schema,
        format_checker=validator_class.FORMAT_CHECKER,
    )
    source_fields = source_schema.get("properties", {})

    async def call_tool(**arguments: Any) -> Any:
        payload = dict(arguments)
        if "patient_id" in source_fields:
            payload["patient_id"] = patient_id
        if "task_key" in source_fields:
            payload["task_key"] = f"{task_key}:{raw_tool.name}"[:128]
        if "confirmed" in source_fields:
            # 只有 HITL 中间件获批后才会调用到这里。
            payload["confirmed"] = True
        if "doctor_confirmed" in source_fields:
            payload["doctor_confirmed"] = False
        if "practitioner_id" in source_fields:
            payload["practitioner_id"] = ""
        try:
            validator.validate(payload)
        except ValidationError as error:
            field_path = ".".join(str(part) for part in error.absolute_path) or "参数"
            raise ValueError(
                f"工具 {raw_tool.name} 的 {field_path} 不符合 MCP 参数规则。"
            ) from None
        return await raw_tool.ainvoke(payload)

    return StructuredTool(
        name=raw_tool.name,
        description=raw_tool.description,
        coroutine=call_tool,
        args_schema=public_schema,
    )


def _reject_remote_schema_references(schema: Any) -> None:
    """只接受工具 schema 自己带的引用，避免校验时访问外部地址。"""
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                not isinstance(value, str) or not value.startswith("#")
            ):
                raise ValueError("MCP 工具 schema 只能引用自身定义，不能引用外部地址。")
            _reject_remote_schema_references(value)
    elif isinstance(schema, list):
        for value in schema:
            _reject_remote_schema_references(value)
