#!/usr/bin/env python3
"""Offline publication checks using Python 3.11+ standard library only.

Checks committed metadata, links and deliverables, not source-data accuracy,
licenses, model performance, live services or reproduction of external datasets.
The raw CSV snapshot ZIP is a local-only optional output, never a deliverable.
"""
from __future__ import annotations

import collections
import ast
import csv
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
LOCAL_ONLY = "reports/jeju-data-snapshot-bundle.zip"


class CheckError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise CheckError(message)


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"Duplicate JSON key in {path.name}")
            result[key] = value
        return result

    def invalid_number(_value):
        raise CheckError(f"Non-finite JSON number in {path.name}")

    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=pairs,
                      parse_constant=invalid_number)


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        require(reader.fieldnames and len(reader.fieldnames) == len(set(reader.fieldnames)),
                f"Missing or duplicate CSV headers: {path.name}")
        rows = list(reader)
        require(all(None not in row and None not in row.values() for row in rows),
                f"Ragged CSV records: {path.name}")
        return rows


def unique(items, field, context):
    require(isinstance(items, list), f"Expected array: {context}")
    result = {}
    for item in items:
        key = item[field]
        require(isinstance(key, str) and key.strip() and key not in result,
                f"Missing or duplicate identifier: {context}")
        result[key] = item
    return result


def finite(value, low=None, high=None):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (low is None or value >= low)
            and (high is None or value <= high))


def local_link(target, origin, root=ROOT):
    parsed = urlsplit(target.strip("<>"))
    if parsed.scheme or parsed.netloc or not parsed.path:
        require(parsed.scheme != "file", f"Machine-local file URL in {origin.name}")
        return
    path = unquote(parsed.path)
    require(not path.startswith("/"), f"Absolute filesystem link in {origin.name}")
    resolved = (origin.parent / path).resolve()
    require(resolved.is_relative_to(root), f"Link escapes repository: {origin.name}")
    relative = resolved.relative_to(root).as_posix()
    require(relative != LOCAL_ONLY,
            f"Local-only raw ZIP must be described as code, not a download link: {origin.name}")
    require(resolved.exists(), f"Missing link target from {origin.name}: {relative}")


def check_links():
    paths = [ROOT / "README.md", ROOT / "CLAUDE.md"]
    paths += sorted((ROOT / "docs").glob("*.md")) + sorted((ROOT / "reports").glob("*.md"))
    for path in paths:
        require(path.is_file(), f"Required document missing: {path.name}")
        lines, fence = [], None
        for line in path.read_text(encoding="utf-8").splitlines():
            marker = re.match(r"^\s*(`{3,}|~{3,})", line)
            if marker:
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = None
                continue
            if fence is None:
                lines.append(line)
        text = re.sub(r"`+[^`\n]*`+", "", "\n".join(lines))
        targets = re.findall(r'!?\[[^\]\n]*\]\(\s*(<[^>\n]+>|[^\s]+?)(?:\s+[\"\'][^\n]*?[\"\'])?\s*\)', text)
        targets += re.findall(r"^\s*\[[^\]]+\]:\s*(<[^>]+>|[^\s]+)", text, re.M)
        for target in targets:
            local_link(target, path)


def check_inventory():
    inventory = read_json(ROOT / "data/raw-data-inventory.json")
    files = unique(inventory["files"], "relative_path", "inventory JSON")
    table = unique(read_csv(ROOT / "data/raw-data-inventory.csv"), "relative_path", "inventory CSV")
    catalog = unique(read_csv(ROOT / "data/confirmed-data-catalog.csv"), "relative_path", "catalog")
    require(len(files) == inventory["summary"]["csv_files"] == 55, "Expected the documented 55-file snapshot")
    require(files.keys() == table.keys() == catalog.keys(), "Inventory/catalog file sets differ")
    commit = inventory["source_commit"]
    require(re.fullmatch(r"[0-9a-f]{40}", commit), "Invalid source commit")
    require(inventory["source_unchanged_during_run"] is True, "Source changed during recorded inspection")
    for flag in ("source_row_values_included", "license_or_semantic_quality_approved", "excel_attachment_coverage_confirmed"):
        require(inventory[flag] is False, f"Unexpected provenance approval: {flag}")
    columns = read_csv(ROOT / "data/raw-data-columns.csv")
    by_file = collections.defaultdict(list)
    for column in columns:
        require(column["relative_path"] in files, "Column dictionary references an unknown file")
        by_file[column["relative_path"]].append(column)
    require(by_file.keys() == files.keys(), "Column dictionary is missing a file")
    for relative, info in files.items():
        pure = PurePosixPath(relative)
        require(relative.startswith("prototype/data/") and ".." not in pure.parts and pure.suffix == ".csv",
                "Invalid source-file path in metadata")
        require(info["source_commit"] == commit and info["git_blob"] == info["worktree_git_blob"]
                and info["head_blob_matches_worktree"] is True and info["status"] == "ok",
                f"Inconsistent recorded source verification: {relative}")
        require(re.fullmatch(r"[0-9a-f]{64}", info["sha256"]), f"Invalid SHA-256: {relative}")
        for key, value in table[relative].items():
            require(key in info and value == str(info[key]), f"Inventory CSV/JSON drift: {relative} ({key})")
        row = catalog[relative]
        for key in ("source_commit", "sha256", "data_rows", "column_count", "encoding", "byte_size"):
            require(row[key] == str(info[key]), f"Catalog/inventory drift: {relative} ({key})")
        require(row["metadata_hash_check"] in {"match", "not_recorded"}
                and row["metadata_rows_check"] in {"match", "not_recorded"}, f"Manifest mismatch: {relative}")
        require(row["license_review_status"] == "recorded_source_only; current_terms_not_rechecked",
                f"License review status changed without updating the publication contract: {relative}")
        require(row["archive_path"] == "csv/" + str(pure.relative_to("prototype/data")),
                f"Archive mapping drift: {relative}")
        for key, expected in {
            "repository_file_url": f"https://github.com/OREUM-IT/jeju-tour/blob/{commit}/{relative}",
            "snapshot_download_url": f"https://raw.githubusercontent.com/OREUM-IT/jeju-tour/{commit}/{relative}",
        }.items():
            require(unquote(row[key]) == expected, f"Unpinned or inconsistent source URL: {relative}")
        headers = json.loads(info["column_names_json"])
        group = sorted(by_file[relative], key=lambda c: int(c["column_index"]))
        require(len(headers) == info["column_count"] and len(group) == info["max_columns"],
                f"Column count drift: {relative}")
        for index, column in enumerate(group, 1):
            require(int(column["column_index"]) == index and column["original_column_name"] == headers[index - 1],
                    f"Column identity drift: {relative}")
            counts = {key: int(column[key]) for key in column if key.endswith("_count")}
            require(all(n >= 0 for n in counts.values()) and int(column["data_rows"]) == info["data_rows"],
                    f"Invalid column counts: {relative}")
            require(counts["null_count"] + counts["nonempty_count"] == info["data_rows"]
                    and counts["null_count"] == sum(counts[k] for k in (
                        "blank_count", "nan_literal_count", "other_null_literal_count", "missing_field_count"))
                    and counts["present_nonempty_count"] == sum(counts[k] for k in (
                        "nonempty_count", "nan_literal_count", "other_null_literal_count")),
                    f"Column missingness arithmetic drift: {relative}")
    summary = inventory["summary"]
    for field, expected in {"total_bytes": sum(f["byte_size"] for f in files.values()),
                            "data_rows": sum(f["data_rows"] for f in files.values()),
                            "column_records": len(columns),
                            "encodings": dict(collections.Counter(f["encoding"] for f in files.values())),
                            "statuses": dict(collections.Counter(f["status"] for f in files.values()))}.items():
        require(summary[field] == expected, f"Inventory summary drift: {field}")
    kinds = collections.Counter(row["data_kind"] for row in catalog.values())
    require(kinds == {"source_file_snapshot": 37, "jeju_filtered_snapshot": 17, "derived_aggregate": 1},
            "Snapshot source/transformation classification drift")
    require(catalog["prototype/data/public-csv/temporal-demand/profiles.csv"]["data_kind"] == "derived_aggregate",
            "Temporal profiles must remain labelled as a derived aggregate")


