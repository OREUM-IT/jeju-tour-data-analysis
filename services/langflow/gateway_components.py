"""Gateway-only request scope and final validation components; no model calls.

Embed one Component class plus the shared helpers and argument models into each
new flow node. The existing AWS and public experiment flows are unchanged.
"""
import asyncio
import json
import re
from typing import Annotated

from langchain_core.tools import StructuredTool, Tool, ToolException
from pydantic import BaseModel, ConfigDict, Field, field_validator

import httpx
from lfx.custom.custom_component.component import Component
from lfx.io import MessageInput, Output, SecretStrInput
from lfx.schema.message import Message


VALIDATION_BASE_URL = "http://jeju-mcp:8788"
MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
BLOCKED_MESSAGE = "요청 조건과 장소 정보를 확인하지 못해 추천을 제공할 수 없습니다."
PREPARED_KEYS = {
    "schemaVersion", "status", "request", "requestSha256", "catalogSha256",
    "catalogVersion", "policyVersion", "evaluatedAt", "candidateCount",
}


def _json_object(value) -> dict:
    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("INVALID_VALIDATION_INPUT")
            result[key] = item
        return result

    def invalid_constant(_value):
        raise ValueError("INVALID_VALIDATION_INPUT")

    result = json.loads(value, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError("INVALID_VALIDATION_INPUT")
    return result


def _message_json(message: Message) -> dict:
    value = message.text
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ValueError("INVALID_VALIDATION_INPUT")
    return _json_object(value)


def _prepared(value: dict) -> dict:
    if (
        not isinstance(value, dict) or set(value) != PREPARED_KEYS
        or value.get("schemaVersion") != "1.0" or value.get("status") != "prepared"
        or not isinstance(value.get("request"), dict)
        or not isinstance(value["request"].get("requestId"), str)
        or not re.fullmatch(r"gw-[a-f0-9]{32}", value["request"]["requestId"])
        or any(not isinstance(value.get(key), str) or not re.fullmatch(r"[a-f0-9]{64}", value[key])
               for key in ("requestSha256", "catalogSha256"))
        or any(not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= 200
               for key in ("catalogVersion", "policyVersion", "evaluatedAt"))
        or type(value.get("candidateCount")) is not int or value["candidateCount"] < 0
    ):
        raise ValueError("INVALID_PREPARED_REQUEST")
    return value


async def _post_validation(path: str, body: dict, token) -> dict:
    if path not in {"/gateway/prepare-request", "/gateway/validate-response", "/gateway/tools/call"}:
        raise ValueError("INVALID_VALIDATION_ROUTE")
    secret = token.get_secret_value() if hasattr(token, "get_secret_value") else token
    if not isinstance(secret, str) or not re.fullmatch(r"[\x21-\x7e]{32,256}", secret):
        raise ValueError("VALIDATION_AUTH_UNAVAILABLE")
    payload = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("VALIDATION_INPUT_TOO_LARGE")
    # A fixed private service address, no redirects, and no inherited proxy prevent request
    # content from selecting another destination. Credentials never enter messages.
    async with asyncio.timeout(10.0), httpx.AsyncClient(timeout=10.0, follow_redirects=False, trust_env=False) as client:
        async with client.stream("POST", VALIDATION_BASE_URL + path,
                                 headers={"Authorization": "Bearer " + secret,
                                          "Content-Type": "application/json"}, content=payload) as response:
            if response.status_code != 200:
                raise ValueError("VALIDATION_SERVICE_REJECTED")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise ValueError("VALIDATION_RESPONSE_TOO_LARGE")
    return _json_object(content)


def _output(value: dict, original: Message) -> Message:
    # Construct a new message: never forward model content_blocks, tool traces,
    # properties, or arbitrary text alongside the checked response.
    return Message(text=json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
                   sender="Machine", sender_name="AI", session_id=original.session_id)


class JejuPrepareRequest(Component):
    display_name = "Jeju Prepare Request"
    description = "Validate a structured request before the Agent can execute."
    icon = "ShieldCheck"
    name = "JejuPrepareRequest"
    inputs = [
        MessageInput(name="original_request", display_name="Structured Request", required=True),
        SecretStrInput(name="service_token", display_name="MCP Token", required=True, load_from_db=True),
    ]
    outputs = [Output(display_name="Prepared Request", name="prepared_request", method="prepare_request")]

    async def prepare_request(self) -> Message:
        try:
            request = _message_json(self.original_request)
            prepared = _prepared(await _post_validation("/gateway/prepare-request", request, self.service_token))
            if prepared["request"]["requestId"] != request.get("requestId"):
                raise ValueError("PREPARED_SCOPE_MISMATCH")
            return _output(prepared, self.original_request)
        except Exception:
            # Raising stops dependent Agent vertices. No exception/body echo.
            raise ValueError("REQUEST_PREPARATION_FAILED") from None


class JejuFinalValidation(Component):
    display_name = "Jeju Final Validation"
    description = "Publish only a server-accepted response, or a fixed blocked message."
    icon = "ShieldCheck"
    name = "JejuFinalValidation"
    inputs = [
        MessageInput(name="agent_response", display_name="Untrusted Agent Response", required=True),
        MessageInput(name="prepared_request", display_name="Prepared Request", required=True),
        SecretStrInput(name="service_token", display_name="MCP Token", required=True, load_from_db=True),
    ]
    outputs = [Output(display_name="Validated Message", name="message", method="validate_response")]

    async def validate_response(self) -> Message:
        try:
            prepared = _prepared(_message_json(self.prepared_request))
            response = _message_json(self.agent_response)
            result = await _post_validation("/gateway/validate-response", {
                "request": prepared["request"], "response": response,
                "expectedCatalogVersion": prepared["catalogVersion"],
            }, self.service_token)
            accepted_keys = PREPARED_KEYS - {"request"} | {"reasons", "checks", "response"}
            if (set(result) != accepted_keys or result.get("status") != "accepted"
                or result.get("schemaVersion") != "1.0" or result.get("reasons") != []
                or not isinstance(result.get("checks"), list) or not isinstance(result.get("response"), dict)
                or any(result.get(key) != prepared[key] for key in
                       ("requestSha256", "catalogSha256", "catalogVersion", "policyVersion", "candidateCount"))):
                return _output({"status": "blocked", "code": "RECOMMENDATION_BLOCKED", "message": BLOCKED_MESSAGE},
                               self.prepared_request)
            return _output(result["response"], self.prepared_request)
        except Exception:
            return _output({"status": "blocked", "code": "VALIDATION_FAILED", "message": BLOCKED_MESSAGE},
                           self.prepared_request)


class GatewayArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


GatewayId = Annotated[str, Field(min_length=1, max_length=160)]


class SearchArguments(GatewayArguments):
    query: Annotated[str, Field(max_length=100)] | None = None
    category: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    theme: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    zone: Annotated[str, Field(min_length=1, max_length=40)] | None = None
    excludedIds: Annotated[list[GatewayId], Field(max_length=200)] | None = None
    maxResults: Annotated[int, Field(ge=1, le=50)] = 10

    @field_validator("excludedIds")
    @classmethod
    def unique_exclusions(cls, value):
        if value is not None and len(value) != len(set(value)):
            raise ValueError("DUPLICATE_IDS")
        return value


class GetArguments(GatewayArguments):
    ids: Annotated[list[GatewayId], Field(min_length=1, max_length=20)]

    @field_validator("ids")
    @classmethod
    def unique_ids(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("DUPLICATE_IDS")
        return value


class OpenAtArguments(GatewayArguments):
    date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    time: Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class RequirementArguments(GatewayArguments):
    wheelchairRequired: bool | None = None
    indoorRequired: bool | None = None
    maxVisitCostKRW: Annotated[int, Field(ge=0, le=1_000_000_000)] | None = None
    openAt: OpenAtArguments | None = None


class CheckArguments(GetArguments):
    # MCP reconstructs the active requirements from the registered original request.
    # Optional compatibility input is validated there and cannot loosen that scope.
    requirements: RequirementArguments | None = None


def _scoped_tool(name, schema, description, request_id, token) -> StructuredTool:
    # Langflow compiles custom code with postponed annotations in an isolated
    # namespace. Resolve our fixed Pydantic types before LangChain inspects them;
    # relying on the synthetic class module loses Annotated/GatewayId at runtime.
    namespace = {"Annotated": Annotated, "Field": Field, "GatewayId": GatewayId,
                 "OpenAtArguments": OpenAtArguments, "RequirementArguments": RequirementArguments}
    for model in (OpenAtArguments, RequirementArguments, SearchArguments, GetArguments, CheckArguments):
        model.model_rebuild(_types_namespace=namespace)

    async def call(**arguments):
        try:
            checked = schema.model_validate(arguments).model_dump(exclude_none=True)
            result = await _post_validation("/gateway/tools/call", {
                "requestId": request_id, "name": name, "arguments": checked,
            }, token)
            return json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except Exception:
            raise ToolException("GATEWAY_TOOL_FAILED") from None

    return StructuredTool.from_function(
        coroutine=call, name=name, description=description, args_schema=schema,
        handle_validation_error="GATEWAY_TOOL_ARGUMENTS_INVALID",
        handle_tool_error="GATEWAY_TOOL_FAILED",
    )


class JejuScopedTools(Component):
    display_name = "Jeju Scoped Tools"
    description = "Three read-only tools bound to the server-registered original Gateway request."
    icon = "ShieldCheck"
    name = "JejuScopedTools"
    inputs = [
        MessageInput(name="prepared_request", display_name="Prepared Request", required=True),
        SecretStrInput(name="service_token", display_name="MCP Token", required=True, load_from_db=True),
    ]
    outputs = [Output(display_name="Scoped Tools", name="tools", method="build_tools")]

    def build_tools(self) -> list[Tool]:
        try:
            prepared = _prepared(_message_json(self.prepared_request))
            request_id = prepared["request"]["requestId"]
            # Capture both values once. No tool argument can replace the scope or token.
            token = self.service_token
            return [
                _scoped_tool("places.search", SearchArguments, "Find catalog places inside the original request filters. Query and extra filters may only narrow that scope; never invent IDs.", request_id, token),
                _scoped_tool("places.get", GetArguments, "Read 1 to 20 exact IDs inside the original request candidate scope. Missing or excluded IDs are rejected.", request_id, token),
                _scoped_tool("constraints.check", CheckArguments, "Check 1 to 20 IDs against all original hard requirements. The server reconstructs requirements; unknown is not pass or fail.", request_id, token),
            ]
        except Exception:
            raise ValueError("GATEWAY_TOOLS_UNAVAILABLE") from None
