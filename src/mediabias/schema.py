from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Strict):
    article_id: str
    quote: str = Field(min_length=4, max_length=1500)
    start: int | None = None
    end: int | None = None


class Claim(Strict):
    text_tr: str = Field(min_length=5, max_length=2000)
    evidence: list[Evidence] = Field(min_length=1, max_length=25)


class Finding(Strict):
    id: str = Field(min_length=1, max_length=80)
    concept_id: str
    layer: Literal["media_technique", "cognitive_concept"]
    status: Literal["supported", "candidate"]
    attribution: Literal["journalist", "quoted_speaker", "reported_actor", "unclear"]
    explanation_tr: str = Field(min_length=10, max_length=3000)
    alternative_explanation_tr: str = Field(min_length=5, max_length=2000)
    missing_context_tr: str = Field(max_length=2000)
    reader_impact_tr: str = Field(min_length=5, max_length=2000)
    evidence: list[Evidence] = Field(min_length=1, max_length=25)


class Difference(Claim):
    finding_ids: list[str] = Field(default_factory=list)


class Comparison(Strict):
    schema_version: Literal["1.0"] = "1.0"
    what_happened: list[Claim] = Field(min_length=1, max_length=8)
    agreements: list[Claim] = Field(max_length=12)
    differences: list[Difference] = Field(max_length=15)
    findings: list[Finding] = Field(max_length=60)
    why_care_tr: str = Field(min_length=10, max_length=4000)
    limitations_tr: list[str] = Field(min_length=1, max_length=20)


class ArticleInput(Strict):
    source_id: str = Field(min_length=1, max_length=100)
    source_name: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=4, max_length=500)
    text: str = Field(min_length=20, max_length=100000)
    url: str = Field(default="", max_length=2000)
    content_scope: Literal["full_text", "feed_excerpt", "provided_excerpt"] = "provided_excerpt"


class EventInput(Strict):
    title: str = Field(min_length=4, max_length=300)
    articles: list[ArticleInput] = Field(min_length=2, max_length=25)


class ReviewInput(Strict):
    finding_id: str = Field(min_length=1, max_length=80)
    verdict: Literal["accept", "reject", "uncertain", "correct", "add"]
    replacement: Finding | None = None
    notes: str = Field(min_length=10, max_length=4000)


class DecisionInput(Strict):
    review_id: str
    notes: str = Field(min_length=10, max_length=4000)


class AnalyzeInput(Strict):
    sensitivity: Literal["sensitive", "balanced"] = "sensitive"
    force: bool = False


class MoveInput(Strict):
    event_id: str