def check_personas():
    data = read_json(ROOT / "data/persona-prototypes.json")
    themes = unique(data["themes"], "id", "persona themes")
    scalars = unique(data["scalars"], "id", "persona scalar preferences")
    prototypes = unique(data["prototypes"], "id", "persona prototypes")
    require(data["version"] and data["provenance"] and themes and scalars and len(prototypes) >= 2,
            "Persona identity or provenance is missing")
    weights = data["weights"]
    require(weights.keys() == {"themes", *scalars} and all(finite(v, 0, 1) for v in weights.values())
            and math.isclose(sum(weights.values()), 1), "Persona weights are invalid")
    for key, scalar in scalars.items():
        require(scalar["weight"] == weights[key], "Scalar weight differs from the matching weights")
    for prototype in prototypes.values():
        require(len(prototype["themes"]) == len(themes) and all(finite(v, 0, 1) for v in prototype["themes"])
                and math.isclose(sum(prototype["themes"]), 1)
                and all(finite(prototype[key], 0, 1) for key in scalars), "Invalid persona feature vector")
    thresholds = data["thresholds"]
    require(finite(thresholds["min_coverage"], 0, 1) and finite(thresholds["mixed_margin"], 0, 100)
            and finite(thresholds["min_score"], 0, 100), "Invalid persona matching thresholds")
    exported = unique(read_csv(ROOT / "reports/assets/persona-prototypes.csv"), "id", "persona CSV")
    require(exported.keys() == prototypes.keys(), "Persona CSV/definition identities differ")
    for key, prototype in prototypes.items():
        row = exported[key]
        require(row["label"] == prototype["label"] and row["provenance"] == "manually_authored_not_learned",
                "Persona CSV provenance or labels drifted")
        for feature, value in [*zip(themes, prototype["themes"]), *((s, prototype[s]) for s in scalars)]:
            require(math.isclose(float(row[feature]), value), "Persona CSV/definition feature drift")
    projection = read_json(ROOT / "reports/assets/persona-projection.json")
    dimensions = len(themes) + len(scalars)
    require(len(projection["mean"]) == dimensions and len(projection["components"]) == 2
            and all(len(row) == dimensions for row in projection["components"])
            and len(projection["points"]) == len(prototypes) and all(len(row) == 2 for row in projection["points"])
            and len(projection["variance_ratio"]) == 2, "Persona projection dimensions drifted")
    require(all(finite(v) for row in [projection["mean"], *projection["components"], *projection["points"]] for v in row)
            and all(finite(v, 0, 1) for v in projection["variance_ratio"])
            and sum(projection["variance_ratio"]) <= 1 + 1e-9, "Invalid persona projection numbers")


def check_model_registry():
    data = read_json(ROOT / "data/ai-agent-models.json")
    require(data["schemaVersion"] == "1.0" and data["registryVersion"], "Missing model registry version")
    date.fromisoformat(data["updatedOn"])
    require(data["isRuntimeConfiguration"] is False and data["activatesProductionChanges"] is False,
            "The documentation registry must not activate runtime changes")
    local_link(data["documentation"], ROOT / "registry-root.md")
    groups = {key: unique(data[key], "key", key) for key in
              ("sources", "hosts", "engines", "models", "methods", "toolConnections")}
    require(all(groups.values()), "Model registry sections must not be empty")
    kinds = {"local_artifact_observed", "reference_source_observed", "official_source_reviewed",
             "user_reported", "unread_reference"}
    for source in groups["sources"].values():
        require(source["kind"] in kinds, "Unknown source evidence kind")
        for path in source.get("paths", []):
            local_link(path, ROOT / "registry-root.md")
        if "commit" in source:
            require(re.fullmatch(r"[0-9a-f]{40}", source["commit"])
                    and source["commit"] in source["url"], "Unpinned registry source")

    def references(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "sourceRefs":
                    require(isinstance(item, list) and item and all(ref in groups["sources"] for ref in item),
                            "Unresolved source reference in registry")
                elif key in {"hostRef", "applicationHostRef", "engineRef"}:
                    require(item in groups["engines" if key == "engineRef" else "hosts"],
                            "Unresolved engine/host reference in registry")
                references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)
    references(data)
    for model in groups["models"].values():
        require(model["engineRef"] in groups["engines"] and model["role"]
                and isinstance(model["role"], list) and model["status"] and model["sourceRefs"]
                and model["inferenceValidation"] and model["qualityValidation"] and model["nextTests"],
                "Model role, evidence or separate validation status is missing")
        require(model["runtimeResolvedModelId"] is None or isinstance(model["runtimeResolvedModelId"], str),
                "Unknown model IDs must be null, not an invented structure")
    roles = unique(data["unselectedModelRoles"], "role", "unselected model roles")
    require(all(role["selectedModelId"] is None for role in roles.values()), "An unselected role contains a selected model")
    persona = read_json(ROOT / "data/persona-prototypes.json")
    for method in groups["methods"].values():
        if "prototypeCount" in method:
            require(method["prototypeCount"] == len(persona["prototypes"]) and method["version"] == persona["version"],
                    "Model registry/persona version drift")
    dependencies = read_json(ROOT / "services/jeju-mcp/package.json")["dependencies"]
    for connection in groups["toolConnections"].values():
        require(connection["isModel"] is False, "MCP connections must not be classified as models")
        if "sdkVersion" in connection:
            require(connection["sdkVersion"] == dependencies["@modelcontextprotocol/sdk"]
                    and connection["schemaLibraryVersion"] == dependencies["zod"], "Registry/MCP dependency version drift")


def forbidden_name(name):
    path = PurePosixPath(name.replace("\\", "/"))
    return (any(part in {".git", ".local", "node_modules", "__pycache__", ".aws", ".ssh"} for part in path.parts)
            or any(part == ".env" or part.startswith(".env.") for part in path.parts)
            or path.suffix.lower() == ".env"
            or path.suffix.lower() in {".pyc", ".pem", ".p12", ".pfx", ".key"}
            or path.name in {"id_rsa", "id_ed25519", ".netrc"})


