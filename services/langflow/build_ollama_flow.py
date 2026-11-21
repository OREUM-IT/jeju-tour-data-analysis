#!/usr/bin/env python3
"""Build the local Ollama evaluation variant of the reviewed v2 Langflow flow.

Standard library only. The input is the committed, pinned Vertex v2 flow. Only the
Agent's model selection, its credential reference and its ``_get_llm`` method change,
plus the guard deadline for the CPU profile. Prompts, the four-model-call budget, the
other five components and every edge stay byte-identical. Building contacts no
Langflow server, Ollama daemon or model, and the output is a local evaluation flow,
not a production one.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent
V2_FLOW = ROOT / "jeju-tour-agent-vertex-v2.flow.json"
OUTPUT = ROOT / "jeju-tour-agent-ollama-v2.flow.json"
# Same pin as check_validated_vertex_flow in scripts/check_repository.py.
V2_CANONICAL_SHA256 = "020e2b0b05f9fa291ba18b91b9c10e78c8247b4c52deed217287c2cab989dc5e"
# ast.get_source_segment of the reviewed Vertex _get_llm inside that flow.
V2_GET_LLM_SHA256 = "cc39cb9f3cb2d4020f9e0cf3516f43bd9af4fdb7664008a893c94acf2da26118"
AGENT_ID = "Agent-jejuV2"
DEFAULT_MODEL = "qwen3:4b"
DEFAULT_PROFILE = "cpu-evaluation"
# Guard deadline for the whole Agent run and the Ollama HTTP client timeout (seconds).
PROFILES = {"baseline-limits": (90.0, 60), "cpu-evaluation": (900.0, 600)}
MODEL_TAG = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*:[a-z0-9]+(?:[._-][a-z0-9]+)*")
GUARD_LINE = "GUARDED_AGENT_MAX_SECONDS = 90.0"
VALIDATION_LINE = 'VALIDATION_BASE_URL = "http://127.0.0.1:8789"'
NODE_TYPES = {"ChatInput", "ChatOutput", "Agent", "MCPTools", "JejuPrepareRequest", "JejuFinalValidation"}
EDGES = {
    ("ChatInput", "JejuPrepareRequest", "original_request"),
    ("JejuPrepareRequest", "Agent", "input_value"),
    ("JejuPrepareRequest", "JejuFinalValidation", "prepared_request"),
    ("MCPTools", "Agent", "tools"), ("Agent", "JejuFinalValidation", "agent_response"),
    ("JejuFinalValidation", "ChatOutput", "input_value"),
}
GET_LLM_TEMPLATE = '''def _get_llm(self):
        """Override parent to include max_tokens and the reviewed local Ollama client.

        Local evaluation only: accept just the reviewed Ollama model tag and call the
        loopback daemon at http://127.0.0.1:11434; no key, cloud project or account is
        read. As in the original method, streaming is mandatory for AgentComponent (the
        Playground and /events surface need ``on_chat_model_stream`` chunks). Thinking is
        off and sampling keeps the model defaults; context, output length, residency and
        the HTTP timeout are fixed by the evaluation profile this flow was built with.
        """
        selected = self.model
        if (not isinstance(selected, list) or len(selected) != 1
            or not isinstance(selected[0], dict)
            or selected[0].get("provider") != "Ollama"
            or selected[0].get("name") != __REVIEWED_TAG__):
            raise ValueError("OLLAMA_MODEL_SELECTION_INVALID")
        return get_llm(
            model=self.model,
            user_id=self.user_id,
            api_key=None,
            stream=True,
            max_tokens=self._get_max_tokens_value(),
            watsonx_url=getattr(self, "base_url_ibm_watsonx", None),
            watsonx_project_id=getattr(self, "project_id", None),
            overrides={**(getattr(self, "_model_overrides", None) or {}),
                "base_url": "http://127.0.0.1:11434", "reasoning": False, "num_ctx": 16384,
                "num_predict": 2048, "keep_alive": "30m", "client_kwargs": {"timeout": __TIMEOUT_SECONDS__}},
        )'''


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def canonical_sha256(flow: dict) -> str:
    text = json.dumps(flow, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def serialize(flow: dict) -> bytes:
    """Same layout as the committed flows: UTF-8, two-space indent, one LF at the end."""
    return (json.dumps(flow, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def get_llm_method(code: str) -> ast.FunctionDef:
    """Return the only _get_llm method, which must be defined directly on AgentComponent."""
    tree = ast.parse(code)
    methods = [node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_get_llm"]
    owners = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AgentComponent"]
    require(len(methods) == 1 and isinstance(methods[0], ast.FunctionDef) and not methods[0].decorator_list
            and len(owners) == 1 and methods[0] in owners[0].body, "AGENT_GET_LLM_MISMATCH")
    return methods[0]


def reviewed_get_llm(model_tag: str, profile: str) -> str:
    """The replacement method source, starting at ``def`` like ast.get_source_segment."""
    require(isinstance(model_tag, str) and len(model_tag) <= 128 and MODEL_TAG.fullmatch(model_tag) is not None,
            "OLLAMA_MODEL_TAG_INVALID")
    require(isinstance(profile, str) and profile in PROFILES, "OLLAMA_PROFILE_INVALID")
    require(GET_LLM_TEMPLATE.count("__REVIEWED_TAG__") == GET_LLM_TEMPLATE.count("__TIMEOUT_SECONDS__") == 1,
            "OLLAMA_METHOD_TEMPLATE_INVALID")
    return (GET_LLM_TEMPLATE.replace("__REVIEWED_TAG__", json.dumps(model_tag))
            .replace("__TIMEOUT_SECONDS__", str(PROFILES[profile][1])))


def _check_unchanged_graph(flow: dict) -> dict:
    """Reviewed v2 shape: six components, six edges, local validators and one v2 MCP."""
    nodes = flow["data"]["nodes"]
    by_type = {node["data"]["type"]: node for node in nodes}
    require(len(nodes) == len(by_type) == 6 and set(by_type) == NODE_TYPES
            and by_type["Agent"]["id"] == AGENT_ID, "FLOW_COMPONENTS_MISMATCH")
    kind_by_id = {node["id"]: node["data"]["type"] for node in nodes}
    edges = flow["data"]["edges"]
    connections = {(kind_by_id[edge["source"]], kind_by_id[edge["target"]], edge["data"]["targetHandle"]["fieldName"])
                   for edge in edges}
    require(len(kind_by_id) == 6 and len(edges) == len(connections) == 6 and connections == EDGES,
            "FLOW_VALIDATION_BYPASS_REJECTED")
    for kind in ("JejuPrepareRequest", "JejuFinalValidation"):
        fields = by_type[kind]["data"]["node"]["template"]
        assignments = [node for node in ast.parse(fields["code"]["value"]).body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "VALIDATION_BASE_URL" for target in node.targets)]
        require(len(assignments) == 1 and fields["code"]["value"].count(VALIDATION_LINE) == 1
                and fields["service_token"].get("value") == "JEJU_MCP_TOKEN"
                and fields["service_token"].get("load_from_db") is True, "VALIDATION_DESTINATION_MISMATCH")
    mcp = by_type["MCPTools"]["data"]["node"]["template"]
    require(mcp["mcp_server"]["value"] == {"name": "jeju-mcp-v2", "config": {}} and mcp["headers"]["value"] == {}
            and mcp["tool_execution_timeout"]["value"] == 10, "MCP_SERVER_MISMATCH")
    agent = by_type["Agent"]["data"]["node"]["template"]
    require(agent["max_iterations"]["value"] == 4 and agent["n_messages"]["value"] == 0
            and all(agent[name]["value"] is False for name in
                    ("add_calculator_tool", "add_current_date_tool", "handle_parsing_errors")),
            "AGENT_LIMITS_MISMATCH")
    return by_type


def build_ollama_flow(v2_flow: dict, *, model_tag: str = DEFAULT_MODEL, profile: str = DEFAULT_PROFILE) -> dict:
    """Return a new flow; the reviewed v2 input is never modified."""
    method = reviewed_get_llm(model_tag, profile)
    require(isinstance(v2_flow, dict) and canonical_sha256(v2_flow) == V2_CANONICAL_SHA256, "V2_FLOW_PIN_MISMATCH")
    flow = copy.deepcopy(v2_flow)
    agent = _check_unchanged_graph(flow)["Agent"]["data"]["node"]["template"]
    code = agent["code"]["value"]
    original = ast.get_source_segment(code, get_llm_method(code))
    require(hashlib.sha256(original.encode()).hexdigest() == V2_GET_LLM_SHA256 and code.count(original) == 1,
            "AGENT_GET_LLM_MISMATCH")
    guard_seconds = PROFILES[profile][0]
    require(code.count(GUARD_LINE) == code.count("\n" + GUARD_LINE + "\n") == 1, "AGENT_GUARD_MISMATCH")
    code = code.replace(original, method)
    code = code.replace(GUARD_LINE, f"GUARDED_AGENT_MAX_SECONDS = {guard_seconds}")
    require(ast.get_source_segment(code, get_llm_method(code)) == method, "AGENT_GET_LLM_MISMATCH")

    entry = {"name": model_tag, "provider": "Ollama", "metadata": {"tool_calling": True, "model_type": "llm"},
             "icon": "Ollama"}
    agent["model"]["value"] = [copy.deepcopy(entry)]
    agent["model"]["options"] = [copy.deepcopy(entry)]
    agent["api_key"].update(value="", load_from_db=False)
    agent["code"]["value"] = code
    flow.update(name=f"제주 관광 Agent v2 — 로컬 Ollama 평가 ({model_tag}, {profile})",
                description=(f"로컬 평가 전용 흐름이며 운영용이 아니다. 검토된 v2 흐름의 Agent 모델 연결을 로컬 Ollama "
                             f"{model_tag}로 바꿨고 제한은 최대 모델 4회/{int(guard_seconds)}초다. 모델 품질과 도구 호출은 미검증이다."))

    # Apart from these fields, the serialized flow stays byte-identical to the reviewed v2.
    reference, changed = copy.deepcopy(v2_flow), copy.deepcopy(flow)
    for item in (reference, changed):
        del item["name"], item["description"]
        fields = next(node for node in item["data"]["nodes"] if node["id"] == AGENT_ID)["data"]["node"]["template"]
        del fields["model"]["value"], fields["model"]["options"], fields["code"]["value"]
        del fields["api_key"]["value"], fields["api_key"]["load_from_db"]
    require(serialize(reference) == serialize(changed), "OLLAMA_FLOW_SCOPE_MISMATCH")
    _check_unchanged_graph(flow)
    return flow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="exact local Ollama tag, e.g. qwen3:4b")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, choices=sorted(PROFILES))
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--output", type=Path, help="required unless building the committed qwen3:4b/cpu-evaluation artifact")
    target.add_argument("--check", type=Path, metavar="PATH", help="exit non-zero unless the rebuild equals PATH")
    args = parser.parse_args(argv)
    try:
        flow = build_ollama_flow(json.loads(V2_FLOW.read_text(encoding="utf-8")),
                                 model_tag=args.model, profile=args.profile)
        expected = serialize(flow)
        if args.check is not None:
            # A Windows checkout may convert line endings; JSON strings cannot hold a raw CR or LF.
            if args.check.read_bytes().replace(b"\r\n", b"\n") != expected:
                print("OLLAMA_FLOW_CHECK_MISMATCH", file=sys.stderr)
                return 1
            status = "matches_rebuild"
        else:
            output = args.output or OUTPUT
            # The committed artifact is pinned; other builds need their own explicit path.
            require((args.model, args.profile) == (DEFAULT_MODEL, DEFAULT_PROFILE)
                    or output.resolve() != OUTPUT.resolve(), "OLLAMA_FLOW_OUTPUT_RESERVED")
            output.write_bytes(expected)
            status = "built_without_model_inference"
    except (ValueError, KeyError, TypeError, SyntaxError) as error:
        message = str(error)
        print(message if re.fullmatch(r"[A-Z0-9_]+", message) else "OLLAMA_FLOW_BUILD_FAILED", file=sys.stderr)
        return 2
    except OSError:
        print("OLLAMA_FLOW_IO_FAILED", file=sys.stderr)
        return 2
    print(json.dumps({"status": status, "model": args.model, "profile": args.profile,
                      "canonicalSha256": canonical_sha256(flow)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
