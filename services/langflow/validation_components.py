"""Deterministic loopback boundaries embedded into the separate catalog-v2 flow.

These components never call a language model. The server owns catalog, candidate,
and evidence rules; neither component accepts a URL from the request or the model.
"""
from __future__ import annotations

import json
import re

import httpx
from lfx.custom.custom_component.component import Component
from lfx.io import MessageInput, Output, SecretStrInput
from lfx.schema.message import Message


VALIDATION_BASE_URL = "http://127.0.0.1:8789"
MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
BLOCKED_MESSAGE = "요청 조건과 장소 정보를 확인하지 못해 추천을 제공할 수 없습니다."
PREPARED_KEYS = {
    "schemaVersion", "status", "request", "requestSha256", "catalogSha256",
    "catalogVersion", "policyVersion", "evaluatedAt", "candidateCount",
}


def _message_json(message: Message) -> dict:
    value = message.text
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ValueError("INVALID_VALIDATION_INPUT")
    # Reject duplicate keys and NaN/Infinity rather than letting parsers disagree.
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


def _prepared(value: dict) -> dict:
    if (
        not isinstance(value, dict) or set(value) != PREPARED_KEYS
        or value.get("schemaVersion") != "1.0" or value.get("status") != "prepared"
        or not isinstance(value.get("request"), dict)
        or any(not isinstance(value.get(key), str) or not re.fullmatch(r"[a-f0-9]{64}", value[key])
               for key in ("requestSha256", "catalogSha256"))
        or any(not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= 200
               for key in ("catalogVersion", "policyVersion", "evaluatedAt"))
        or type(value.get("candidateCount")) is not int or value["candidateCount"] < 0
    ):
        raise ValueError("INVALID_PREPARED_REQUEST")
    return value


async def _post_validation(path: str, body: dict, token) -> dict:
    if path not in {"/prepare-request", "/validate-response"}:
        raise ValueError("INVALID_VALIDATION_ROUTE")
    secret = token.get_secret_value() if hasattr(token, "get_secret_value") else token
    if not isinstance(secret, str) or not re.fullmatch(r"[\x21-\x7e]{32,256}", secret):
        raise ValueError("VALIDATION_AUTH_UNAVAILABLE")
    payload = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("VALIDATION_INPUT_TOO_LARGE")
    # A fixed loopback address, no redirects, and no inherited proxy prevent request
    # content from selecting another destination. Credentials never enter messages.
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False, trust_env=False) as client:
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
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("INVALID_VALIDATION_RESPONSE")
    return result


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
            prepared = _prepared(await _post_validation("/prepare-request", request, self.service_token))
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
            result = await _post_validation("/validate-response", {
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
