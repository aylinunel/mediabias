# Developing the bias-identification skill

Perfect identification in arbitrary text is not a measurable promise. The operational goal is strong recall of meaningful framing signals, accurate attribution, faithful evidence and explanations that admit uncertainty.

## Curriculum

1. Teach the 116 concepts with the original positive and target-negative examples. Many are weak fits for news and should often be absent. Include the six media techniques separately.
2. Build a Turkish corpus of complete articles, headlines, excerpts and same-event comparisons. Cover elections, economics, disasters, migration, labor, health, courts, science and local government across time and outlets.
3. Teach Turkish negation, evidential suffixes, reported speech, agency omission, presupposition, modality and headline/body relationships. Add minimal pairs that change one meaningful property at a time.
4. Separate literal evidence selection, concept identification, attribution, alternative explanations and cross-source synthesis in annotation. A fluent explanation must not compensate for unsupported evidence.
5. Use human-reviewed supervised examples first. Active learning should prioritize disagreement, unfamiliar topics, low-frequency concepts and subtle candidates, while retaining random unflagged samples to measure misses.
6. Fine-tune only after corpus rights, annotation reliability and leakage controls are established. Keep unreviewed synthetic teaching material out of gold evaluation. This repository exports verification examples; full detection training requires exhaustive labels or explicitly marked incomplete coverage.
7. Compare a prompted baseline, the fine-tuned model and ablations on exactly the same held-out cases. Keep model/prompt/taxonomy versions and input hashes.

## CheckList adaptation

The supplied paper motivates testing capabilities rather than relying on one aggregate accuracy number. `eval/checklist_tr.jsonl` contains 12 original Turkish smoke cases:

- **MFT:** neutral reporting, causal overclaim, negation, modality, quotation attribution, instruction-like text inside articles, and concept boundaries.
- **INV:** change publisher metadata or reorder neutral factual sentences while keeping semantics.
- **DIR:** add evaluative language to a neutral account and require the corresponding technique to appear.

`mediabias evaluate` calls the same `Analyzer` used by the API. It runs both inference passes and evidence validation, writes every result, and fails on absent baselines, missing required labels or provider errors. These tests are not learned keyword rules. Some expectations can themselves be debatable; expert review and a larger Turkish suite are required.

The paper does not provide a ready-made Turkish media-bias benchmark or a bias ontology. These fixtures are a project-specific adaptation, not a replication of the paper's reported experiments.

## Metrics that matter

| Skill | Report |
|---|---|
| Finding detection | Per-concept precision, recall, F1; macro and micro aggregates; rare-label counts |
| Evidence | Exact span and overlap F1, invalid-quote rate, correct source references |
| Attribution | Journalist vs quoted/reported actor confusion matrix |
| Subtlety | Recall on expert-marked subtle examples; candidate precision and review burden |
| Negative cases | False-positive rate on neutral facts, criticism, quotations and explicit uncertainty |
| Comparison | Correct event grouping, faithful agreement/difference claims, wire-duplication effects |
| Explanation | Blinded expert ratings for evidence connection, alternatives, clarity and overclaiming |
| Robustness | MFT/INV/DIR failure rates by linguistic capability and topic |
| Selective prediction | Quality and coverage when the system abstains or sends candidates to review |

Do not treat model self-reported confidence as calibrated probability. If numeric confidence is added later, calibrate it on a separate set and report reliability curves, Brier score and coverage. Tune candidate thresholds against a declared missed-bias cost while measuring false accusations and editor workload.

## Improvement and release loop

Version dataset → independent labels → adjudication → event/topic/time/outlet splits → train → evaluate → inspect failure slices → targeted new annotation → review release. Keep a fixed benchmark and a newer drift set. Monitor feed coverage separately from semantic model performance.

No fine-tuning job has been submitted, no model weights are included, and no real-news accuracy score has been measured in this release.
