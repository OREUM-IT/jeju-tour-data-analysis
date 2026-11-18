"""Transparent prototype matching; this module does not fit empirical clusters."""
from __future__ import annotations
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_catalog():
    catalog = json.loads((ROOT / "data/persona-prototypes.json").read_text())
    assert len(catalog["themes"]) == 9
    assert len({p["id"] for p in catalog["prototypes"]}) == len(catalog["prototypes"])
    assert math.isclose(sum(catalog["weights"].values()), 1)
    for p in catalog["prototypes"]:
        assert len(p["themes"]) == 9 and math.isclose(sum(p["themes"]), 1)
        assert all(0 <= value <= 1 for value in p["themes"])
        assert all(0 <= p[s["id"]] <= 1 for s in catalog["scalars"])
    return catalog


def score(profile, catalog=None):
    catalog = catalog or load_catalog()
    raw = profile.get("themes")
    themes = None
    if raw is not None:
        if len(raw) != len(catalog["themes"]) or any(
            not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0 for v in raw
        ):
            raise ValueError("themes must be a finite nonnegative nine-value vector")
        if sum(raw) > 0:
            norm = math.sqrt(sum(v * v for v in raw))
            themes = [v / norm for v in raw]
    observed = ["themes"] if themes is not None else []
    for s in catalog["scalars"]:
        key = s["id"]
        value = profile.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{key} must be null or a number in [0,1]")
            observed.append(key)
    coverage = sum(catalog["weights"][k] for k in observed)
    if not coverage:
        return {"coverage": 0, "status": "insufficient", "ranked": [], "margin": None}
    ranked = []
    for p in catalog["prototypes"]:
        distances = {}
        for key in observed:
            if key == "themes":
                norm = math.sqrt(sum(v * v for v in p["themes"]))
                dist = 1 - sum(u * v / norm for u, v in zip(themes, p["themes"]))
                distances[key] = max(0.0, min(1.0, dist))
            else:
                distances[key] = abs(profile[key] - p[key])
        contributions = {k: catalog["weights"][k] * v / coverage for k, v in distances.items()}
        ranked.append({"id": p["id"], "score": 100 * (1 - sum(contributions.values())), "contributions": contributions})
    ranked.sort(key=lambda x: (-x["score"], x["id"]))
    margin = ranked[0]["score"] - ranked[1]["score"] if len(ranked) > 1 else None
    t = catalog["thresholds"]
    if themes is None or coverage + 1e-10 < t["min_coverage"]:
        status = "insufficient"
    elif ranked[0]["score"] < t["min_score"]:
        status = "exploratory"
    elif margin is not None and margin < t["mixed_margin"]:
        status = "mixed"
    else:
        status = "matched"
    return {"coverage": coverage, "status": status, "ranked": ranked, "margin": margin}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path, help="JSON with themes and optional four scalar preferences; null means unanswered")
    args = parser.parse_args()
    print(json.dumps(score(json.loads(args.profile.read_text())), ensure_ascii=False, indent=2))
