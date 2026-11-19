"""Offline boundary tests with tiny lfx/httpx stubs; no third-party dependencies."""
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]


class Message:
    def __init__(self, text="", session_id="test-session", **kwargs):
        self.text, self.session_id = text, session_id
        for key, value in kwargs.items():
            setattr(self, key, value)


class Component:
    def __init__(self, **kwargs):
        vars(self).update(kwargs)


def load_components():
    stubs = {name: types.ModuleType(name) for name in [
        "httpx", "lfx", "lfx.custom", "lfx.custom.custom_component", "lfx.custom.custom_component.component",
        "lfx.io", "lfx.schema", "lfx.schema.message",
    ]}
    stubs["lfx.custom.custom_component.component"].Component = Component
    stubs["lfx.schema.message"].Message = Message
    for name in ("MessageInput", "Output", "SecretStrInput"):
        setattr(stubs["lfx.io"], name, lambda **kwargs: kwargs)
    spec = importlib.util.spec_from_file_location("isolated_validation_components", ROOT / "validation_components.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


component = load_components()
REQUEST = {"schemaVersion": "1.0", "requestId": "mock-request-001", "query": "제주 여행 추천",
           "timezone": "Asia/Seoul", "filters": {}, "excludedIds": [], "requirements": []}
RESPONSE = {"schemaVersion": "1.0", "requestId": REQUEST["requestId"], "action": "recommend", "placeIds": ["fixture-1"],
            "requirementClaims": [], "confirmationNotice": False, "noticeTopics": []}
PREPARED = {"schemaVersion": "1.0", "status": "prepared", "request": REQUEST, "requestSha256": "a" * 64,
            "catalogSha256": "b" * 64, "catalogVersion": "catalog-v2-fixture", "policyVersion": "policy-v1",
            "evaluatedAt": "2026-11-19T00:00:00.000Z", "candidateCount": 1}
ACCEPTED = {**{key: value for key, value in PREPARED.items() if key != "request"}, "status": "accepted",
            "reasons": [], "checks": [], "response": RESPONSE}


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = json.dumps(body).encode()
        self.status_code = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def aiter_bytes(self):
        yield self.body


class HttpBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def client(self, body, status=200):
        calls = []
        options = []

        class Client:
            def __init__(self, **kwargs):
                options.append(kwargs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            def stream(self, method, url, **kwargs):
                calls.append((method, url, kwargs))
                return FakeResponse(body, status)

        component.httpx.AsyncClient = Client
        return calls, options

    def final(self, model=RESPONSE, prepared=PREPARED):
        return component.JejuFinalValidation(agent_response=Message(json.dumps(model), content_blocks=["unsafe-trace"]),
                                            prepared_request=Message(json.dumps(prepared)), service_token="x" * 32)

    async def test_prepare_calls_fixed_loopback_and_preserves_trusted_request(self):
        calls, options = self.client(PREPARED)
        node = component.JejuPrepareRequest(original_request=Message(json.dumps(REQUEST)), service_token="x" * 32)
        result = await node.prepare_request()
        self.assertEqual(json.loads(result.text), PREPARED)
        self.assertEqual(calls[0][:2], ("POST", "http://127.0.0.1:8789/prepare-request"))
        self.assertEqual(json.loads(calls[0][2]["content"]), REQUEST)
        self.assertEqual(options, [{"timeout": 10.0, "follow_redirects": False, "trust_env": False}])

    async def test_invalid_prepare_stops_before_network_without_echo(self):
        calls, _ = self.client(PREPARED)
        node = component.JejuPrepareRequest(original_request=Message('{"x":1,"x":"PRIVATE_TEXT"}'), service_token="x" * 32)
        with self.assertRaisesRegex(ValueError, "^REQUEST_PREPARATION_FAILED$"):
            await node.prepare_request()
        self.assertEqual(calls, [])

    async def test_prepare_server_rejection_is_sanitized(self):
        self.client({"error": "PRIVATE_TEXT"}, 400)
        node = component.JejuPrepareRequest(original_request=Message(json.dumps(REQUEST)), service_token="x" * 32)
        with self.assertRaisesRegex(ValueError, "^REQUEST_PREPARATION_FAILED$"):
            await node.prepare_request()

    async def test_accepted_only_response_published_and_context_not_from_agent(self):
        calls, _ = self.client(ACCEPTED)
        result = await self.final().validate_response()
        self.assertEqual(json.loads(result.text), RESPONSE)
        self.assertFalse(hasattr(result, "content_blocks"))
        self.assertEqual(json.loads(calls[0][2]["content"]), {
            "request": REQUEST, "response": RESPONSE, "expectedCatalogVersion": PREPARED["catalogVersion"],
        })

    async def test_blocked_never_echoes_model_or_server_reasons(self):
        self.client({**ACCEPTED, "status": "blocked", "reasons": [{"code": "PRIVATE_TEXT"}]})
        result = await self.final({**RESPONSE, "placeIds": ["UNSAFE_MODEL_ID"]}).validate_response()
        output = json.loads(result.text)
        self.assertEqual(output["code"], "RECOMMENDATION_BLOCKED")
        self.assertEqual(output["message"], component.BLOCKED_MESSAGE)
        self.assertNotIn("PRIVATE_TEXT", result.text)
        self.assertNotIn("UNSAFE_MODEL_ID", result.text)

    async def test_catalog_or_request_binding_mismatch_blocks(self):
        for field, value in [("catalogSha256", "c" * 64), ("requestSha256", "d" * 64), ("candidateCount", 9),
                             ("policyVersion", "other-policy")]:
            with self.subTest(field=field):
                self.client({**ACCEPTED, field: value})
                output = json.loads((await self.final().validate_response()).text)
                self.assertEqual(output["status"], "blocked")

    async def test_non_json_or_fenced_model_response_fails_closed_before_network(self):
        calls, _ = self.client(ACCEPTED)
        node = self.final()
        node.agent_response = Message("```json\nPRIVATE_TEXT\n```")
        result = await node.validate_response()
        self.assertEqual(json.loads(result.text)["code"], "VALIDATION_FAILED")
        self.assertNotIn("PRIVATE_TEXT", result.text)
        self.assertEqual(calls, [])

    async def test_redirect_or_http_failure_does_not_forward_body(self):
        for status in (302, 401, 500):
            with self.subTest(status=status):
                self.client({"error": "PRIVATE_TEXT"}, status)
                result = await self.final().validate_response()
                self.assertEqual(json.loads(result.text)["code"], "VALIDATION_FAILED")
                self.assertNotIn("PRIVATE_TEXT", result.text)

    async def test_oversized_server_response_is_blocked(self):
        self.client({"data": "x" * (component.MAX_RESPONSE_BYTES + 1)})
        result = await self.final().validate_response()
        self.assertEqual(json.loads(result.text)["code"], "VALIDATION_FAILED")


class FlowContractTests(unittest.TestCase):
    def test_graph_requires_prepare_and_validation_and_embeds_current_helpers(self):
        flow = json.loads((ROOT / "jeju-tour-agent-vertex-v2.flow.json").read_text())
        nodes = {node["id"]: node for node in flow["data"]["nodes"]}
        edges = {(edge["source"], edge["target"], edge["data"]["targetHandle"]["fieldName"])
                 for edge in flow["data"]["edges"]}
        self.assertEqual(edges, {
            ("ChatInput-jejuV2", "JejuPrepareRequest-v2", "original_request"),
            ("JejuPrepareRequest-v2", "Agent-jejuV2", "input_value"),
            ("JejuPrepareRequest-v2", "JejuFinalValidation-v2", "prepared_request"),
            ("MCPTools-jejuV2", "Agent-jejuV2", "tools"),
            ("Agent-jejuV2", "JejuFinalValidation-v2", "agent_response"),
            ("JejuFinalValidation-v2", "ChatOutput-jejuV2", "input_value"),
        })
        agent = nodes["Agent-jejuV2"]["data"]["node"]["template"]
        self.assertEqual(agent["max_iterations"]["value"], 4)
        self.assertFalse(agent["add_calculator_tool"]["value"])
        self.assertFalse(agent["add_current_date_tool"]["value"])
        self.assertEqual(agent["n_messages"]["value"], 0)
        self.assertIn("result = await _guarded_agent_run(self, _run_once)", agent["code"]["value"])
        message_method = agent["code"]["value"].split("    async def message_response(self)", 1)[1].split("    async def json_response(self)", 1)[0]
        self.assertNotIn("_run_agent_with_model_remediation", message_method)
        self.assertTrue(agent["code"]["value"].endswith((ROOT / "guarded_vertex_agent.py").read_text()))
        for node_id in ("JejuPrepareRequest-v2", "JejuFinalValidation-v2"):
            token = nodes[node_id]["data"]["node"]["template"]["service_token"]
            self.assertEqual(token["value"], "JEJU_MCP_TOKEN")
            self.assertTrue(token["load_from_db"])
        self.assertEqual(nodes["MCPTools-jejuV2"]["data"]["node"]["template"]["mcp_server"]["value"]["name"], "jeju-mcp-v2")


if __name__ == "__main__":
    unittest.main()
