# Security and data handling

Use this initial release as a private research workspace. Live mode requires configured personal tokens; reviewer and editor permissions are enforced server-side. Do not use one shared identity to simulate independent approval. Keep tokens and model keys outside Git, and terminate TLS before accepting external connections. API documentation is public but contains schemas, not records.

Article text is untrusted data. The inference prompt explicitly rejects embedded instructions, output is schema-validated, and evidence quotes must exist in stored snapshots. These controls reduce risk but cannot prove freedom from prompt injection or semantic misinterpretation. A human remains responsible for publication decisions.

The collector requests only configured public HTTPS feeds, rejects private DNS results, validates redirects and limits response sizes. Runtime DNS checking is not a complete defense against DNS rebinding: use network-level egress rules to deny internal/reserved destinations in a deployed environment. Register edits and model endpoint configuration are trusted operator actions. Model endpoints receive supplied article excerpts; assess provider data handling before enabling them.

The UI escapes article/model strings, uses a restrictive Content Security Policy, stores tokens only in memory and rejects cross-origin writes. Request bodies are capped at 3 MB. Add reverse-proxy rate limits and request/time budgets for any internet-facing deployment. Built-in SSO, per-user rate limiting, account recovery, encryption at rest, deletion/retention automation and public correction intake are not implemented.

Back up the database, minimize personal data in news snapshots and define your retention policy. Exports contain source text and internal reviewer identities and must remain private. Publisher text is not relicensed by the repository’s code license.

For a suspected vulnerability, contact the repository owner privately; do not include secrets or private article/contact data in a public issue.
