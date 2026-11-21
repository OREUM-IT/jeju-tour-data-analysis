"""Offline checks for the local Ollama evaluation flow; no Langflow, Ollama daemon or model is contacted."""

import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("build_ollama_flow", ROOT / "build_ollama_flow.py")
builder = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(builder)

V2_PATH = ROOT / "jeju-tour-agent-vertex-v2.flow.json"
ARTIFACT = ROOT / "jeju-tour-agent-ollama-v2.flow.json"
V2 = json.loads(V2_PATH.read_text(encoding="utf-8"))
ENTRY = {"name": "qwen3:4b", "provider": "Ollama", "metadata": {"tool_calling": True, "model_type": "llm"}, "icon": "Ollama"}
REVIEWED_OVERRIDES = {"base_url": "http://127.0.0.1:11434", "reasoning": False, "num_ctx": 16384,
                      "num_predict": 2048, "keep_alive": "30m"}


def agent_fields(flow):
    return next(node for node in flow["data"]["nodes"] if node["id"] == "Agent-jejuV2")["data"]["node"]["template"]


def get_llm_node(code):
    methods = [node for node in ast.walk(ast.parse(code))
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_get_llm"]
    assert len(methods) == 1 and isinstance(methods[0], ast.FunctionDef)
    return methods[0]


def structure_without_get_llm(code):
    """AST dump with AgentComponent._get_llm removed and the guard constant blanked, plus that constant."""
    tree = ast.parse(code)
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AgentComponent")
    owner.body = [node for node in owner.body if not (isinstance(node, ast.FunctionDef) and node.name == "_get_llm")]
    guards = [node for node in tree.body if isinstance(node, ast.Assign)
              and [getattr(target, "id", None) for target in node.targets] == ["GUARDED_AGENT_MAX_SECONDS"]]
    assert len(guards) == 1
    seconds = guards[0].value.value
    guards[0].value = ast.Constant(value=None)
    return ast.dump(tree), seconds


def compiled_get_llm(code, fake_get_llm):
    namespace = {"get_llm": fake_get_llm}
    module = ast.fix_missing_locations(ast.Module(body=[get_llm_node(code)], type_ignores=[]))
    exec(compile(module, "<generated-ollama-get-llm>", "exec"), namespace)
    return namespace["_get_llm"]


class FakeAgent:
    def __init__(self, model, **attributes):
        self.model = model
        self.user_id = "test-user"
        self.base_url_ibm_watsonx = "https://us-south.ml.cloud.ibm.com"
        self.project_id = ""
        vars(self).update(attributes)

    def _get_max_tokens_value(self):
        return None


def run_cli(*argv):
    """Return the exit status and the fixed stderr code (empty on success)."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        status = builder.main(list(argv))
    if status == 0:
        assert json.loads(stdout.getvalue())["status"] in {"matches_rebuild", "built_without_model_inference"}
    return status, stderr.getvalue().strip()


class OllamaFlowArtifactTests(unittest.TestCase):
    def test_committed_artifact_is_the_serialized_default_rebuild(self):
        built = builder.build_ollama_flow(V2)
        self.assertEqual(builder.serialize(built), ARTIFACT.read_bytes().replace(b"\r\n", b"\n"))
        self.assertEqual(built, builder.build_ollama_flow(V2, model_tag="qwen3:4b", profile="cpu-evaluation"))
        # Same layout as the committed flows the existing builder writes.
        self.assertEqual(builder.serialize(V2), V2_PATH.read_bytes().replace(b"\r\n", b"\n"))

    def test_builder_pin_matches_repository_check_and_input_is_not_mutated(self):
        check = (ROOT.parents[1] / "scripts/check_repository.py").read_text(encoding="utf-8")
        vertex_check = check.split("def check_validated_vertex_flow", 1)[1].split("\ndef ", 1)[0]
        self.assertIn(f'"{builder.V2_CANONICAL_SHA256}"', vertex_check)
        self.assertEqual(builder.canonical_sha256(V2), builder.V2_CANONICAL_SHA256)
        before = json.dumps(V2, ensure_ascii=False)
        builder.build_ollama_flow(V2, profile="baseline-limits")
        self.assertEqual(json.dumps(V2, ensure_ascii=False), before)

    def test_only_agent_model_api_key_and_code_differ(self):
        built = builder.build_ollama_flow(V2)
        self.assertEqual(list(built), list(V2))
        self.assertEqual(built["name"], "제주 관광 Agent v2 — 로컬 Ollama 평가 (qwen3:4b, cpu-evaluation)")
        self.assertIn("로컬 평가 전용", built["description"])
        self.assertIn("운영용이 아니다", built["description"])
        self.assertIn("미검증", built["description"])
        for key in set(V2) - {"name", "description"}:
            if key != "data":
                self.assertEqual(built[key], V2[key])
        self.assertEqual(json.dumps(built["data"]["edges"]), json.dumps(V2["data"]["edges"]))
        self.assertEqual(built["data"]["viewport"], V2["data"]["viewport"])
        self.assertEqual([node["id"] for node in built["data"]["nodes"]], [node["id"] for node in V2["data"]["nodes"]])
        for old, new in zip(V2["data"]["nodes"], built["data"]["nodes"]):
            if old["id"] != "Agent-jejuV2":
                self.assertEqual(json.dumps(new, ensure_ascii=False), json.dumps(old, ensure_ascii=False))

        old, new = agent_fields(V2), agent_fields(built)
        self.assertEqual(list(new), list(old))
        self.assertEqual({name for name in old if old[name] != new[name]}, {"model", "api_key", "code"})
        for name, keys in (("model", {"value", "options"}), ("api_key", {"value", "load_from_db"}), ("code", {"value"})):
            self.assertEqual(list(new[name]), list(old[name]))
            self.assertEqual({key for key in old[name] if old[name][key] != new[name][key]}, keys)
        self.assertEqual(json.dumps(new["model"]["value"]), json.dumps([ENTRY]))
        self.assertEqual(json.dumps(new["model"]["options"]), json.dumps([ENTRY]))
        self.assertEqual((new["api_key"]["value"], new["api_key"]["load_from_db"]), ("", False))
        self.assertEqual(new["max_iterations"]["value"], 4)
        self.assertEqual(new["system_prompt"]["value"], old["system_prompt"]["value"])
        self.assertNotIn("JEJU_VERTEX_SERVICE_ACCOUNT_JSON", ARTIFACT.read_text(encoding="utf-8"))
        old_agent = copy.deepcopy(next(node for node in V2["data"]["nodes"] if node["id"] == "Agent-jejuV2"))
        new_agent = copy.deepcopy(next(node for node in built["data"]["nodes"] if node["id"] == "Agent-jejuV2"))
        for agent in (old_agent, new_agent):
            for name in ("model", "api_key", "code"):
                del agent["data"]["node"]["template"][name]
        self.assertEqual(json.dumps(new_agent, ensure_ascii=False), json.dumps(old_agent, ensure_ascii=False))

    def test_code_differs_only_in_get_llm_and_profile_deadline(self):
        original = agent_fields(V2)["code"]["value"]
        old_method = ast.get_source_segment(original, get_llm_node(original))
        self.assertEqual(hashlib.sha256(old_method.encode()).hexdigest(), builder.V2_GET_LLM_SHA256)
        old_structure, old_seconds = structure_without_get_llm(original)
        self.assertEqual(old_seconds, 90.0)
        for profile, seconds in (("cpu-evaluation", 900.0), ("baseline-limits", 90.0)):
            with self.subTest(profile=profile):
                code = agent_fields(builder.build_ollama_flow(V2, profile=profile))["code"]["value"]
                new_structure, new_seconds = structure_without_get_llm(code)
                self.assertEqual(new_structure, old_structure)
                self.assertEqual(new_seconds, seconds)
                new_method = ast.get_source_segment(code, get_llm_node(code))
                restored = code.replace(new_method, old_method).replace(
                    f"\nGUARDED_AGENT_MAX_SECONDS = {seconds}\n", "\nGUARDED_AGENT_MAX_SECONDS = 90.0\n")
                self.assertEqual(restored, original)
                lowered = new_method.lower()
                for word in ("vertex", "google", "credential", "service_account", "oauth", "temperature", "json"):
                    self.assertNotIn(word, lowered)
                tree = get_llm_node(code)
                self.assertFalse([node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))])
                self.assertNotIn("api_key", {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)})
                self.assertTrue(ast.get_docstring(tree).startswith("Override parent to include max_tokens"))
                self.assertIn("Ollama", ast.get_docstring(tree))

    def test_generated_method_passes_reviewed_local_client_settings(self):
        for profile, timeout in (("cpu-evaluation", 600), ("baseline-limits", 60)):
            with self.subTest(profile=profile):
                fields = agent_fields(builder.build_ollama_flow(V2, profile=profile))
                calls, client = [], object()

                def fake_get_llm(**kwargs):
                    calls.append(kwargs)
                    return client

                method = compiled_get_llm(fields["code"]["value"], fake_get_llm)
                self.assertIs(method(FakeAgent(copy.deepcopy(fields["model"]["value"]))), client)
                # Remediation overrides cannot replace the reviewed client settings.
                stale = {"base_url": "http://192.0.2.1:1", "reasoning": True, "num_ctx": 1, "keep_alive": 0,
                         "client_kwargs": {"timeout": 1}, "max_retries": 3}
                self.assertIs(method(FakeAgent(copy.deepcopy(fields["model"]["value"]), _model_overrides=stale)), client)
                self.assertEqual(len(calls), 2)
                for kwargs in calls:
                    self.assertEqual(set(kwargs), {"model", "user_id", "api_key", "stream", "max_tokens",
                                                   "watsonx_url", "watsonx_project_id", "overrides"})
                    self.assertEqual(kwargs["model"], [ENTRY])
                    self.assertEqual(kwargs["user_id"], "test-user")
                    self.assertIsNone(kwargs["api_key"])
                    self.assertIs(kwargs["stream"], True)
                    self.assertIsNone(kwargs["max_tokens"])
                    overrides = kwargs["overrides"]
                    self.assertEqual({key: overrides[key] for key in REVIEWED_OVERRIDES}, REVIEWED_OVERRIDES)
                    self.assertIs(overrides["reasoning"], False)
                    self.assertEqual(overrides["client_kwargs"], {"timeout": timeout})
                    self.assertNotIn("temperature", overrides)
                self.assertEqual(set(calls[0]["overrides"]), set(REVIEWED_OVERRIDES) | {"client_kwargs"})
                self.assertEqual(calls[1]["overrides"]["max_retries"], 3)

    def test_wrong_model_selection_fails_closed_before_client_creation(self):
        fields = agent_fields(builder.build_ollama_flow(V2))
        calls = []
        method = compiled_get_llm(fields["code"]["value"], lambda **kwargs: calls.append(kwargs))
        cases = [
            [dict(ENTRY, provider="Google Generative AI")], [dict(ENTRY, provider="ollama")],
            [dict(ENTRY, name="qwen3:8b")], [dict(ENTRY, name="QWEN3:4B")], [{"name": "qwen3:4b"}],
            [ENTRY, ENTRY], [], ENTRY, "qwen3:4b", None, [["qwen3:4b", "Ollama"]],
        ]
        for selection in cases:
            with self.subTest(selection=selection):
                with self.assertRaises(ValueError) as caught:
                    method(FakeAgent(copy.deepcopy(selection)))
                self.assertEqual(str(caught.exception), "OLLAMA_MODEL_SELECTION_INVALID")
        self.assertEqual(calls, [])
        other = agent_fields(builder.build_ollama_flow(V2, model_tag="llama3.1:8b", profile="baseline-limits"))
        method = compiled_get_llm(other["code"]["value"], lambda **kwargs: calls.append(kwargs) or "client")
        with self.assertRaisesRegex(ValueError, "^OLLAMA_MODEL_SELECTION_INVALID$"):
            method(FakeAgent([ENTRY]))
        self.assertEqual(method(FakeAgent(other["model"]["value"])), "client")
        self.assertEqual(calls[0]["model"][0]["name"], "llama3.1:8b")

    def test_v2_pin_mismatch_is_rejected(self):
        description = copy.deepcopy(V2)
        description["description"] += " "
        code = copy.deepcopy(V2)
        agent_fields(code)["code"]["value"] = agent_fields(code)["code"]["value"].replace(
            "GUARDED_AGENT_MAX_SECONDS = 90.0", "GUARDED_AGENT_MAX_SECONDS = 9000.0")
        for flow in (description, code, builder.build_ollama_flow(V2), [], None):
            with self.assertRaisesRegex(ValueError, "^V2_FLOW_PIN_MISMATCH$"):
                builder.build_ollama_flow(flow)

    def test_bad_model_tag_and_profile_are_rejected(self):
        for tag in ("Qwen3:4b", "qwen3:4B", "qwen3", "qwen3:", ":4b", " qwen3:4b", "qwen3:4b ", "qwen3:4b\n",
                    "library/qwen3:4b", "qwen3:4b:x", "qwen3::4b", "qwen3:-4b", "qwen3:4b-", "qwen3..x:4b",
                    "qwen3:4b;id", 'qwen3:4b"', "", None, 4, b"qwen3:4b", "a" * 120 + ":" + "b" * 20):
            with self.subTest(tag=tag):
                with self.assertRaisesRegex(ValueError, "^OLLAMA_MODEL_TAG_INVALID$"):
                    builder.build_ollama_flow(V2, model_tag=tag)
        for profile in ("", "gpu", "CPU-evaluation", "cpu_evaluation", None, 1):
            with self.subTest(profile=profile):
                with self.assertRaisesRegex(ValueError, "^OLLAMA_PROFILE_INVALID$"):
                    builder.build_ollama_flow(V2, profile=profile)
        for tag in ("llama3.1:8b", "qwen2.5-coder:7b-instruct", "gpt-oss:20b", "qwen3:4b-instruct-2507-q4_k_m"):
            with self.subTest(tag=tag):
                fields = agent_fields(builder.build_ollama_flow(V2, model_tag=tag))
                self.assertEqual(fields["model"]["value"][0]["name"], tag)
                self.assertIn(f'!= "{tag}"', fields["code"]["value"])

    def test_baseline_limits_profile_keeps_original_deadline_and_short_timeout(self):
        baseline = builder.build_ollama_flow(V2, profile="baseline-limits")
        code = agent_fields(baseline)["code"]["value"]
        self.assertEqual(code.count("\nGUARDED_AGENT_MAX_SECONDS = 90.0\n"), 1)
        self.assertNotIn("GUARDED_AGENT_MAX_SECONDS = 900.0", code)
        self.assertEqual(code.count('"client_kwargs": {"timeout": 60}'), 1)
        self.assertEqual(baseline["name"], "제주 관광 Agent v2 — 로컬 Ollama 평가 (qwen3:4b, baseline-limits)")
        self.assertIn("90초", baseline["description"])
        cpu = agent_fields(builder.build_ollama_flow(V2))["code"]["value"]
        self.assertEqual(cpu.count("\nGUARDED_AGENT_MAX_SECONDS = 900.0\n"), 1)
        self.assertEqual(cpu.count('"client_kwargs": {"timeout": 600}'), 1)
        self.assertEqual(agent_fields(baseline)["max_iterations"]["value"], 4)

    def test_cli_check_and_reserved_artifact_path(self):
        committed = ARTIFACT.read_bytes()
        unix = committed.replace(b"\r\n", b"\n")
        self.assertEqual(run_cli("--check", str(ARTIFACT)), (0, ""))
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "flow.json"
            for variant in (unix, unix.replace(b"\n", b"\r\n")):  # LF and Windows CRLF checkouts
                path.write_bytes(variant)
                self.assertEqual(run_cli("--check", str(path)), (0, ""))
            path.write_bytes(unix + b"\n")
            self.assertEqual(run_cli("--check", str(path)), (1, "OLLAMA_FLOW_CHECK_MISMATCH"))
            self.assertEqual(run_cli("--profile", "baseline-limits", "--check", str(ARTIFACT)),
                             (1, "OLLAMA_FLOW_CHECK_MISMATCH"))
            output = pathlib.Path(directory) / "baseline.flow.json"
            self.assertEqual(run_cli("--profile", "baseline-limits", "--output", str(output)), (0, ""))
            self.assertEqual(run_cli("--profile", "baseline-limits", "--check", str(output)), (0, ""))
            self.assertEqual(run_cli("--model", "Qwen3:4b", "--output", str(output)), (2, "OLLAMA_MODEL_TAG_INVALID"))
            self.assertEqual(run_cli("--check", str(pathlib.Path(directory) / "missing.json")),
                             (2, "OLLAMA_FLOW_IO_FAILED"))
        # Only the reviewed model/profile may be written to the pinned artifact path.
        self.assertEqual(run_cli("--profile", "baseline-limits"), (2, "OLLAMA_FLOW_OUTPUT_RESERVED"))
        self.assertEqual(run_cli("--model", "llama3.1:8b", "--output", str(ARTIFACT)), (2, "OLLAMA_FLOW_OUTPUT_RESERVED"))
        self.assertEqual(ARTIFACT.read_bytes(), committed)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            builder.main(["--profile", "gpu"])

if __name__ == "__main__":
    unittest.main()
