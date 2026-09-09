"""Validated review additions, independent decisions, and scoped training export."""

import hashlib

from sqlalchemy import select

from .db import Analysis, Decision, NewsEvent, Review
from .evidence import ground_finding
from .schema import Finding


def validate_review(body, analysis):
    existing = {f["id"]: f for f in analysis.payload["findings"]}
    if body.verdict == "add":
        if body.finding_id in existing or not body.replacement:
            raise ValueError("Add requires a new finding ID and complete evidence-grounded finding.")
    elif body.finding_id not in existing:
        raise ValueError("Finding does not belong to this analysis.")
    if body.verdict in {"correct", "add"}:
        if not body.replacement or body.replacement.id != body.finding_id:
            raise ValueError("Replacement must match the reviewed finding ID.")
        ground_finding(body.replacement, {a["id"]: a for a in analysis.snapshots})
    elif body.replacement is not None:
        raise ValueError("Replacement is only valid for correct/add.")
    return body.model_dump()


def split_for(event_id):
    bucket = int(hashlib.sha256(event_id.encode()).hexdigest()[:8], 16) % 10
    return "test" if bucket == 9 else "validation" if bucket == 8 else "train"


def export_rows(session):
    # Latest editor decision per analysis/finding wins; original records remain unchanged.
    rows = session.execute(
        select(Decision, Review, Analysis, NewsEvent)
        .join(Review, Decision.review_id == Review.id)
        .join(Analysis, Review.analysis_id == Analysis.id)
        .join(NewsEvent, Analysis.event_id == NewsEvent.id)
        .order_by(Decision.created_at, Decision.id)
    ).all()
    latest = {}
    for decision, review, analysis, event in rows:
        latest[(analysis.id, review.finding_id)] = (decision, review, analysis, event)
    for decision, review, analysis, event in latest.values():
        payload = review.payload
        if event.is_demo or payload["verdict"] == "uncertain":
            continue
        original = next((f for f in analysis.payload["findings"] if f["id"] == review.finding_id), None)
        finding = payload["replacement"] if payload["verdict"] in {"correct", "add"} else original
        finding = ground_finding(
            Finding.model_validate(finding), {a["id"]: a for a in analysis.snapshots}
        ).model_dump()
        if payload["verdict"] != "reject" and finding["status"] != "supported":
            continue  # tentative hypotheses never become positive gold merely by approval
        yield {
            "schema_version": "1.0",
            "task": "evidence_scoped_finding_verification",
            "event_id": event.id,
            "analysis_id": analysis.id,
            "split": split_for(event.id),
            "input": {"articles": analysis.snapshots, "proposed_finding": original or finding},
            "target": {
                "verdict": payload["verdict"],
                "finding": None if payload["verdict"] == "reject" else finding,
                "rationale_tr": payload["notes"],
            },
            "review": {"reviewer": review.reviewer, "editor": decision.editor, "decision_id": decision.id},
            "provenance": analysis.provenance,
            "rights": "Publisher text: confirm permitted training and redistribution before use.",
        }
