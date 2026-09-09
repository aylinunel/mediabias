# Mercek · Turkish news evidence workspace

[![Checks](https://github.com/aylinunel/mediabias/actions/workflows/ci.yml/badge.svg)](https://github.com/aylinunel/mediabias/actions/workflows/ci.yml)

Compare how Turkish news outlets describe the same event. Read literal evidence behind each finding, review missed or mistaken labels, and export independently approved annotations.

**This is a working research application, not a validated bias detector.** It does not promise perfect identification, infer ideology from a publisher name, or equate agreement with truth. The default screen uses three clearly fictional sources. Live inference requires your own model configuration.

## The five questions

1. **Ne oldu?** What do the supplied reports say happened?
2. **Nerede anlaşıyorlar?** Which claims do sources share?
3. **Nerede ayrışıyorlar?** Where does their account or emphasis differ?
4. **Hangi çerçeveler kullanılıyor?** Which observable techniques or possible cognitive mechanisms frame those differences?
5. **Neden önemli?** How could the differences affect a reader’s interpretation or decision?

The Turkish interface includes source cards, a clickable source-by-technique matrix, exact-quote context, a review desk, feed status, and a searchable concept reference. Supported findings and uncertain candidates have distinct visual markers. No political spectrum or invented confidence percentages are shown.

## Run the demo

Python 3.11 or 3.12:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]' -c requirements.lock
mediabias serve
```

Open **http://127.0.0.1:8000**. The demo needs no model key or news access. Its labels are prewritten illustrations, not model predictions. The UI itself is Turkish; this development documentation and the 116 cognitive-concept reference entries are English.

## Configure live operation

```bash
cp .env.example .env
# Edit .env: enable live mode, add distinct reviewer/editor tokens and model credentials.
mediabias migrate
mediabias serve
```

Generate each personal token with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. The server derives identity and role from the token; a request cannot supply its own reviewer identity. In the interface, select **Giriş** and enter your token. Use HTTPS and a private network/reverse proxy when serving beyond localhost.

The model adapter uses the HTTPS `/chat/completions` contract with JSON output. Set a model/provider supporting that contract and enough context for the selected articles. Analysis makes two billable calls: generation and verification. The second pass is not independent evidence, especially when using the same model. No provider calls occur during the automated unit tests.

**Collection:** use the source page’s control in the default `COLLECTOR_MODE=api`. For periodic collection, set `MEDIABIAS_COLLECTOR_MODE=worker`, restart the API, and run:

```bash
mediabias collect             # continuously, every POLL_SECONDS after each run
mediabias collect --once      # one pass instead
```

Run exactly **one collector**, and one API process in this release. Worker mode disables the API collection button’s operation to avoid competing collectors. Both use the same database. Collection does not automatically incur inference charges; open a candidate event and request analysis.

## Your source register

The supplied workbook contains **53 outlets / 118 feed records**. It marks **71 feeds from 26 outlets** as live; all 53 remain in the register. The supplied observations were produced on **8 September 2026**, and are not current availability guarantees. Set `MEDIABIAS_ACTIVE_SOURCE_IDS` to an explicit list if you want exactly 25 outlets. Nothing silently removes the 26th.

Only provided, enabled HTTPS RSS/Atom endpoints are requested. Political-placement, ownership, traffic and trust columns are excluded from the inference inputs. The importer can rebuild the portable register:

```bash
python scripts/import_sources.py path/to/Turk_Haber_Analizi_Kaynak_Listesi.xlsx src/mediabias/data/sources.json
```

Runtime observations are stored separately from the supplied statuses. The development environment's attempted check of all 71 selected feeds failed at DNS resolution; **live retrieval has not been verified here**. See [validation](docs/validation.md).

## Evidence and annotation

- 116 concepts from [The Decision Lab directory](https://thedecisionlab.com/biases), plus six media techniques: loaded language, selective statistics, one-sided sourcing, emphasis framing, causal overclaim and contextual omission.
- [Full catalog with positive/negative examples](docs/bias-catalog.md); 244 original synthetic teaching records in `src/mediabias/data/teaching_examples.jsonl`. These are **not adjudicated fine-tuning gold**.
- Literal quotations and character offsets are checked against immutable analyzed snapshots. Unknown labels, fabricated quotations and unsupported cross-source references fail validation.
- Feed excerpts cannot establish full-article omission. An absence of findings does not establish neutrality.
- Reviewers can accept, reject, remain uncertain, correct, or add a missed finding. A **different editor** approves the review. Previous analyses and decisions remain stored.
- Export contains only adjudicated non-demo examples. Candidates are excluded from positive gold; uncertain judgments are excluded. Rejections train a scoped negative, not “this whole article has no bias.”

The export task is **finding verification/correction**, not a complete detector training set. Fully annotating entire articles, including missed spans, is still required before training a high-recall detector. See [annotation protocol](docs/annotation.md) and [model development](docs/model-development.md).

## Tests and behavioral evaluation

```bash
pytest -q
ruff check src tests scripts
node --check src/mediabias/static/app.js
```

GitHub Actions runs checks for Python 3.11/3.12, source validation and migration checks on pushes and pull requests. Tests mock the model and do not measure semantic accuracy.

The attached paper, [Beyond Accuracy: Behavioral Testing of NLP Models with CheckList](https://arxiv.org/abs/2005.04118), motivates 12 Turkish synthetic cases covering MFT, INV and DIR behavior. Run the actual two-pass analyzer against them only after configuring a model:

```bash
mediabias evaluate --cases eval/checklist_tr.jsonl --output var/model-evaluation.json
```

This command incurs model usage, saves each result, and fails if any case fails. The fixtures need expert review and expansion; they are not a real-news accuracy benchmark.

## Repository map

```text
src/mediabias/
  api.py, schema.py          HTTP interface and strict data contracts
  model.py, evidence.py      two-pass inference and quote validation
  feeds.py                   bounded feed retrieval and candidate grouping
  db.py, migrations/         SQLite/PostgreSQL persistence and schema migration
  reviews.py                 review, independent approval and scoped export
  static/                    Turkish interface, no frontend build dependency
  data/                      source register, taxonomy, synthetic examples
  evaluation.py              production-path behavioral runner
scripts/                     workbook import and register validation
tests/                      software and evidence contract tests
eval/                       Turkish behavioral fixtures
docs/                       methodology, operations, limitations and integration notes
```

See [architecture and operations](docs/architecture.md), [reference-repository compatibility](docs/integrations.md), and [security](SECURITY.md). This release does not deploy an external site or connect to the private repositories’ databases.
