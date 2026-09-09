# Compatibility with the supplied editorial repositories

The three user-provided repositories were reviewed through authenticated GitHub access. They are private. This public repository contains newly written code and general interoperability notes, not copied private operational documents or private source code.

| Reference | Review scope | Design consequence here |
|---|---|---|
| `aylinunel/turkish-news-evidence-operations-kit` | Feed eligibility result contract | Preserve dated retrieval outcomes and separate technical eligibility from editorial judgments |
| `aylinunel/turkish-news-correction-queue-tests` | Current correction-review API contract | Keep a submitted correction separate from changing the underlying record; test role boundaries and state effects |
| `aylinunel/editorial-operations-skills` | Correction-case contract and feed request policy | Use authenticated reviewer/editor identities, independent review, append-only history and ordinary public HTTPS feed requests |

Reference snapshots reviewed: evidence kit `b48e97d86c816f8d18eb95cb78a8e3417fd90c4f`; correction tests `f685133bd26903ebb84760451942f92b5392db14`; editorial skills `810721072e7836e91c9bfa5bd8ca289e87492bf3`.

## Integration boundary

This application exposes FastAPI JSON routes under `/api`, with a local annotation/decision model. It is **not a drop-in client or server for the existing tRPC/SuperJSON correction queue**. No private database migrations are applied. The reference queue's implemented routes and proposed normalized future schema must not be conflated.

A future adapter should explicitly map event/article/analysis IDs, evidence snapshots, actor identities, lifecycle outcomes and superseded versions. Use each system's authenticated actor context. A successful queue disposition must not be presented as having updated the challenged article. Public correction notices require their own editorial publication gate and privacy filtering.

Before enabling an adapter, test unauthorized requests, reviewer/editor boundaries, missing targets, duplicate submissions, invalid transitions, independent resolution and private-data suppression against a disposable staging database. No integration credentials or private contact data belong in this repository.