def check_deliverables():
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], check=True,
                             capture_output=True).stdout.decode().split("\0")
    require(LOCAL_ONLY not in tracked, "Raw snapshot ZIP is local-only and must not be committed")
    for name in filter(None, tracked):
        require(not forbidden_name(name), f"Private/local filename in Git: {name}")
    archives = [ROOT / "reports/jeju-personal-tour-agent-design.docx", ROOT / "reports/ollama-tunnel-starter.zip"]
    for path in archives:
        with ZipFile(path) as archive:
            names = archive.namelist()
            require(len(names) == len(set(names)), f"Duplicate archive member: {path.name}")
            require(sum(item.file_size for item in archive.infolist()) <= 100 * 1024 * 1024,
                    f"Unexpected expanded archive size: {path.name}")
            for item in archive.infolist():
                name = item.filename
                require(not name.startswith(("/", "\\")) and ".." not in PurePosixPath(name).parts
                        and "\\" not in name and not forbidden_name(name)
                        and not stat.S_ISLNK(item.external_attr >> 16) and not (item.flag_bits & 1),
                        f"Unsafe or encrypted archive member: {path.name}")
            require(archive.testzip() is None, f"Archive CRC failure: {path.name}")
            if path.suffix == ".docx":
                require({"[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml"} <= set(names),
                        "Word report is missing required package parts")
                for name in names:
                    if name.endswith((".xml", ".rels")):
                        ET.fromstring(archive.read(name))
            else:
                expected = {"README.md", ".gitignore", "gateway.mjs", "gateway.test.mjs", "configure.mjs", "check.mjs"}
                require(set(names) == {"ollama-tunnel/" + name for name in expected}, "Starter ZIP contents drifted")
                for name in expected:
                    require(archive.read("ollama-tunnel/" + name) == (ROOT / "examples/ollama-tunnel" / name).read_bytes(),
                            f"Starter ZIP differs from example source: {name}")


PLACE_ID = re.compile(r"^(?:kk|osm|cur|vj|nv|bf|olle|trail|hy|drama|bike|hub)-")


