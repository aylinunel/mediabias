# Validation status for the initial implementation

## Executed locally

| Check | Result |
|---|---|
| Python test suite | 47 passed on Python 3.12 |
| Ruff lint and format | Passed |
| JavaScript syntax (`node --check`) | Passed |
| Source/data contract | 53 outlets, 118 feeds, 71 enabled feeds across 26 outlets, 122 concepts, 244 synthetic teaching examples |
| Alembic upgrade | Initial schema created; repeated upgrade is idempotent in SQLite |
| Package installation | Editable installation and CLI entry point verified |
| Wheel build | Built successfully; packaged interface, taxonomy and migrations verified |
| Dependency consistency | `pip check` reports no broken requirements |
| Demo HTTP integration | Health, event data, evidence payload and HTML served successfully through FastAPI TestClient |

The tests cover invented quotes, unknown concepts, invalid spans, cross-source evidence requirements, excerpt omission gates, attribution preservation, live access roles, cross-origin writes, request size limits, review/correction/addition, independent editorial decisions, scoped export, immutable analysis history, updated-content cache invalidation, feed dates, URL normalization, unsafe DNS/redirects, HTTP 304, behavioral-runner failures, and mocked two-pass model validation.

Two upstream deprecation warnings occur in the installed Starlette test-client dependencies. They do not fail the suite. `requirements.lock` records the environment's dependency versions as constraints; production dependencies and development extras remain declared in `pyproject.toml`.

## Attempted, not verified

A read-only feed pass attempted the 71 enabled endpoints using the collector's production network policy. All 71 failed with DNS resolution errors (`gaierror`) in this environment, before an HTTP response. Zero articles were collected. This result says nothing about publisher availability, access restrictions, trust or bias. Run a fresh check from the intended deployment environment.

## Not executed

- Live LLM inference, paid fine-tuning, or model accuracy evaluation: no configured model credentials.
- Expert adjudication of the teaching examples or Turkish behavioral fixtures.
- End-to-end browser automation or a browser screenshot review; UI validation here consists of source/syntax and HTTP integration checks.
- PostgreSQL integration, load testing, failover, production hosting, or private correction-queue integration.
- Docker container startup.

GitHub Actions is configured for Python 3.11 and 3.12. A workflow definition is not evidence of a completed remote run; inspect the repository’s Actions tab for actual status.

No semantic accuracy percentage or perfect-detection claim follows from these software tests.
