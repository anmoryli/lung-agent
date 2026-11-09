"""连接 FHIR MCP，并把病人和写入确认参数锁在服务端。"""

from __future__ import annotations

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
    from pydantic import Field, create_model

    source_schema = raw_tool.args_schema
    if isinstance(source_schema, dict):
        source_fields = source_schema.get("properties", {})
        required_fields = set(source_schema.get("required", []))
        property_specs = {
            name: (spec, name in required_fields)
            for name, spec in source_fields.items()
        }
    else:
        source_fields = source_schema.model_fields
        property_specs = {
            name: ({"type": _python_type(field.annotation), "default": field.default}, field.is_required())
            for name, field in source_fields.items()
        }

    fields: dict[str, tuple[Any, Any]] = {}
    hidden = {"patient_id", "task_key", "confirmed", "doctor_confirmed", "practitioner_id"}
    for field_name, (spec, is_required) in property_specs.items():
        if field_name in hidden:
            continue
        annotation = _json_schema_type(spec)
        default = ... if is_required else spec.get("default", None)
        if "description" in spec:
            default = Field(default, description=spec["description"])
        fields[field_name] = (annotation, default)
    public_schema = create_model(f"{raw_tool.name.title().replace('_', '')}Input", **fields)

    async def call_tool(**arguments: Any) -> Any:
        payload = dict(arguments)
        original_fields = source_fields
        if "patient_id" in original_fields:
            payload["patient_id"] = patient_id
        if "task_key" in original_fields:
            payload["task_key"] = f"{task_key}:{raw_tool.name}"[:128]
        if "confirmed" in original_fields:
            # 只有 HITL 中间件获批后才会调用到这里。
            payload["confirmed"] = True
        if "doctor_confirmed" in original_fields:
            payload["doctor_confirmed"] = False
        if "practitioner_id" in original_fields:
            payload["practitioner_id"] = ""
        return await raw_tool.ainvoke(payload)

    return StructuredTool.from_function(
        name=raw_tool.name,
        description=raw_tool.description,
        coroutine=call_tool,
        args_schema=public_schema,
    )


def _json_schema_type(spec: dict[str, Any]) -> Any:
    """把 MCP JSON Schema 的常用类型转成模型工具能用的 Python 类型。"""
    from typing import Literal

    schema_type = spec.get("type")
    if schema_type == "array":
        base = list[_json_schema_type(spec.get("items", {}))]
    else:
        base = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "object": dict[str, Any],
        }.get(schema_type, Any)
    choices = spec.get("enum")
    if choices and spec.get("type") == "string":
        return Literal.__getitem__(tuple(choices))
    return base


def _python_type(annotation: Any) -> str:
    """Pydantic 字段转成 JSON Schema 的基础类型名。"""
    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        return "array"
    if origin is dict:
        return "object"
    return {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
    }.get(annotation, "object")
