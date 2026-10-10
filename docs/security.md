# Security

Scope: Phases 1–4. Items marked *planned* are tracked in the [implementation plan](implementation-plan.md).

## Authentication and sessions

- Passwords are hashed with **Argon2id** (`argon2-cffi` defaults) and must be at least 12 characters. Hashes
  are upgraded on login when parameters change.
- Sign-in sets a signed **HS256 JWT** in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` when
  `APP_ENV=production`). Lifetime is `SESSION_TTL_MINUTES` (default 8 h). Tokens are checked for issuer,
  audience, expiry and algorithm (`alg=none` is rejected).
- The user's **role and active status are read from PostgreSQL on every request**, so disabling a user or
  changing a role applies immediately.
- Failed logins return one generic message for unknown emails, wrong passwords and disabled accounts. Unknown
  emails are verified against a dummy hash to equalise timing. A throttle allows 5 failures per (IP, email)
  per 5 minutes.
- Login success and failure, logout and CLI user creation are written to `audit_events`, along with request IDs.
- *Planned:* server-side session revocation, admin user management UI, dataset-level grants, and a shared
  (Redis) throttle for multiple API processes.

## Authorization

| Endpoint group | admin | analyst | viewer |
|---|---|---|---|
| `/analytics/*`, `/datasets/*` (incl. schema, quality) | ✓ | ✓ | ✓ |
| `/explorer/*`, `POST /exports` (published rows only, bounded) | ✓ | ✓ | ✓ |
| `GET /reports`, report detail, preview, download (reports the caller may see) | ✓ all | ✓ own + shared | ✓ shared |
| `POST /reports`, `…/generate` | ✓ | ✓ (generate: any report they may see) | ✗ (403) |
| `PATCH`, `DELETE /reports/{id}` | ✓ | ✓ own | ✗ (403) |
| `GET /ingestion-jobs`, `/processing-runs/*/logs`, `/data-sources` | ✓ | ✓ | ✗ (403) |
| `POST /ingestion-jobs`, `…/retry`, `…/cancel` | ✓ | ✗ (403) | ✗ (403) |

Job actions are audited (`job.queued`, `job.retried`, `job.cancel_requested`), and so is every extract
(`export.csv`, with the filters, scope, matching and exported row counts). The web UI hides actions a role
cannot take, but the API is the enforcement point.

Checks run in backend dependencies, never only in the UI. In Phase 1 every role can read every published
dataset; per-dataset grants are planned for Phase 6.

## Query safety

- No endpoint accepts SQL, column names or expressions. Filters are a strict Pydantic model: unknown
  parameters return 422, and IDs, hours and payment types are range-checked integers.
- Metric expressions are constants in `analytics/metrics.py`. Filter values are **typed server-side
  ClickHouse parameters** (`{name:Type}`), never interpolated. A unit test asserts that generated SQL
  contains only allowlisted identifiers.
- The only SQL interpolation is of identifiers (database and table names), and those come from
  configuration and pass `validate_identifier`.
- Queries can only reach published months. Hourly series are limited to 62 days, other ranges to
  `ANALYTICS_MAX_RANGE_DAYS`, and series to 5,000 points.
- Explorer sort columns, extract columns and quality flags are closed lists (`Literal` types), checked against
  the query builder's allowlists by a unit test. The row preview allows page sizes 25/50/100 and stops at the
  first 10,000 rows; extracts stop at `MAX_EXPORT_ROWS` (default 100,000, at most 1,000,000) and are streamed.

## Reports

- A report the caller may not see (another user's private report) answers **404** on every route, so its
  existence is not disclosed. Role checks run first: viewers get 403 on create, edit, generate and delete.
- Report creation, edits (with the changed fields), generation requests (with format and filters), deletions and
  every download are audited with the request ID and client IP.
- Runs snapshot the definition at request time; a file always reflects what was requested, never a later edit.
- Files are stored in the lake with their sha256 next to the `document.json` they were rendered from. Downloads
  go through the API only (the lake is not reachable from the browser), are integrity-checked against the stored
  sha256 and are refused if the object is missing or altered. File names are built from the template id and
  dates, never from user input.
- The report worker runs with the API's credentials: the read-only, bounded ClickHouse user and the app's lake
  identity. It has no writer or admin secrets and no JVM.
- Report text from data (zone names, labels) is XML-escaped before ReportLab's markup parser sees it; tooltips in
  the preview are HTML-escaped like every other chart.
- The bundled Geist fonts are SIL Open Font License 1.1 (licence shipped next to them).

## Exports

CSV extracts and report CSVs use standard quoting. Text cells that a spreadsheet would evaluate (starting with
`=`, `+`, `-`, `@`, tab or carriage return) get a leading apostrophe; numbers, dates and booleans are written as
values, so negative amounts stay numeric. XLSX workbooks are written with `strings_to_formulas`,
`strings_to_urls` and `strings_to_numbers` off and every text value through `write_string`, so text is always a
plain string cell that spreadsheet software never evaluates (unit-tested with a `=HYPERLINK(…)` zone name). XLSX
extracts are built in a private temporary directory that is removed after the response is sent. The response states how many rows matched and how many were exported
(`X-Total-Rows`, `X-Exported-Rows`, `X-Truncated`) so a truncated extract is never mistaken for a complete one.

## Least privilege

| Principal | Can | Cannot |
|---|---|---|
| ClickHouse `tripscope_reader` (API, report worker, future AI tools) | `SELECT` on TripScope databases | write, DDL, `url()`, `s3()`, `system.users`, raise its own limits. Server profile: `readonly=2`, `max_execution_time ≤ 30 s`, `max_result_rows ≤ 100k` (throw), `max_memory_usage ≤ 2 GB`, `max_threads ≤ 8` |
| ClickHouse `tripscope_writer` (pipeline) | DDL/DML on TripScope databases, read the lake via the named collection | read `system.parts`, manage users |
| Lake identity `tripscope_app` | read/write TripScope buckets | other buckets, admin |
| Lake identity `tripscope_clickhouse` | read/list TripScope buckets | write |

Lake credentials for ClickHouse live in server config (named collection with `from_env`), never in query
text. The API container gets only the reader credentials and the app's lake identity (for readiness).
The writer and ClickHouse admin credentials are never given to it. Integration tests verify the reader and
lake-identity restrictions.

## Jobs and logs

Only manifest source keys (validated pattern) can be queued; the API never accepts URLs or paths from clients.
A partial unique index allows one queued-or-running job per source, so concurrent requests cannot start duplicate
runs (409). Run logs keep only the run's own TripScope records, pass through `redact()`, and are capped at 500
entries; they are visible to admins and analysts.

## Untrusted input files

CSV sources are checked line by line before Spark: a missing or duplicate header, a row with the wrong number
of fields, invalid UTF-8, or a trailing server-error body (a truncated export) rejects the file. Source files are
treated as data only. They come from HTTPS hosts on the manifest allowlist, or from local
paths confined to the repository directory, and are size-capped and checksum-verified. Their Parquet
structure is validated, and only pyarrow/Spark readers touch them. Nothing in a file is executed or
interpreted as an instruction. Raw objects are write-once: a different checksum for an existing key fails the
run instead of overwriting the file.

The taxi-zone shapefile archive is handled the same way: pinned checksum and size cap, then only the expected
`.shp`, `.shx`, `.dbf` and `.prj` members are read (each capped at 25 MB; paths inside the archive are never
used to write files). The projection must be the documented NY State Plane Long Island system and every zone
must fall inside New York City's bounding box, or the build fails. The API serves the stored GeoJSON only after
it parses as JSON, and the map draws it with ECharts, never as HTML.

## Secrets

- `.env` is git-ignored and generated by `make env` with random values. Placeholder values (`replace-me`)
  and short app secrets are refused at startup.
- Settings hold secrets as `SecretStr`. Failure summaries stored in PostgreSQL and logs are passed through
  `redact()` with every configured secret.
- API errors return a code, a message and a request ID. Stack traces and SQL stay in server logs.

## HTTP hardening

API responses carry `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
`Cache-Control: no-store` and a request ID. The Next.js server adds the same headers plus a per-request
**nonce-based Content-Security-Policy** from `proxy.ts` (`script-src 'self' 'nonce-…' 'strict-dynamic'`, no
`unsafe-eval` in production, `frame-ancestors 'none'`, `connect-src 'self'`); inline style attributes are allowed
for chart rendering. The post-login redirect only accepts same-site relative paths (no open redirect). The
frontend never uses `dangerouslySetInnerHTML` (lint rule) and chart tooltip text is HTML-escaped. OpenAPI docs are
disabled when `APP_ENV=production`. All service ports bind to loopback.
