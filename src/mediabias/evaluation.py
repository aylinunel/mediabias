"""CheckList-inspired behavioral checks using the same production analyzer.

Synthetic smoke tests diagnose failures; they do not estimate real-news accuracy.
"""

import hashlib
import json
from collections import Counter

from .evidence import TAXONOMY_HASH
from .model import PROMPT_VERSION, Analyzer


def labels(result):
    return {f["concept_id"] for f in result["findings"]}


def assess(case, result, previous):
    found = labels(result)
    checks = []
    expected = case.get("expect", {})
    for label in expected.get("present", []):
        checks.append((f"present:{label}", label in found))
    for label in expected.get("absent", []):
        checks.append((f"absent:{label}", label not in found))
    for label, actor in expected.get("attribution", {}).items():
        fs = [f for f in result["findings"] if f["concept_id"] == label]
        checks.append((f"attribution:{label}", bool(fs) and all(f["attribution"] == actor for f in fs)))
    for label, status in expected.get("status", {}).items():
        fs = [f for f in result["findings"] if f["concept_id"] == label]
        checks.append((f"status:{label}", bool(fs) and all(f["status"] == status for f in fs)))
    if "same_labels_as" in expected:
        baseline = previous.get(expected["same_labels_as"])
        checks.append(("invariance", baseline is not None and found == labels(baseline)))
    if "added_relative_to" in expected:
        reference = expected["added_relative_to"]
        baseline = previous.get(reference["case"])
        checks.append(("direction", baseline is not None and reference["label"] in found - labels(baseline)))
    if not checks:
        raise ValueError("Every case must define at least one behavioral expectation.")
    return [{"check": name, "passed": passed} for name, passed in checks]


async def evaluate(settings, cases_path, output_path):
    cases = [json.loads(line) for line in cases_path.read_text().splitlines() if line.strip()]
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Cases must be nonempty and have unique IDs.")
    if not settings.llm_api_key or not settings.llm_model:
        raise ValueError("Evaluation needs an explicitly configured live model and incurs provider usage.")
    analyzer, previous, records = Analyzer(settings), {}, []
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        record = {"id": case["id"], "type": case["type"], "capability": case["capability"]}
        try:
            result = await analyzer.analyze(case["articles"], "sensitive")
            checks = assess(case, result, previous)
            previous[case["id"]] = result
            record.update(result=result, checks=checks, passed=all(c["passed"] for c in checks))
        except Exception as error:
            record.update(passed=False, error=type(error).__name__)
        records.append(record)
        report = {
            "kind": "synthetic_behavioral_smoke_tests",
            "not_real_news_accuracy": True,
            "model": settings.llm_model,
            "verifier": settings.verifier_model or settings.llm_model,
            "prompt_version": PROMPT_VERSION,
            "taxonomy_hash": TAXONOMY_HASH,
            "cases_hash": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
            "expected": len(cases),
            "completed": len(records),
            "passed": sum(r["passed"] for r in records),
            "by_type": dict(Counter(r["type"] for r in records if not r["passed"])),
            "records": records,
        }
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not all(r["passed"] for r in records):
        raise RuntimeError("Behavioral evaluation failed; inspect the saved report.")
