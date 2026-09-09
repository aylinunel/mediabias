# Turkish news annotation protocol

## Annotation unit

A finding consists of a concept, exact evidence span(s), source attribution, status, Turkish explanation, plausible alternative, missing context and possible reader consequence. Keep the source event and analyzed snapshot version with every label. Do not label an outlet globally based on one article.

`cognitive_concept` covers the 116 directory concepts, including heuristics, memory effects and constructs that are not necessarily errors. `media_technique` covers directly observable presentation practices. A loaded phrase can support a language-technique label without proving that a reader used the affect heuristic. Contextual omission is distinct from omission bias, which concerns judgments about action versus inaction.

## Read and label

1. Establish that the articles concern the same event, location, actors and time. Mark grouping problems before interpreting framing differences.
2. Read the complete available texts, including headline, quotation attribution, negation, modality and temporal scope. Record whether you only have a feed excerpt.
3. State the shared claim and concrete contrast. Cite both sources for an agreement or difference.
4. Select the narrowest supported concept. Copy the shortest sufficient literal span; include a second span where a relation is needed. Offsets use Python Unicode character indices in the exact analyzed text, starting at zero, end exclusive.
5. Attribute the wording: journalist/headline, quoted speaker, reported actor or unclear. Quoting a claim does not imply endorsement.
6. Write a Turkish explanation connecting wording to interpretation. Give a plausible alternative explanation and state absent evidence.
7. Retain subtle possibilities as `candidate` with the evidence needed to resolve them. Do not convert every emotional word, opinion, criticism or unfavorable fact into a bias finding.
8. Add missed findings explicitly. Rejecting one finding only rejects that finding; it does not establish that the whole article is neutral.

Full-article omission, one-sided sourcing and selective-statistics findings require complete untruncated compared texts and cross-source evidence to remain supported under the code’s conservative gate. Even satisfying that gate does not prove intent or guarantee a correct interpretation.

## Review and final decision

A reviewer selects accept, reject, uncertain, correct, or add. Corrections/additions require a complete, revalidated finding. The record stores the authenticated server identity. A different editor approves a chosen review with a reason. The latest decision for an analysis/finding determines export; earlier decisions and analyses remain unchanged.

The original comparison screen continues to show the original model version; the review desk displays later decisions. Approval does not rewrite a public story or create a publisher correction notice. Demo mode uses one local identity; its records cannot enter training gold and it intentionally cannot self-adjudicate.

## Training export

`GET /api/export` requires editor access and returns JSONL. It excludes demo events, uncertain verdicts and candidate positives. Each record is a **scoped verification/correction task** containing input articles, a proposed finding, the approved verdict and corrected finding (or null for rejection). It is not a complete event summary or exhaustive article annotation.

A stable event-level hash assigns 80/10/10 train/validation/test buckets in expectation. Small exports may have uneven or empty buckets. Human review must consolidate duplicate event IDs and wire stories before dataset release; event hashing alone does not prevent semantic leakage. Also create held-out time, topic and outlet slices.

The export includes publisher excerpts and internal reviewer IDs. Keep it private, establish permitted training use, redact personal information, and exclude it from public Git history. Synthetic teaching examples have a separate purpose and must not be mixed into held-out real-news evaluation.

## Adjudicated benchmark requirements

Use at least two trained Turkish-speaking annotators independently before discussion, with a third adjudicator for disagreements. Include negative cases and hard near-misses; do not sample only model-flagged sentences. Measure multi-label agreement, span overlap and attribution agreement. Report prevalence and disagreement by concept; absence of consensus is informative. Proposed annotation instructions and all synthetic examples require expert review before claiming a validated dataset.
