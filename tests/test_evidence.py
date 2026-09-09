from copy import deepcopy

import pytest

from mediabias.evidence import CONCEPTS, validate_comparison


def sample(client):
    a = client.get("/api/events/demo-event").json()["analysis"]
    return deepcopy(a["payload"]), deepcopy(a["snapshots"])


def test_demo_quotes_are_grounded(client):
    payload, snapshots = sample(client)
    result = validate_comparison(payload, snapshots)
    by_id = {a["id"]: a for a in snapshots}
    for f in result.findings:
        for e in f.evidence:
            assert by_id[e.article_id]["text"][e.start : e.end] == e.quote


@pytest.mark.parametrize(
    "mutation", ["quote", "article", "concept", "layer", "duplicate", "span", "difference"]
)
def test_invalid_evidence_rejected(client, mutation):
    p, s = sample(client)
    if mutation == "quote":
        p["findings"][0]["evidence"][0]["quote"] = "Bu söz hiçbir kaynakta yok."
    elif mutation == "article":
        p["findings"][0]["evidence"][0]["article_id"] = "unknown"
    elif mutation == "concept":
        p["findings"][0]["concept_id"] = "invented_bias"
    elif mutation == "layer":
        p["findings"][0]["layer"] = "cognitive_concept"
    elif mutation == "duplicate":
        p["findings"][1]["id"] = p["findings"][0]["id"]
    elif mutation == "span":
        p["findings"][0]["evidence"][0]["start"] = 500
    else:
        p["differences"][0]["finding_ids"] = ["not-present"]
    with pytest.raises(ValueError):
        validate_comparison(p, s)


def test_agreement_requires_different_outlets(client):
    p, s = sample(client)
    for a in s:
        a["source_id"] = "same-agency"
    with pytest.raises(ValueError, match="two distinct"):
        validate_comparison(p, s)


@pytest.mark.parametrize("concept", ["contextual_omission", "one_sided_sourcing", "selective_statistics"])
def test_snippet_cannot_prove_omission(client, concept):
    p, s = sample(client)
    p["findings"][0]["concept_id"] = concept
    for a in s:
        a["content_scope"] = "feed_excerpt"
    r = validate_comparison(p, s)
    assert r.findings[0].status == "candidate"
    assert r.findings[0].missing_context_tr


def test_no_automatic_journalist_attribution(client):
    p, s = sample(client)
    p["findings"][0]["attribution"] = "quoted_speaker"
    assert validate_comparison(p, s).findings[0].attribution == "quoted_speaker"


def test_taxonomy_and_teaching_examples_complete():
    assert len(CONCEPTS) == 122
    assert sum(e["layer"] == "cognitive_concept" for e in CONCEPTS.values()) == 116
    assert all(e["positive_example"] and e["target_negative_example"] for e in CONCEPTS.values())
