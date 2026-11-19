"""Stable task identity is independent of wording, sample and parameter values."""
from __future__ import annotations
import unicodedata
from .io_utils import digest


def norm(value):
    return unicodedata.normalize("NFKC", str(value)).strip().casefold()


def identity(spec):
    return {
        "mechanism": norm(spec["core_mechanism"]),
        "object": norm(spec["decision_object"]),
        "decisions": sorted({norm(x) for x in spec["required_decisions"]}),
        "dependencies": sorted({tuple(sorted(norm(x) for x in edge))
                                for edge in spec["decision_dependencies"]}),
        "maintenance": sorted({norm(x) for x in spec["maintenance_targets"]}),
    }


def fingerprint(spec):
    return digest(identity(spec))


def nearest(spec, records, limit=10):
    """Recall across all families; a score is never an approval decision."""
    base = identity(spec)
    base_tokens = set(base["decisions"] + base["maintenance"] + [base["mechanism"], base["object"]])
    ranked = []
    for record in records:
        other = record.get("spec", record)
        target = identity(other)
        tokens = set(target["decisions"] + target["maintenance"] + [target["mechanism"], target["object"]])
        score = len(tokens & base_tokens) / max(1, len(tokens | base_tokens))
        exact = base == target
        ranked.append({"task_id": record.get("task_id", record.get("id", "unknown")),
                       "score": round(score, 4), "exact_structure": exact,
                       "spec": other, "status": record.get("status", "candidate")})
    return sorted(ranked, key=lambda x: (-x["exact_structure"], -x["score"], x["task_id"]))[:limit]


def structural_check(spec, records):
    neighbors = nearest(spec, records)
    matches = [x["task_id"] for x in neighbors if x["exact_structure"]]
    return {"fingerprint": fingerprint(spec), "classification": "variant" if matches else "needs_semantic_review",
            "exact_matches": matches, "nearest": neighbors,
            "guarantee": "结构指纹识别相同机制；自动接纳保留模型分类，重复和变体不计独立新题"}