def check_agent_data():
    """Theme map design rules and the aggregate-only agent catalog coverage report."""
    persona = read_json(ROOT / "data/persona-prototypes.json")
    themes = [theme["id"] for theme in persona["themes"]]
    data = read_json(ROOT / "data/theme-category-map.json")
    require(data["version"] and data["provenance"] and data["personaVersion"] == persona["version"],
            "Theme map/persona version drift")
    require(data["themes"] == themes, "Theme map themes must equal the persona themes in order")
    categories = data["appCategories"]
    require(len(categories) == len(set(categories)) == 10 and set(data["categoryWeights"]) == set(categories),
            "Theme map must have one row per app category")

    def valid_weights(weights, allow_empty):
        if not weights:
            return allow_empty
        return (all(key in themes and finite(value, 0, 1) and value > 0 for key, value in weights.items())
                and math.isclose(sum(weights.values()), 1, abs_tol=1e-9))

    for category, weights in data["categoryWeights"].items():
        require(valid_weights(weights, category == "stay") and (category != "stay" or not weights),
                "Invalid theme weights for an app category")
    override_ids = set()
    for rule in data["overrides"]:
        require(rule["id"] not in override_ids and rule["categories"]
                and set(rule["categories"]) <= set(categories) - {"stay"}
                and (rule.get("kinds") or rule.get("namePattern")) and valid_weights(rule["weights"], False),
                "Invalid theme override")
        override_ids.add(rule["id"])
        re.compile(rule.get("namePattern", ""))
    reachable = {key for weights in data["categoryWeights"].values() for key in weights}
    reachable |= {key for rule in data["overrides"] for key in rule["weights"]}
    require(reachable == set(themes), "Every persona theme must be reachable from the theme map")

    report = read_json(ROOT / "reports/agent-catalog-coverage.json")
    require(report["kind"] == "jeju-agent-catalog-coverage" and "places" not in report,
            "Coverage report must stay aggregate-only")
    source = report["source"]
    require(re.fullmatch(r"[0-9a-f]{40}", source["commit"]) and re.fullmatch(r"[0-9a-f]{64}", source["rawSha256"])
            and source["themeMapVersion"] == data["version"] and source["personaVersion"] == persona["version"],
            "Coverage report provenance drift")
    totals = report["totals"]
    require(set(totals["byCategory"]) == set(categories)
            and sum(totals["byCategory"].values()) == totals["places"] == sum(totals["byOrigin"].values()),
            "Coverage report totals drift")
    require(set(report["themes"]) == set(themes), "Coverage report themes drift")

    def strings(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
        elif isinstance(value, str):
            yield value

    require(not any(PLACE_ID.match(text) for text in strings(report)), "Coverage report must not contain place IDs")


EVALUATION_HARD_CASES = {"walk_minimal", "accessibility_unknown", "date_conflict", "disallowed_id", "empty_result"}
EVALUATION_HARD_REQUIREMENTS = {"indoor_required", "open_at", "wheelchair", "budget_cap"}
EVALUATION_AUTHOR_KINDS = {"in_session_llm", "external_llm", "human"}
# The text rules every query passes before it enters a set, in the order the spec documents them.
EVALUATION_TEXT_CHECKS = ["length", "single_line", "korean", "request_wording", "no_place_id", "named_places",
                          "condition_cues", "no_unstated_cues", "no_candidate_names"]
EVALUATION_CUE_MAPS = ("zone", "theme", "category", "transport", "companion", "walk")
EVALUATION_CUE_LISTS = ("indoor_required", "rain", "crowd_avoid", "wheelchair", "exclude_places", "visited")
# Condition kinds with extra words a query must not use unless it states that kind.
EVALUATION_UNSTATED_KINDS = ("zone", "transport", "companion", "walk", "indoor_required", "crowd_avoid", "open_at", "wheelchair",
                             "budget_cap", "exclude_places", "trip_dates")
# The reference method caps a query at 200 characters; the spec may be stricter, never looser.
EVALUATION_MAX_QUERY_LENGTH = 200
# Unlike PLACE_ID above, this also finds an ID embedded in a longer string or key, in any letter case.
PLACE_ID_ANYWHERE = re.compile(r"(?:^|[^a-z0-9])(?:kk|osm|cur|vj|nv|bf|olle|trail|hy|drama|bike|hub)-[a-z0-9]", re.I)
# Every key the summary may contain besides identifiers taken from the spec. A new summary field must be added here
# on purpose: the list is the publication contract of the aggregate report.
EVALUATION_SUMMARY_KEYS = {
    "schemaVersion", "kind", "source", "contentSha256", "totals", "gold", "generation", "queries",
    "repository", "commit", "placesContentSha256", "rawSha256", "themeMapVersion", "personaVersion", "specVersion",
    "specSha256", "rulesVersion", "seed", "anchorDate",
    "items", "requestedItems", "droppedItems", "byPersona", "bySlot", "byDifficulty", "byExpectedAction", "byHardCase",
    "byStatedConditions", "byConditionType", "bySelectorSource", "byTheme", "byCategory", "byZone", "byConflict",
    "byEmptyMechanism",
    "allowedSetSize", "relevantSetSize", "count", "min", "median", "max", "itemsWithEmptyAllowedSet",
    "itemsWithEmptyRelevantSet", "itemsWithVerifiedPass", "hardRequirements", "itemsExpectingUnknown",
    "candidateVerdicts", "candidateSignals", "pass", "fail", "unknown", "supports", "contradicts", "none", "verified",
    "reported", "inferred", "noticeRequiredItems", "noticeTopics", "forbiddenPlaces", "total", "byReason",
    "offProfileSelectors", "fallbackTierItems", "reusedCellItems", "walkMinimalItemsWithRouteExclusion",
    "accessibilityItemsWithListedCandidates", "disallowedIdItemsRankedByPopularity",
    "mode", "promptVersion", "authored", "template", "authorKind", "authoringRecord",
    "textRules", "minLength", "maxLength", "candidateNameMinLength", "checks", "textLength",
    "1", "2", "3", "4", "theme", "category", "persona", "date_conflict", "no_candidates",
    "requirements_contradicted:jointly", "empty_cell", "all_routes_minimal_walk", "all_excluded",
    "user_excluded", "route_for_minimal_walk", "verified_fail",
}


def check_evaluation_set():
    """Step-3 evaluation spec rules and the aggregate-only evaluation set summary (no place ID, name or query text)."""
    persona = read_json(ROOT / "data/persona-prototypes.json")
    theme_map = read_json(ROOT / "data/theme-category-map.json")
    spec = read_json(ROOT / "data/evaluation-spec.json")
    require(spec["version"] and spec["provenance"] and spec["seed"] and spec["rulesVersion"]
            and spec["personaVersion"] == persona["version"] and spec["themeMapVersion"] == theme_map["version"],
            "Evaluation spec/persona/theme map version drift")
    date.fromisoformat(spec["anchorDate"])
    require(finite(spec["themeThreshold"], 0, 1) and spec["themeThreshold"] > 0 and finite(spec["personaFloor"], 0, 1),
            "Invalid evaluation thresholds")
    zones = unique(spec["zones"], "id", "evaluation zones")
    categories = unique(spec["categories"], "id", "evaluation categories")
    require(set(categories) == set(theme_map["appCategories"]) - {"stay"}, "Evaluation categories must be the non-stay app categories")
    conditions = unique(spec["conditions"], "id", "evaluation conditions")
    require(all(row["kind"] in {"filter", "context", "preference", "hard_requirement"} and row["label"] and row["evidence"]
                and row["rule"] for row in conditions.values()), "Every evaluation condition needs a kind, its evidence and its rule")
    require({key for key, row in conditions.items() if row["kind"] == "hard_requirement"} == EVALUATION_HARD_REQUIREMENTS,
            "Hard requirements changed without updating the publication contract")
    actions = [row["id"] for row in spec["expectedActions"]]
    require(actions == ["impossible", "ask_clarification", "cannot_confirm", "recommend"] and all(row["when"] for row in spec["expectedActions"]),
            "Expected actions must stay in precedence order")
    difficulties = unique(spec["difficulties"], "id", "evaluation difficulties")
    require(set(difficulties) == {"simple", "compound", "conflict"}, "Difficulty levels drifted")
    require(set(unique(spec["hardCases"], "id", "evaluation hard cases")) == EVALUATION_HARD_CASES, "Mandatory hard cases drifted")
    slots = unique(spec["blueprint"]["slots"], "id", "evaluation blueprint slots")
    require(spec["blueprint"]["itemsPerPersona"] == len(slots)
            and EVALUATION_HARD_CASES <= {slot.get("hardCase") for slot in slots.values()}
            and all(set(sum(slot["extras"], [])) <= set(conditions) for slot in slots.values()),
            "Blueprint must cover every mandatory hard case with known conditions")
    metrics = unique(spec["metrics"]["definitions"], "id", "evaluation metrics")
    require(isinstance(spec["metrics"]["recallK"], int) and spec["metrics"]["recallK"] > 0
            and {"disallowed_id_rate", "hard_requirement_misjudgement", "recall_at_k", "persona_theme_match",
                 "precision_at_k", "confirmation_notice_rate"} <= set(metrics), "Evaluation metrics are incomplete")
    authoring = spec["queryAuthoring"]
    require(authoring["promptVersion"] and authoring["prompt"], "Query authoring prompt is missing")
    rules = authoring["textRules"]
    checks = [row["id"] for row in rules["checks"]]
    require(set(rules) == {"minLength", "maxLength", "candidateNameMinLength", "checks"}
            and all(isinstance(rules[key], int) and not isinstance(rules[key], bool)
                    for key in ("minLength", "maxLength", "candidateNameMinLength"))
            and 1 <= rules["minLength"] < rules["maxLength"] <= EVALUATION_MAX_QUERY_LENGTH
            and 2 <= rules["candidateNameMinLength"] <= 4
            and checks == EVALUATION_TEXT_CHECKS and all(row["rule"] for row in rules["checks"]),
            "Query text rules drifted from the publication contract")
    cues = authoring["cues"]
    listed = lambda words: isinstance(words, list) and words and all(isinstance(word, str) and word.strip() == word and word for word in words)
    require(set(cues) == set(EVALUATION_CUE_MAPS) | set(EVALUATION_CUE_LISTS)
            and set(cues["zone"]) == set(zones) and set(cues["theme"]) == set(theme_map["themes"])
            and set(cues["category"]) == set(categories)
            and all(set(cues[key]) == set(conditions[key]["values"]) for key in ("transport", "companion", "walk"))
            and all(listed(words) for key in EVALUATION_CUE_MAPS for words in cues[key].values())
            and all(listed(cues[key]) for key in EVALUATION_CUE_LISTS),
            "Query cue words must cover every zone, theme, category and condition value")
    unstated = authoring["unstatedWords"]
    matching = authoring["cueMatching"]
    all_cues = [word for key in EVALUATION_CUE_MAPS for words in cues[key].values() for word in words]
    all_cues += [word for key in EVALUATION_CUE_LISTS for word in cues[key]]
    require(set(unstated) == set(EVALUATION_UNSTATED_KINDS) and all(listed(words) for words in unstated.values())
            and set(matching) == {"wordStart", "ignoredPhrases", "note"} and matching["note"]
            and set(matching["wordStart"]) <= set(EVALUATION_CUE_MAPS) | set(EVALUATION_CUE_LISTS)
            and len(set(matching["wordStart"])) == len(matching["wordStart"])
            and isinstance(matching["ignoredPhrases"], list)
            and (not matching["ignoredPhrases"] or listed(matching["ignoredPhrases"]))
            and not set(matching["ignoredPhrases"]) & set(all_cues),
            "Unstated-condition words or cue matching options drifted from the publication contract")
    # The method this spec follows is pinned to one commit of the authors' code, and the registry names the same commit.
    reference = spec["methodReference"]
    registry = read_json(ROOT / "data/ai-agent-models.json")
    pinned = {row["key"]: row for row in registry["sources"]}.get(reference["id"])
    date.fromisoformat(reference["reviewedOn"])
    require(pinned is not None and re.fullmatch(r"[0-9a-f]{40}", reference["commit"]) and pinned.get("commit") == reference["commit"]
            and isinstance(reference["repository"], str) and pinned.get("repository") == reference["repository"]
            and pinned.get("license") == reference["license"] and pinned.get("reviewedOn") == reference["reviewedOn"]
            and pinned.get("codeExecuted") is False and reference["scope"],
            "Evaluation method reference must match the pinned registry source")

    report = read_json(ROOT / "reports/evaluation-set-summary.json")
    require(report["kind"] == "jeju-agent-evaluation-set-summary" and not {"items", "places", "candidates", "briefs"} & set(report),
            "Evaluation summary must stay aggregate-only")
    source = report["source"]
    coverage = read_json(ROOT / "reports/agent-catalog-coverage.json")
    require(all(source[key] == coverage["source"][key] for key in
                ("repository", "commit", "placesContentSha256", "rawSha256", "themeMapVersion", "personaVersion")),
            "Evaluation summary and catalog coverage report come from different catalogs")
    def plain_numbers(value):
        # JavaScript prints 1.0 as 1, so integral floats are folded before hashing to keep both sides identical.
        if isinstance(value, dict):
            return {key: plain_numbers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [plain_numbers(item) for item in value]
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    canonical = json.dumps(plain_numbers(spec), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    require(source["specVersion"] == spec["version"] and source["rulesVersion"] == spec["rulesVersion"]
            and source["seed"] == spec["seed"] and source["anchorDate"] == spec["anchorDate"]
            and source["specSha256"] == hashlib.sha256(canonical).hexdigest(),
            "Evaluation summary was not generated from the committed spec; rebuild it")
    totals = report["totals"]
    require(totals["items"] > 0 and totals["items"] + totals["droppedItems"] == totals["requestedItems"]
            == len(persona["prototypes"]) * len(slots), "Evaluation summary item counts drift")
    for key in ("byPersona", "bySlot", "byDifficulty", "byExpectedAction", "byStatedConditions", "bySelectorSource", "byZone"):
        require(sum(totals[key].values()) == totals["items"], f"Evaluation summary totals drift: {key}")
    require(set(totals["byPersona"]) <= {row["id"] for row in persona["prototypes"]} and set(totals["bySlot"]) <= set(slots)
            and set(totals["byExpectedAction"]) == set(actions) and set(totals["byDifficulty"]) == set(difficulties)
            and set(totals["byZone"]) <= set(zones) | {"none"} and set(totals["byCategory"]) <= set(categories)
            and set(totals["byTheme"]) <= set(theme_map["themes"]) and set(totals["byConditionType"]) == set(conditions),
            "Evaluation summary uses identifiers the spec does not define")
    require(set(totals["byHardCase"]) == EVALUATION_HARD_CASES and all(count >= 1 for count in totals["byHardCase"].values()),
            "Every mandatory hard case must be present in the evaluation set")
    require(all(1 <= int(key) <= 4 for key in totals["byStatedConditions"]), "Items must state 1 to 4 conditions")
    gold = report["gold"]
    require(gold["itemsWithEmptyAllowedSet"] == totals["byExpectedAction"]["impossible"], "Impossible must mean an empty candidate set")
    require(set(gold["hardRequirements"]) <= EVALUATION_HARD_REQUIREMENTS and set(gold["noticeTopics"]) <= set(spec["noticeTopics"]),
            "Evaluation summary requirement or notice identifiers drifted")
    if all(row["verified"] == 0 for key, row in coverage["hardRequirementReadiness"].items() if key != "note"):
        require(gold["itemsWithVerifiedPass"] == 0 and all(
            row["candidateVerdicts"]["pass"] == 0 and row["candidateVerdicts"]["fail"] == 0
            and row["itemsExpectingUnknown"] == row["items"] for row in gold["hardRequirements"].values()),
            "A hard requirement was judged without verified evidence in the catalog")
    queries = report["queries"]
    authored = queries["authored"] > 0
    require(queries["authored"] + queries["template"] == totals["items"] and queries["promptVersion"] == spec["queryAuthoring"]["promptVersion"]
            and queries["mode"] == ("authored" if authored else "template_only")
            and queries["authoringRecord"] == ("local_only" if authored else "none")
            and (queries["authorKind"] in EVALUATION_AUTHOR_KINDS if authored else queries["authorKind"] is None)
            and (re.fullmatch(r"[0-9a-f]{64}", report["contentSha256"]["queries"] or "") if authored
                 else report["contentSha256"]["queries"] is None)
            and re.fullmatch(r"[0-9a-f]{64}", report["contentSha256"]["gold"]),
            "Evaluation summary query counts, hashes or authoring record drifted")
    lengths = queries["textLength"]
    require(queries["textRules"] == {"minLength": rules["minLength"], "maxLength": rules["maxLength"],
                                     "candidateNameMinLength": rules["candidateNameMinLength"], "checks": checks}
            and set(lengths) == {"authored", "template"} and lengths["authored"]["count"] == queries["authored"]
            and lengths["template"]["count"] == totals["items"]
            and all(row["count"] == 0 or rules["minLength"] <= row["min"] <= row["median"] <= row["max"] <= rules["maxLength"]
                    for row in lengths.values()),
            "Evaluation summary text rules or query lengths drifted from the spec")
    require(set(totals["byConflict"]) <= {"date_conflict", "no_candidates", "requirements_contradicted:jointly"}
            | {"requirement_contradicted:" + key for key in EVALUATION_HARD_REQUIREMENTS}
            and set(totals["byEmptyMechanism"]) <= {"empty_cell", "all_routes_minimal_walk", "all_excluded"}
            and set(totals["bySelectorSource"]) <= {"theme", "category", "persona"}
            and set(gold["forbiddenPlaces"]["byReason"]) <= {"user_excluded", "route_for_minimal_walk", "verified_fail"},
            "Evaluation summary uses a conflict, mechanism, selector or exclusion reason the rules do not define")

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key, True
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, str):
            yield value, False

    vocabulary = {report["kind"], report["schemaVersion"], source["repository"], spec["version"], spec["rulesVersion"], spec["seed"],
                  spec["anchorDate"], persona["version"], theme_map["version"], spec["queryAuthoring"]["promptVersion"],
                  "template_only", "authored", "none", "local_only"} | EVALUATION_AUTHOR_KINDS | set(EVALUATION_TEXT_CHECKS)
    known_keys = (EVALUATION_SUMMARY_KEYS | set(zones) | set(categories) | set(conditions) | set(slots) | set(actions)
                  | set(difficulties) | EVALUATION_HARD_CASES | set(spec["noticeTopics"]) | set(theme_map["themes"])
                  | {row["id"] for row in persona["prototypes"]}
                  | {"requirement_contradicted:" + key for key in EVALUATION_HARD_REQUIREMENTS})
    for text, is_key in walk(report):
        require(not re.search(r"[\u3131-\u318e\uac00-\ud7a3]", text) and not PLACE_ID_ANYWHERE.search(text),
                "Evaluation summary must not contain place names, place IDs or query text")
        if is_key:
            require(text in known_keys, "Evaluation summary has a key outside its publication contract")
        else:
            require(text in vocabulary or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", text),
                    "Evaluation summary values must be known identifiers or hashes")


def check_langflow():
    """The committed Langflow starter flow and settings example carry no credentials and keep safe defaults."""
    path = ROOT / "services/langflow/jeju-tour-agent.flow.json"
    text = path.read_text(encoding="utf-8")
    flow = read_json(path)
    version = tuple(int(part) for part in re.findall(r"\d+", flow["last_tested_version"])[:3])
    require(version >= (1, 13, 0), "Langflow flows must be tested on 1.13.0 or newer (earlier releases are unpatched)")
    nodes = {node["data"]["type"]: node for node in flow["data"]["nodes"]}
    require({"ChatInput", "Agent", "MCPTools", "ChatOutput"} <= set(nodes), "Starter flow is missing a core node")
    mcp = nodes["MCPTools"]["data"]["node"]
    template = mcp["template"]
    require(template["mcp_server"]["value"] == {"name": "jeju-mcp", "config": {}} and not template["headers"]["value"]
            and mcp.get("tool_mode") is True, "MCP node must reference jeju-mcp by name without inline headers")
    tools = sorted(row["name"] for row in template["tools_metadata"]["value"])
    require(tools == ["constraints.check", "places.get", "places.search"], "MCP node tool list drifted from jeju-mcp")
    require(any(edge["source"] == nodes["MCPTools"]["id"] and edge["data"]["targetHandle"]["fieldName"] == "tools"
                for edge in flow["data"]["edges"]), "MCP tools must be wired into the agent")
    for node in flow["data"]["nodes"]:
        for field in node["data"]["node"].get("template", {}).values():
            if isinstance(field, dict) and (field.get("password") or field.get("name") == "api_key"):
                require(field.get("value") in ("", None), "Starter flow contains a credential value")
    require(not re.search(r"Bearer (?!\{\{JEJU_MCP_TOKEN\}\})\S", text), "Starter flow contains a literal bearer credential")
    settings = {}
    for line in (ROOT / "services/langflow/langflow.env.example").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            key, value = line.split("=", 1)
            settings[key] = value
    require(settings.get("LANGFLOW_AUTO_LOGIN") == "false" and settings.get("LANGFLOW_HOST") == "127.0.0.1"
            and settings.get("LANGFLOW_SSRF_ALLOWED_HOSTS") == "127.0.0.1"
            and settings.get("LANGFLOW_SUPERUSER_PASSWORD") == "REPLACE_WITH_A_STRONG_PASSWORD"
            and "LANGFLOW_SECRET_KEY" not in settings, "Langflow settings example lost a safe default or holds a secret")


def check_baseline_summary():
    """The published run is a scorer self-check, never a fabricated provider benchmark."""
    report = read_json(ROOT / "reports/baseline-harness-self-check.json")
    evaluation = read_json(ROOT / "reports/evaluation-set-summary.json")
    require(set(report) == {"schemaVersion", "kind", "mode", "resultType", "system", "planSha256", "binding",
                            "provenanceSha256", "runSha256", "startedAt", "completedAt", "wording", "groups", "securityEvaluation"},
            "Unexpected fields in the public baseline aggregate")
    require(report["schemaVersion"] == "1.0" and report["kind"] == "jeju-baseline-summary"
            and report["mode"] == report["system"] == "oracle_self_check"
            and report["resultType"] == "scorer_self_check_not_model_quality"
            and report["securityEvaluation"] == "not_executed", "Self-check must not claim model or security execution")
    binding = report["binding"]
    require(set(binding) == {"goldSha256", "queriesSha256", "specSha256", "catalogSha256", "personasSha256", "themeMapSha256"}
            and binding["goldSha256"] == evaluation["contentSha256"]["gold"] and binding["queriesSha256"] is None,
            "Self-check is not bound to the restored template-only evaluation set")
    hashes = [report[key] for key in ("planSha256", "provenanceSha256", "runSha256")]
    hashes += [value for key, value in binding.items() if key != "queriesSha256"]
    require(all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes), "Invalid baseline provenance hash")
    require(report["wording"] == {"templateItems": 96, "writtenItems": 0, "identicalWrittenItems": 0, "distinctWrittenItems": 0},
            "Unavailable authored queries must not be counted as restored")
    require(len(report["groups"]) == 1, "Expected one template-only self-check group")
    group = report["groups"][0]
    require(group["system"] == "oracle_self_check" and group["variant"] == "template"
            and group["plannedCases"] == group["successfulResponses"] == 96 and group["failedCases"] == 0
            and group["completionRate"] == group["actionAccuracyAllCases"] == 1,
            "Self-check must retain all 96 cases")
    metrics = group["metrics"]
    require(metrics["items"] == 96 and metrics["disallowedIds"]["rate"] == 0
            and metrics["hardRequirementMisjudgement"]["rate"] == 0
            and metrics["recallAtK"]["mean"] == metrics["precisionAtK"]["mean"] == 1
            and metrics["latencyMs"] == {"measuredItems": 0, "p50": None, "p95": None}
            and metrics["costUsd"] == {"measuredItems": 0, "total": None},
            "Self-check results or unmeasured model latency/cost were changed")
    # Only fixed labels, timestamps and hashes may occur as string values in a published summary.
    safe_labels = {"1.0", "jeju-baseline-summary", "oracle_self_check", "scorer_self_check_not_model_quality", "template", "not_executed"}
    def check_values(value):
        if isinstance(value, dict):
            for child in value.values():
                check_values(child)
        elif isinstance(value, list):
            for child in value:
                check_values(child)
        elif isinstance(value, str):
            require(value in safe_labels or re.fullmatch(r"[0-9a-f]{64}", value)
                    or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z", value),
                    "Free text or raw identifiers must not be published in the self-check summary")
        else:
            require(value is None or finite(value, 0), "Invalid public baseline measurement")
    check_values(report)


def check_vertex_flow():
    """Pin the reviewed large flow, including embedded code; credentials remain a named runtime global."""
    flow = read_json(ROOT / "services/langflow/jeju-tour-agent-vertex.flow.json")
    text = json.dumps(flow, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    require(hashlib.sha256(text.encode()).hexdigest() == "2507a4c385879e958ffa14170cd935985a14898ad55cde5ad4f05b104e7d71f0",
            "Vertex flow differs from the reviewed credential-free artifact; review its full nested diff before updating the pin")
    require(not re.search(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----|AIza[A-Za-z0-9_-]{30,}|ya29\.[A-Za-z0-9_-]+", text)
            and not re.search(r"[a-z0-9._-]+@[a-z0-9.-]+\.iam\.gserviceaccount\.com", text, re.I)
            and not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text, re.I),
            "Vertex flow contains credential material, an account identity or a runtime UUID")
    nodes = {node["data"]["type"]: node for node in flow["data"]["nodes"]}
    require({"ChatInput", "Agent", "MCPTools", "ChatOutput"} <= set(nodes), "Vertex flow is missing core nodes")
    agent = nodes["Agent"]["data"]["node"]["template"]
    require(agent["api_key"]["value"] == "JEJU_VERTEX_SERVICE_ACCOUNT_JSON" and agent["api_key"]["load_from_db"] is True
            and agent["api_key"]["password"] is True and agent["project_id"]["value"] in ("", None),
            "Vertex credentials must use the exact Credential global, with no inline project or account")
    require(agent["add_current_date_tool"]["value"] is False, "Vertex baseline must keep the supplied scenario date")
    require(hashlib.sha256(agent["code"]["value"].encode()).hexdigest() == "8476b6eb666a9e3e8cbc3fa4798ebcef01f8e360ab261b085bd8ece469db8fdf"
            and hashlib.sha256(agent["system_prompt"]["value"].encode()).hexdigest() == "c2b62ca27e33ce6aed3ba15eaea7885126d8faa1240c83dd048b0dd387884580",
            "Vertex runtime code or structured prompt changed without review")
    for node in flow["data"]["nodes"]:
        for name, field in node["data"]["node"].get("template", {}).items():
            if isinstance(field, dict) and (field.get("password") or field.get("name") == "api_key"):
                allowed = node["data"]["type"] == "Agent" and name == "api_key"
                require(field.get("value") == "JEJU_VERTEX_SERVICE_ACCOUNT_JSON" if allowed else field.get("value") in ("", None),
                        "Vertex flow contains an inline credential or an unexpected secret reference")
    mcp = nodes["MCPTools"]["data"]["node"]["template"]
    require(mcp["mcp_server"]["value"] == {"name": "jeju-mcp", "config": {}} and not mcp["headers"]["value"],
            "Vertex flow must resolve jeju-mcp without inline headers or server identifiers")


def check_validated_vertex_flow():
    """Pin the reviewed v2 graph and require that the tested helpers are the actual embedded code."""
    root = ROOT / "services/langflow"
    flow = read_json(root / "jeju-tour-agent-vertex-v2.flow.json")
    text = json.dumps(flow, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    require(hashlib.sha256(text.encode()).hexdigest() == "020e2b0b05f9fa291ba18b91b9c10e78c8247b4c52deed217287c2cab989dc5e",
            "Validated Vertex flow changed; review nested code, graph and credential references before updating the pin")
    require(not re.search(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----|AIza[A-Za-z0-9_-]{30,}|ya29\.[A-Za-z0-9_-]+", text)
            and not re.search(r"[a-z0-9._-]+@[a-z0-9.-]+\.iam\.gserviceaccount\.com", text, re.I)
            and not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text, re.I),
            "Validated flow contains a credential, account identifier or runtime UUID")
    nodes = {node["data"]["type"]: node for node in flow["data"]["nodes"]}
    require(len(flow["data"]["nodes"]) == len(nodes) == 6
            and set(nodes) == {"ChatInput", "ChatOutput", "Agent", "MCPTools", "JejuPrepareRequest", "JejuFinalValidation"},
            "Validated flow must have exactly the reviewed six components")
    kind_by_id = {node["id"]: kind for kind, node in nodes.items()}
    edges = {(kind_by_id[edge["source"]], kind_by_id[edge["target"]], edge["data"]["targetHandle"]["fieldName"])
             for edge in flow["data"]["edges"]}
    require(len(flow["data"]["edges"]) == len(edges) == 6 and edges == {
        ("ChatInput", "JejuPrepareRequest", "original_request"),
        ("JejuPrepareRequest", "Agent", "input_value"),
        ("JejuPrepareRequest", "JejuFinalValidation", "prepared_request"),
        ("MCPTools", "Agent", "tools"), ("Agent", "JejuFinalValidation", "agent_response"),
        ("JejuFinalValidation", "ChatOutput", "input_value"),
    }, "Validated flow must preserve the original request and have no final-validator bypass")
    for kind, node in nodes.items():
        for name, field in node["data"]["node"].get("template", {}).items():
            if not isinstance(field, dict) or not (field.get("password") or field.get("name") == "api_key"):
                continue
            reference = "JEJU_VERTEX_SERVICE_ACCOUNT_JSON" if kind == "Agent" and name == "api_key" else (
                "JEJU_MCP_TOKEN" if kind in {"JejuPrepareRequest", "JejuFinalValidation"} and name == "service_token" else None)
            require(field.get("value") == reference and field.get("load_from_db") is True if reference
                    else field.get("value") in ("", None), "Validated flow contains an inline or unexpected credential")
    agent = nodes["Agent"]["data"]["node"]["template"]
    require(agent["max_iterations"]["value"] == 4 and agent["n_messages"]["value"] == 0
            and all(agent[name]["value"] is False for name in ("add_calculator_tool", "add_current_date_tool", "handle_parsing_errors")),
            "Validated Agent budgets or history/tool settings changed")
    base = read_json(root / "jeju-tour-agent-vertex.flow.json")
    base_code = next(node for node in base["data"]["nodes"] if node["data"]["type"] == "Agent")["data"]["node"]["template"]["code"]["value"]
    hook = "        result = await self._run_agent_with_model_remediation(_run_once)\n        self._agent_result = result"
    replacement = "        result = await _guarded_agent_run(self, _run_once)\n        self._agent_result = result"
    require(base_code.count(hook) == 1 and agent["code"]["value"] == base_code.replace(hook, replacement)
            + "\n\n" + (root / "guarded_vertex_agent.py").read_text(encoding="utf-8"),
            "Embedded Agent differs from reviewed baseline plus the tested execution guard")
    component_source = (root / "validation_components.py").read_text(encoding="utf-8")
    lines = component_source.splitlines(keepends=True)
    parsed = ast.parse(component_source)
    for kind in ("JejuPrepareRequest", "JejuFinalValidation"):
        removed = {line for node in parsed.body if isinstance(node, ast.ClassDef) and node.name != kind
                   for line in range(node.lineno - 1, node.end_lineno)}
        expected = "".join(line for index, line in enumerate(lines) if index not in removed)
        require(nodes[kind]["data"]["node"]["template"]["code"]["value"] == expected,
                "Embedded validation component differs from the tested source")
    mcp = nodes["MCPTools"]["data"]["node"]["template"]
    require(mcp["mcp_server"]["value"] == {"name": "jeju-mcp-v2", "config": {}}
            and mcp["headers"]["value"] == {} and mcp["tool_execution_timeout"]["value"] == 10,
            "Validated flow must use the separately registered v2 MCP with bounded tool calls")


BASELINE_METRICS_SHAPE = {
    "items": "count", "actionAccuracy": "ratio",
    "disallowedIds": {"returnedIds": "count", "rate": "nullable_ratio", "itemsWithViolation": "count",
                      "byReason": {key: "count" for key in ("notInCatalog", "outsideCandidates", "userExcluded", "routeForMinimalWalk", "verifiedFail")}},
    "hardRequirementMisjudgement": {**{key: "count" for key in ("applicableItems", "passOnUnknownItems", "failOnUnknownItems", "contradictsVerifiedItems")}, "rate": "nullable_ratio"},
    "recallAtK": {"k": "count", "scoredItems": "count", "mean": "nullable_ratio"},
    "precisionAtK": {"k": "count", "scoredItems": "count", "mean": "nullable_ratio"},
    "personaThemeMatch": {"scoredItems": "count", "mean": "nullable_ratio"},
    "confirmationNotice": {"requiredItems": "count", "given": "count", "rate": "nullable_ratio", "topicCoverage": {"scoredItems": "count", "mean": "nullable_ratio"}},
    "latencyMs": {"measuredItems": "count", "p50": "nullable_measurement", "p95": "nullable_measurement"},
    "costUsd": {"measuredItems": "count", "total": "nullable_measurement"},
}
BASELINE_GROUP_SHAPE = {
    "system": ("gemini", "langflow"), "variant": ("template", "written"),
    **{key: "count" for key in ("plannedCases", "successfulResponses", "failedCases")},
    "completionRate": "ratio", "actionAccuracyAllCases": "ratio",
    "errors": {key: "count" for key in ("timeout", "transport_error", "invalid_output", "not_executed")},
    "unexpectedImpossible": {"count": "count", "applicableCases": "count", "measuredCases": "count", "rate": "nullable_ratio"},
    "metrics": "metrics",
}
BASELINE_EXTERNAL_SHAPE = {
    "schemaVersion": ("1.0",), "kind": ("jeju-baseline-summary",), "mode": ("external",),
    "resultType": ("imported_responses_not_independently_verified_inference",), "system": ("gemini", "langflow"),
    **{key: "hash" for key in ("planSha256", "provenanceSha256", "runSha256")},
    "binding": {**{key: "hash" for key in ("goldSha256", "specSha256", "catalogSha256", "personasSha256", "themeMapSha256")}, "queriesSha256": "nullable_hash"},
    "startedAt": "instant", "completedAt": "instant",
    "wording": {key: "count" for key in ("templateItems", "writtenItems", "identicalWrittenItems", "distinctWrittenItems")},
    "groups": "groups", "securityEvaluation": ("not_executed",),
    "annotationReview": {**{key: "count" for key in ("structured", "humanReviewed", "modelReviewed", "unannotated", "noResponse")},
                         "status": ("provisional", "no_model_annotations"), "humanSpotCheck": ("pending", "not_applicable")},
}


def check_public_baseline_shape(value, shape):
    """Exact recursive keys and scalar types; arbitrary text is forbidden even under otherwise familiar keys."""
    if isinstance(shape, dict):
        require(isinstance(value, dict) and set(value) == set(shape), "External aggregate has unexpected or missing fields")
        for key, child in shape.items():
            check_public_baseline_shape(value[key], child)
    elif isinstance(shape, tuple):
        require(isinstance(value, str) and value in shape, "External aggregate contains an unknown string label")
    elif shape == "groups":
        require(isinstance(value, list) and 1 <= len(value) <= 2, "External aggregate needs one or two query-variant groups")
        for group in value:
            check_public_baseline_shape(group, BASELINE_GROUP_SHAPE)
    elif shape == "metrics":
        if value is not None:
            check_public_baseline_shape(value, BASELINE_METRICS_SHAPE)
    elif shape == "instant":
        require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z", value), "External aggregate timestamp is invalid")
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif shape in {"hash", "nullable_hash"}:
        require((value is None and shape == "nullable_hash") or (isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)),
                "External aggregate hash is invalid or contains free text")
    elif shape == "count":
        require(isinstance(value, int) and not isinstance(value, bool) and value >= 0, "External aggregate count is invalid")
    else:
        require(shape in {"ratio", "nullable_ratio", "nullable_measurement"}, "Unknown public aggregate schema rule")
        require((value is None and shape.startswith("nullable_")) or finite(value, 0, 1 if "ratio" in shape else None),
                "External aggregate measurement is invalid or contains free text")


def validate_external_baseline_summary(report, system, evaluation, recall_k):
    check_public_baseline_shape(report, BASELINE_EXTERNAL_SHAPE)
    require(report["system"] == system and report["binding"]["goldSha256"] == evaluation["contentSha256"]["gold"]
            and report["binding"]["specSha256"] == evaluation["source"]["specSha256"], "External baseline is bound to another system or evaluation specification")
    require(datetime.fromisoformat(report["startedAt"].replace("Z", "+00:00")) <= datetime.fromisoformat(report["completedAt"].replace("Z", "+00:00")),
            "External baseline timestamps are reversed")
    wording = report["wording"]
    items = evaluation["totals"]["items"]
    require(wording["templateItems"] == items and wording["writtenItems"] <= items
            and wording["identicalWrittenItems"] + wording["distinctWrittenItems"] == wording["writtenItems"]
            and (report["binding"]["queriesSha256"] is None) == (wording["writtenItems"] == 0), "External query wording counts/hashes are inconsistent")
    groups = report["groups"]
    require(len({group["variant"] for group in groups}) == len(groups), "External baseline repeats a query-variant group")
    ratio_is = lambda value, numerator, denominator: (value is None if denominator == 0 else value is not None and math.isclose(value, numerator / denominator, abs_tol=1e-12))
    for group in groups:
        successful = group["successfulResponses"]
        require(group["system"] == system and group["plannedCases"] == items and successful + group["failedCases"] == items
                and sum(group["errors"].values()) == group["failedCases"] and ratio_is(group["completionRate"], successful, items)
                and (group["variant"] != "written" or wording["writtenItems"] == items), "External baseline coverage or failure counts are inconsistent")
        refusal = group["unexpectedImpossible"]
        require(refusal["count"] <= refusal["measuredCases"] <= min(successful, refusal["applicableCases"]) <= items
                and ratio_is(refusal["rate"], refusal["count"], refusal["measuredCases"]), "External noncompletion metric denominator is inconsistent")
        metrics = group["metrics"]
        require((metrics is None) == (successful == 0), "Missing responses cannot have fabricated model metrics")
        if metrics is None:
            require(group["actionAccuracyAllCases"] == 0, "No-response baseline cannot claim action accuracy")
            continue
        require(metrics["items"] == successful and math.isclose(group["actionAccuracyAllCases"], metrics["actionAccuracy"] * successful / items, abs_tol=1e-12),
                "External response-only and all-case accuracy disagree")
        ids = metrics["disallowedIds"]
        require(ids["itemsWithViolation"] <= successful and ids["itemsWithViolation"] <= sum(ids["byReason"].values()) <= ids["returnedIds"]
                and ratio_is(ids["rate"], sum(ids["byReason"].values()), ids["returnedIds"]), "External ID violation metric is inconsistent")
        hard = metrics["hardRequirementMisjudgement"]
        require(hard["applicableItems"] <= successful and all(hard[key] <= hard["applicableItems"] for key in ("passOnUnknownItems", "failOnUnknownItems", "contradictsVerifiedItems"))
                and (hard["rate"] is None) == (hard["applicableItems"] == 0), "External requirement metrics are inconsistent")
        for key in ("recallAtK", "precisionAtK", "personaThemeMatch"):
            metric = metrics[key]
            require(metric["scoredItems"] <= successful and (metric["mean"] is None) == (metric["scoredItems"] == 0)
                    and (key == "personaThemeMatch" or metric["k"] == recall_k), "External ranking metric denominator or k is inconsistent")
        notice = metrics["confirmationNotice"]
        require(notice["given"] <= notice["requiredItems"] <= successful and ratio_is(notice["rate"], notice["given"], notice["requiredItems"])
                and notice["topicCoverage"]["scoredItems"] <= notice["requiredItems"]
                and (notice["topicCoverage"]["mean"] is None) == (notice["topicCoverage"]["scoredItems"] == 0), "External notice metric is inconsistent")
        for key, measured in (("latencyMs", ("p50", "p95")), ("costUsd", ("total",))):
            metric = metrics[key]
            require(metric["measuredItems"] <= successful and all((metric[field] is None) == (metric["measuredItems"] == 0) for field in measured),
                    "Unmeasured external latency/cost must remain null")
        require(metrics["latencyMs"]["p50"] is None or metrics["latencyMs"]["p50"] <= metrics["latencyMs"]["p95"], "External latency percentiles are reversed")
    review = report["annotationReview"]
    require(sum(review[key] for key in ("structured", "humanReviewed", "modelReviewed", "unannotated")) == sum(group["successfulResponses"] for group in groups)
            and review["noResponse"] == sum(group["failedCases"] for group in groups), "External annotation counts confuse responses with unexecuted/failed cases")
    require((review["status"], review["humanSpotCheck"]) == (("provisional", "pending") if review["modelReviewed"] else ("no_model_annotations", "not_applicable")),
            "Model annotations must remain provisional with a pending human spot-check")


def check_external_baseline_summaries():
    """Validate optional live reports when present; partial runs remain explicit in the full evaluation denominator."""
    evaluation = read_json(ROOT / "reports/evaluation-set-summary.json")
    spec = read_json(ROOT / "data/evaluation-spec.json")
    reports = []
    for system in ("gemini", "langflow"):
        path = ROOT / f"reports/baseline-{system}-live-20261119.json"
        if not path.exists():
            continue
        require(path.stat().st_size <= 128 * 1024, "External aggregate is unexpectedly large; raw responses do not belong in a report")
        report = read_json(path)
        validate_external_baseline_summary(report, system, evaluation, spec["metrics"]["recallK"])
        reports.append(report)
    if len(reports) == 2:
        require(reports[0]["planSha256"] == reports[1]["planSha256"] and reports[0]["binding"] == reports[1]["binding"],
                "Published provider comparisons must use the same evaluation plan and data binding")


def main():
    checks = [
        ("JSON syntax", lambda: [read_json(path) for path in sorted((ROOT / "data").glob("*.json"))]),
        ("document links", check_links), ("55-file metadata consistency", check_inventory),
        ("persona definitions and exported metadata", check_personas),
        ("model registry references and status structure", check_model_registry),
        ("agent theme map and aggregate coverage report", check_agent_data),
        ("evaluation spec and aggregate evaluation set summary", check_evaluation_set),
        ("baseline harness self-check and publication boundary", check_baseline_summary),
        ("Langflow starter flow and settings example", check_langflow),
        ("reviewed Vertex flow and credential references", check_vertex_flow),
        ("validated Vertex graph, credentials and tested source parity", check_validated_vertex_flow),
        ("external baseline aggregates when present", check_external_baseline_summaries),
        ("publication boundaries and archives", check_deliverables),
    ]
    failed = False
    for label, check in checks:
        try:
            check()
            print(f"PASS: {label}")
        except CheckError as error:
            failed = True
            print(f"FAIL: {label}: {error}", file=sys.stderr)
        except (OSError, ValueError, KeyError, TypeError, IndexError, SyntaxError, BadZipFile, ET.ParseError, subprocess.SubprocessError):
            failed = True
            print(f"FAIL: {label}: missing, malformed or inconsistent artifact; no file contents printed", file=sys.stderr)
    print("Offline checks only; raw source CSVs, network services and optional local ZIP were not accessed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
