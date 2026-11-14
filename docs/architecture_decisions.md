# CredSure: Architecture Decision Records

Each record is short: context, decision, consequences and the alternatives rejected. All
records have status **Accepted** (2026-11-14) unless noted.

ADR-017 to ADR-024 record the pre-T02 decisions D1–D8, which follow the
[schema review](schema_review.md). Where they change an earlier ADR, that ADR carries an
**Amended by** note, and the newer ADR takes precedence.

| Decision | ADR | Amends |
|----------|-----|--------|
| D1 Final database constraints; atomic, irreversible revocation | [ADR-017](#adr-017-final-database-constraint-set-and-atomic-irreversible-revocation) | ADR-008 |
| D2 Whole-second UTC timestamps, one formatter and parser | [ADR-018](#adr-018-whole-second-utc-timestamps-with-one-shared-formatter-and-parser) | ADR-014 |
| D3 SQLite transaction configuration | [ADR-019](#adr-019-sqlite-transaction-configuration) | ADR-002 |
| D4 Linear audit chain | [ADR-020](#adr-020-database-enforced-linear-audit-chain) | ADR-009 |
| D5 Rule effective dates anchored to completion time | [ADR-021](#adr-021-rule-effective-dates-anchored-to-completion-time) | ADR-007 |
| D6 Fixed-point score strings in signed payloads | [ADR-022](#adr-022-fixed-point-decimal-strings-in-signed-payloads) | ADR-004 |
| D7 Duplicate-ID check before eligibility; traceable attempts | [ADR-023](#adr-023-duplicate-credential-id-check-first-and-traceable-issuance-attempts) | – |
| D8 Synthetic issuer isolation | [ADR-024](#adr-024-synthetic-issuer-and-key-isolation) | ADR-005, ADR-013 |

---

## ADR-001 Three-tier modular monolith
- **Context:** 7-hour build, a single developer, local Windows runtime, and a stated ban on microservices.
- **Decision:** One React SPA, one FastAPI process and one SQLite database. Domains are separated by modules (`services/<domain>`), not by processes.
- **Consequences:** Simple to run and debug, and transactions span domains. The domain boundaries keep a later split possible.
- **Rejected:** Microservices (forbidden; operational overhead). Server-rendered UI (the React stack is already set up).

## ADR-002 SQLite with SQLAlchemy 2 and Alembic
- **Context:** No PostgreSQL or Docker. A full-scale dataset of about 60k rows plus audit events.
- **Decision:** A SQLite file at `backend/data/credsure.db`. Every connection runs `PRAGMA foreign_keys=ON`, WAL journalling and a busy timeout (already implemented). The schema is managed only by Alembic with `render_as_batch=True`.
- **Consequences:** Zero setup and fast enough at this scale. There is a single writer, so write services use `BEGIN IMMEDIATE`. Partial indexes, CHECK constraints and triggers are available and used.
- **Rejected:** `Base.metadata.create_all()` in production code (bypasses migration history). PostgreSQL (forbidden).
- **Amended by [ADR-019](#adr-019-sqlite-transaction-configuration):** how `BEGIN IMMEDIATE` is obtained through SQLAlchemy, `recursive_triggers`, and the ban on `INSERT OR REPLACE`.

## ADR-003 Prefixed TEXT business IDs as primary keys
- **Context:** Learners must be identified by unique IDs, credential IDs must be unique and shareable, and synthetic data must be reproducible.
- **Decision:** Human-readable prefixed TEXT primary keys (`LRN-0000042`, `CRED-7K2M9QXR4T1B`). Sequential IDs for internal entities. Random Crockford base32 IDs for credentials (about 60 bits of entropy, not guessable). Credential IDs are upper case, enforced by CHECK. `audit_events` uses an INTEGER autoincrement for ordering.
- **Consequences:** IDs are the same in the API, the CSVs and the database, with no separate mapping layer. TEXT keys are slightly larger, which doesn't matter at this scale.
- **Rejected:** Integer surrogate keys plus a separate business key (two identifiers to manage). UUID4 (hard to read in a demo).

## ADR-004 Canonical JSON, SHA-256 and Ed25519 over the canonical bytes
- **Context:** Altered payloads must be detected and credentials must be verifiable by third parties.
- **Decision:** Canonical form is `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)` in UTF-8, with UTC `Z` timestamps at second precision and scores rounded to 2 dp. `payload_hash = SHA-256(canonical)` (hex). `signature = Ed25519.sign(canonical)` (base64). The stored `payload_json` is the canonical string itself.
- **Consequences:** Deterministic and dependency-free (`hashlib`, `json`, `cryptography`). The hash gives a fast integrity comparison and the signature proves origin. Every producer must use the single shared `canonical_json()`.
- **Rejected:** Signing the hex hash (adds an indirection with no benefit). JWS/JWT or W3C VC proofs (more libraries and time; out of scope). Storing the payload as a `JSON` column (re-serialisation could change the bytes).
- **Amended by [ADR-022](#adr-022-fixed-point-decimal-strings-in-signed-payloads)** (scores are fixed-point strings; no floats in payloads) **and [ADR-018](#adr-018-whole-second-utc-timestamps-with-one-shared-formatter-and-parser)** (timestamp format).

## ADR-005 Private keys in git-ignored files, public keys in the database
- **Context:** Secrets must not be hardcoded or committed, and there is no KMS locally.
- **Decision:** `POST /issuers` generates an Ed25519 key pair. The private key is written to `backend/keys/platform/<signing_key_id>.pem` (PKCS8, path from the `CREDSURE_SIGNING_KEY_DIR` setting). The public key (raw 32 bytes, base64) is stored on the issuer row. Synthetic issuer keys are kept in a separate directory; see ADR-024.
- **Consequences:** Keys are never in the database or API responses. Losing a key file stops issuance (`signing_key_unavailable`) but not verification.
- **Rejected:** Keys in `.env` (easy to leak, awkward multi-line values). Keys in the database (breaks the separation between secrets and data).
- **Amended by [ADR-024](#adr-024-synthetic-issuer-and-key-isolation):** platform and synthetic keys use separate directories and issuer ID ranges. The generator never writes to the platform key directory.

## ADR-006 Issuer history through immutable, superseding rows
- **Context:** Historical issuer information must be retained.
- **Decision:** Issuer rows are never deleted or edited, except `status` and `valid_to`. A change in name or key creates a new issuer with `supersedes_issuer_id`. Credentials keep their original `issuer_id`, and the signed payload contains an issuer snapshot.
- **Consequences:** Old credentials always verify with their original key. The cost is a few extra rows.
- **Rejected:** An `issuer_history` audit table (a 10th entity, and duplication). In-place updates (lose history).

## ADR-007 Immutable, versioned credential rules
- **Context:** The rule version applied to a credential must be preserved.
- **Decision:** Rules are versioned rows `(course_id, version)`. Only drafts can be edited. Activating a version retires the previous one with effective dates. A partial unique index allows one active rule per course. The applicable rule is the one in effect at `completed_at`.
- **Consequences:** Eligibility is reproducible and auditable after the fact.
- **Rejected:** Mutable rules with an audit log (the applied criteria could only be reconstructed indirectly).
- **Amended by [ADR-021](#adr-021-rule-effective-dates-anchored-to-completion-time):** activation may take a backdated `effective_from`, but never one that changes the applicable rule of a completion already recorded. Behaviour when no rule applies is defined there.

## ADR-008 Revocations table is authoritative; credential status is a cache
- **Context:** Revoked credentials must never verify as valid, and listing needs fast status filters.
- **Decision:** `revocations` (UNIQUE `credential_id`, immutable) is the source of truth. `credentials.status` and `revoked_at` are updated in the same transaction. A CHECK keeps the two in step. Verification treats the credential as revoked if **either** source says so.
- **Consequences:** Defence in depth against the A3 fault, plus simple queries.
- **Rejected:** Status only (no reason or approver record). Revocations only (every listing would need a join).
- **Amended by [ADR-017](#adr-017-final-database-constraint-set-and-atomic-irreversible-revocation):** a trigger makes `active → revoked` the only legal status change, and only when a matching `revocations` row exists. Reinstatement is impossible.

## ADR-009 Append-only, hash-chained audit log
- **Context:** Important decisions must be audited, and history must not be silently overwritten.
- **Decision:** An `audit_events` table with SQLite triggers that abort UPDATE and DELETE. Each event stores `previous_hash` and `event_hash` (SHA-256 over the canonical event fields). `GET /audit-events/verify-chain` recomputes the chain. Success events are written in the business transaction; rejections in a follow-up transaction.
- **Consequences:** Application-level tampering is blocked. Edits that bypass the triggers are detectable unless the attacker rewrites the whole chain; the limits are in ADR-020. Writes are serialised.
- **Rejected:** Log files (not queryable or linkable to entities). Per-entity history tables (nine extra tables).
- **Amended by [ADR-020](#adr-020-database-enforced-linear-audit-chain):** the database enforces a single linear chain (`AUTOINCREMENT`, unique predecessor, one genesis event, chain-link trigger). The exact hash definition and the threat-model limits are stated there.

## ADR-010 Simulated role-based access via request headers
- **Context:** Six actors and a 7-hour budget. Authentication is not a stated requirement.
- **Decision:** `X-Actor-Id` and `X-Actor-Role` headers, a central role-permission map (`core/security.py`), ownership checks in services, and a UI role switcher. Public endpoints work without headers.
- **Consequences:** All permission rules can be demonstrated and tested. The headers can be spoofed, which is an accepted demo-only risk. Replacing the `Actor` dependency with a JWT/OIDC dependency later would not change any service.
- **Rejected:** A full user, password and session system (2+ hours, adds a user entity outside the nine).

## ADR-011 Metrics computed on demand from source records
- **Context:** Derived metrics must come from source records.
- **Decision:** `services/metrics.py` runs SQL aggregates for every request and returns each definition (numerator and denominator) with `computed_at`.
- **Consequences:** Metrics are never stale and there is no extra table. It takes milliseconds at full scale.
- **Rejected:** A metrics or snapshot table (risk of drift; violates the rule).

## ADR-012 Rule-based anomaly detection by independent recomputation
- **Context:** Four well-defined faults, ground truth available, a short timeline.
- **Decision:** One deterministic detector per anomaly. Each detector recomputes the correct outcome from source records (completion timing, revocation timing, hash comparison, rejected-attempt audit events) and flags disagreements. Findings are computed on demand; a run summary is audited. Evaluation reads ground truth only at scoring time.
- **Consequences:** Results are explainable (each finding carries evidence), precision and recall are close to 1.0, and no ML dependencies are needed.
- **Rejected:** ML or statistical outlier models (opaque; unnecessary for rule violations). An anomaly findings table (not needed; it would be a 10th entity).

## ADR-013 Synthetic data: stdlib CSV, seeded, valid-first, separate ground truth
- **Context:** Seed 20261022, starter and full profiles, anomalies injected after valid generation, labels kept separate.
- **Decision:** `python -m app.datagen generate` uses only the standard library plus `cryptography` (already installed). A single `random.Random(seed)` and a fixed reference time drive all choices. Records are generated valid first and then mutated. Inputs go to `data/generated/<profile>/` and labels to `data/ground_truth/<profile>/`. `load` bulk-inserts with `record_source='synthetic_import'` (skipping service eligibility checks, keeping database constraints) and replays `issuance_attempts.csv` through the real `IssuanceService`.
- **Consequences:** Reproducible, byte-identical output with no new dependencies. Generated data never touches the test database.
- **Rejected:** Faker or pandas (new dependencies). Generating through the HTTP API (too slow, and it can't produce the faulty records).
- **Amended by [ADR-024](#adr-024-synthetic-issuer-and-key-isolation)** (synthetic issuer IDs and keys are isolated), **[ADR-021](#adr-021-rule-effective-dates-anchored-to-completion-time)** (rules are loaded before the completions they govern) **and [ADR-023](#adr-023-duplicate-credential-id-check-first-and-traceable-issuance-attempts)** (replay passes each `attempt_id` as the attempt reference and uses an injected clock).

## ADR-014 UTC timestamps
- **Context:** SQLite has no native timezone-aware datetime, and Python 3.10 is in use.
- **Decision:** A `UTCDateTime` TypeDecorator stores naive UTC and returns aware UTC (`datetime.timezone.utc`). Inbound naive datetimes are rejected. The API uses ISO-8601 with `Z`. Services use an injectable clock.
- **Consequences:** Comparisons such as `issued_at < completed_at` are correct in SQL because all stored values are in UTC with the same text format.
- **Rejected:** Storing local time (DST ambiguity). Unix epoch integers (poor readability in the database and CSVs).
- **Amended by [ADR-018](#adr-018-whole-second-utc-timestamps-with-one-shared-formatter-and-parser):** whole-second precision everywhere, an explicit storage format, and one shared formatter and parser.

## ADR-015 Frontend: minimal dependencies and state-based navigation
- **Context:** "Do not create unnecessary dependencies." React, Tailwind and Lucide are already installed.
- **Decision:** No router, state library, UI kit or data-fetching library. A typed `fetch` wrapper, React context for the actor, and a page-state switch in `App.tsx`. Vitest with `react-dom/server` for component tests (already set up).
- **Consequences:** Fast to build and nothing new to install. There are no deep links. Adding `react-router` later is easy if approved.
- **Rejected:** React Router, TanStack Query, component libraries (time and dependency cost with little demo value).

## ADR-016 API versioning and error format
- **Context:** An `/api/health` endpoint and an error envelope already exist from Task 01.
- **Decision:** Business endpoints go under `/api/v1`. Errors reuse the existing `{"error": {...}}` envelope with lowercase snake_case codes. Verification failures are results (HTTP 200), not errors.
- **Consequences:** Consistent clients, and verifiers never have to parse error bodies to learn a credential is revoked.

---

# Pre-T02 decisions (D1–D8)

## ADR-017 Final database constraint set and atomic, irreversible revocation
*Decision D1. Amends ADR-008.*
- **Context:** SQLite cannot alter constraints in place. A later change means an Alembic batch rebuild, which silently drops the table's triggers ([schema_review §6.1](schema_review.md#61-verified-behaviours-that-shape-the-design)). The reviewed design left cross-entity evidence and most credential columns unprotected.
- **Decision:** The first migration (`0001`) creates the complete constraint set in [data_dictionary.md §3](data_dictionary.md#3-tables) and the reference DDL in [§6](data_dictionary.md#6-reference-ddl-triggers-and-expression-indexes):
  - **Composite foreign keys** tie each credential to the completion record (same learner and course), the rule (same course) and the signing key (same issuer).
  - **Verification consistency:** the stored `result` must equal the BR-13 precedence function of the stored flags.
  - **Rule-state CHECKs:** draft ⇔ no `effective_from`; active ⇒ open window; retired ⇒ closed window; non-draft ⇒ at least one criterion; `rule_id` derived from course and version.
  - **Immutability triggers:**
    - Credentials: only `status`/`revoked_at` may change.
    - Rules: criteria are frozen after activation; transitions are `draft → active → retired`.
    - Issuers: only `active → retired`.
    - Revocations, verification requests and audit events: no update or delete.
    - Completion records: locked while an active credential references them.
  - **Audit-chain triggers and indexes:** see ADR-020.
- **Revocation** is one `BEGIN IMMEDIATE` transaction:
  1. Insert the `revocations` row.
  2. Update the credential `active → revoked` with `revoked_at` equal to the revocation's `revoked_at`.
  3. Insert the `credential.revoked` audit event.

  The trigger `trg_credentials_status_transition` rejects any status change that is not exactly this transition backed by a matching revocation row. **A revoked credential cannot be reinstated**: there is no API for it, and the database rejects `revoked → active`. Re-issue creates a new credential (BR-05).
- **Consequences:** Most mandatory rules hold even for code paths that bypass services, such as the synthetic loader. Any later constraint change must re-create the triggers from the shared module `app/database/triggers.py`, and a migration test asserts the exact trigger inventory. All four synthetic anomalies remain storable ([schema_review §8](schema_review.md#8-anomaly-injection-feasibility-review)).
- **Rejected:** Service-only enforcement (the loader bypasses services). Adding constraints incrementally after 0001 (each change means a table rebuild plus re-creating the triggers).
- **Validation:** before being recorded (2026-11-14), the reference DDL was executed in a scratch database on the bundled SQLite 3.35.5 with positive and negative cases. Details are in [data_dictionary §6](data_dictionary.md#6-reference-ddl-triggers-and-expression-indexes). This is not a substitute for the T02 migration tests.

## ADR-018 Whole-second UTC timestamps with one shared formatter and parser
*Decision D2. Amends ADR-014.*
- **Context:** Signed payloads used second precision while the database stored microseconds, so the same instant had two values. Raw CSV strings could also introduce a second text format that breaks SQL comparisons.
- **Decision:** All persisted timestamps and all signed-payload timestamps are **UTC with whole-second precision**. One module, `app/core/timeutil.py`, is used by the API schemas, services, generator, loader, detectors and verification:

  | Function | Behaviour |
  |----------|-----------|
  | `utc_now()` | Current UTC time truncated to whole seconds. This is the injectable clock. |
  | `to_utc(dt)` | Rejects naive datetimes, converts aware ones to UTC, truncates fractional seconds |
  | `format_utc(dt)` | `YYYY-MM-DDTHH:MM:SSZ`. The only wire format: API, payloads, audit hash, CSV. |
  | `parse_utc(s)` | Accepts ISO-8601 with an explicit offset (`Z` or `±HH:MM`), converts to UTC, truncates fractional seconds. Rejects naive input. |

  Storage: `UTCDateTime` (TypeDecorator over SQLAlchemy's SQLite `DATETIME`) applies `to_utc` on bind and stores the text `YYYY-MM-DD HH:MM:SS` (`storage_format` without microseconds, plus a matching `regexp`). It returns aware UTC values. Pydantic request and response models use an annotated type built on `parse_utc`/`format_utc`. The loader converts every CSV timestamp with `parse_utc` and never inserts raw strings. Detectors compare column to column or bind typed parameters, never string literals.
- **Consequences:** The stored `credentials.issued_at` equals the signed payload's `issued_at`. Lexical comparison of stored timestamps is chronological. Sub-second ordering within one second relies on `audit_events.event_id`.
- **Rejected:** Keeping microseconds in storage (two representations of one instant). Rejecting fractional seconds on input (browsers send milliseconds; truncation is deterministic).

## ADR-019 SQLite transaction configuration
*Decision D3. Amends ADR-002.*
- **Context:** Python 3.10's `sqlite3` driver begins a transaction only before DML, so reads made before a write run outside it (verified). A plain `session.execute("BEGIN IMMEDIATE")` conflicts with the driver's implicit `BEGIN`. `INSERT OR REPLACE` bypasses delete triggers unless `recursive_triggers` is on (verified).
- **Decision:**
  1. Every new connection runs `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=30000` and **`recursive_triggers=ON`**.
  2. The `connect` event sets `dbapi_connection.isolation_level = None`, which disables the driver's implicit transactions. The engine `begin` event emits `BEGIN IMMEDIATE` when the connection has the execution option `sqlite_write=True`, and `BEGIN` otherwise.
  3. `WriteSessionLocal = sessionmaker(bind=engine.execution_options(sqlite_write=True))` is used for **every write-critical operation**: master-data writes, rule activation, completion recording and correction, issuance, revocation, verification (it writes a row and an audit event), every audit write including rejection events, sequence-ID allocation, and each loader file. Read-only requests use `SessionLocal` (deferred `BEGIN`).
  4. **No `INSERT OR REPLACE`, `INSERT OR IGNORE`, `ON CONFLICT … DO UPDATE` or `Session.merge()`** on immutable or audit-sensitive tables (`issuers`, `credential_rules`, `credentials`, `revocations`, `verification_requests`, `audit_events`). Use plain `INSERT`. An `IntegrityError` is mapped explicitly, by constraint or trigger name, to a domain error code ([exception_catalogue §1.7](exception_catalogue.md#17-database-constraint-mapping)). Unmapped integrity errors are logged and returned as `500 internal_server_error`.
  5. **Integration tests use the real application engine and session factories** (temporary database file, migrated with `alembic upgrade head`), never a mocked session. Required tests:
     - `recursive_triggers` is `1` on fresh connections.
     - A second write session blocks while the first holds the lock.
     - REPLACE on `credentials` and `audit_events` fails.
     - Each mapped constraint produces its domain error code.
     - Concurrent issuance for the same learner and course yields exactly one credential and one `409`.
- **Consequences:** The eligibility reads, ID allocation and insert happen under one write lock, and audit writes are serialised. Writers queue (up to `busy_timeout`) instead of failing with `SQLITE_BUSY_SNAPSHOT`. Read-only traffic stays concurrent under WAL.
- **Rejected:** Taking `BEGIN IMMEDIATE` for every session (needlessly serialises reads). Relying on the driver's default transaction handling (unsafe reads).

## ADR-020 Database-enforced linear audit chain
*Decision D4. Amends ADR-009.*
- **Context:** The original hash formula was ambiguous (as written in Python it hashed only the predecessor's hash), and nothing prevented two events from extending the same predecessor.
- **Decision:**
  - `audit_events.event_id` is `INTEGER PRIMARY KEY AUTOINCREMENT` (`sqlite_autoincrement=True`).
  - `UNIQUE (previous_hash)` stops two events from extending the same predecessor.
  - The expression index `uq_audit_events_single_genesis ON audit_events(ifnull(previous_hash, 'GENESIS'))` allows **exactly one genesis event** (`previous_hash IS NULL`).
  - The trigger `trg_audit_events_chain_link` rejects any insert whose `previous_hash` is not the hash of the newest event, or that is not NULL on an empty table.
  - Append-only triggers block UPDATE and DELETE. `recursive_triggers=ON` (ADR-019) closes the REPLACE bypass.
  - **Serialisation:** audit events are written only through `WriteSessionLocal` (`BEGIN IMMEDIATE`). The chain head is read inside that transaction. A rejection event gets its own `BEGIN IMMEDIATE` transaction after the business transaction rolls back.
  - **Hash definition** (one function, also used by `verify-chain`):
    ```
    event_hash = sha256_hex(canonical_json({
        "previous_hash": <hex or null>, "event_uuid": ..., "event_type": ..., "entity_type": ...,
        "entity_id": ..., "actor_id": ..., "actor_role": ..., "outcome": ..., "reason_code": <str or null>,
        "attempt_ref": <str or null>, "details_json": <stored canonical string>, "occurred_at": format_utc(...)
    }))
    ```
  - `GET /audit-events/verify-chain` recomputes every hash, checks each link, and checks that `sqlite_sequence.seq` equals `MAX(event_id)` with no gaps in `event_id`.
- **Threat-model limits (documented, accepted):** The chain detects **application-level tampering** and edits that bypass the triggers without rewriting the whole chain. It does **not** protect against an attacker with unrestricted access to the database file. Such an attacker can drop triggers, delete the newest events and fix `sqlite_sequence`, recompute every hash after an edit, or replace the file with an older copy. Protecting against that would need an external anchor or a keyed chain, which is out of MVP scope.
- **Consequences:** A forked or broken chain can only arise from direct file manipulation. Every audit write costs one indexed lookup of the head row.
- **Rejected:** Application-only chaining (forks under concurrency). A keyed HMAC chain (the key would sit on the same machine; deferred).

## ADR-021 Rule effective dates anchored to completion time
*Decision D5. Amends ADR-007.*
- **Context:** Rule selection uses the completion timestamp, but activation always set `effective_from = now`. Backdated completions therefore found no rule, and synthetic rules had windows that the API could not create.
- **Decision:**
  - **Selection:** the applicable rule for a completion is the version of its course with `status IN ('active','retired')` and `effective_from ≤ completed_at < COALESCE(effective_to, +∞)`. A version applies only if it was effective **on or before** the completion timestamp. Windows of one course never overlap (single active rule; the predecessor's `effective_to` equals the successor's `effective_from`), so at most one version matches.
  - **Activation:** `effective_from` is optional and defaults to `utc_now()`. It must not be in the future (`422 rule_effective_date_invalid`). It must be later than the current active version's `effective_from`, which the window CHECK on the retiring predecessor also enforces. The predecessor is retired with `effective_to = effective_from` in the same transaction.
  - **Stability guarantee:** activation is **rejected** with `409 rule_effective_date_conflict` (listing the conflicting `completion_id`s) when any completion record of the course has `completed_at ≥ effective_from`. Activation therefore never changes the applicable rule of an already recorded completion, whether that completion had no rule or an earlier version. Enforcement: a service pre-check, plus triggers on activation (UPDATE) and on insertion of non-draft rules (INSERT, which covers the loader). Windows are immutable after activation, so a completion's applicable rule never changes silently.
  - **No applicable rule:** recording the completion is still allowed (it is a fact). The completion response returns `applicable_rule_id: null` with warning `no_applicable_rule`. Eligibility returns `eligible=false` with reason `no_applicable_rule`, and issuance returns `422 not_eligible` (audited). There is no fallback to another version and no automatic rebinding. The only remedy is a genuine new completion attempt with `completed_at` inside an effective window.
  - **Completion corrections** (FR-07, allowed only while no active credential references the record) may change `completed_at` and therefore the applicable rule. That change is explicit: `completion.corrected` records `before` and `after` values including `applicable_rule_id`.
  - **Synthetic data:** the loader inserts every rule version before the completions it governs, as the INSERT guard requires.
- **Consequences:** Eligibility of any recorded completion is reproducible forever. In normal forward operation (activation at "now"), the guard never fires.
- **Rejected:** Selecting by issuance time (judges learners by rules made after they completed). Allowing backdated activation with an acknowledgement flag (silently re-judges past completions; complex). Fallback to the earliest version (invents criteria that were never in force).

## ADR-022 Fixed-point decimal strings in signed payloads
*Decision D6. Amends ADR-004.*
- **Context:** Floats serialise differently in Python (`80.0`) and JavaScript (`80`), so a browser round trip produced a false `tampered` result (verified).
- **Decision:**
  - The payload `score` is a fixed-point decimal string with exactly two decimals, such as `"82.50"` or `"100.00"`, or `null` when there is no score. It is produced by `Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`.
  - `canonical_json()` accepts only `str`, `int`, `bool`, `None`, `dict` and `list`. It **raises on any `float`**, and calls `json.dumps` with `allow_nan=False`, `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`, encoded as UTF-8.
  - Every payload key in the schema is always present, with explicit `null` when empty. A presented payload with extra or missing keys hashes differently and is reported as `tampered`.
  - Storage is unchanged: `completion_records.score` stays `REAL`, rounded to 2 dp on write. Eligibility compares `Decimal` values of the score and the threshold.
- **Consequences:** A payload round-trips byte-identically through any JSON client.
- **Rejected:** Integer hundredths in the payload (less readable for verifiers). Keeping floats and normalising during verification (verification must hash the presented bytes as given).

## ADR-023 Duplicate credential ID check first, and traceable issuance attempts
*Decision D7.*
- **Context:** Eligibility was evaluated before the duplicate-ID check, so some duplicate-ID attempts were recorded as `not_eligible` and A2 detection missed them. Attempts were not traceable after a failed insert.
- **Decision:**
  - **Attempt reference:** every issuance attempt carries a unique `attempt_ref`. If the client supplies one, it must be unused (`409 duplicate_attempt_ref`). Otherwise the server generates `ATT-` + 12 Crockford base32 characters. Synthetic replay supplies the dataset's `ATT-nnnnnn`.
  - **Check order** inside one `BEGIN IMMEDIATE` transaction:
    1. Request schema validation.
    2. `attempt_ref` uniqueness.
    3. **The supplied `credential_id`'s format and uniqueness** (after normalising to upper case), giving `409 duplicate_credential_id`.
    4. Eligibility (BR-04), giving `422 not_eligible`, or `409 credential_already_issued` when the learner already holds an active credential. The internal reason is `already_issued` (SR-12 terminology).
    5. Build, hash and sign the payload.
    6. Insert the credential and its audit events.
  - **Traceability:** every attempt that passes step 2 must end with **exactly one terminal audit event**, either `credential.issued` or `credential.issue_rejected`, whose new `audit_events.attempt_ref` column holds the reference. The guarantee is split:
    - The **database** enforces **at most one** terminal event per `attempt_ref` (partial unique index `uq_audit_events_attempt_outcome`) and requires the reference on those two event types (CHECK `ck_audit_events_attempt_ref_required`).
    - The **application's transaction logic** provides the "at least one" half: the success event commits atomically with the credential, and every rejection path writes its event after rollback.
  - **Failed inserts:** if the insert fails (for example, a race caught by a partial unique index), the transaction rolls back and the rejection event is written in its own `BEGIN IMMEDIATE` transaction with the same `attempt_ref`. The duplicate-ID attempt therefore stays traceable.
  - A request rejected at step 2 is not a new attempt. It is logged but not audited, because its reference already has a terminal event.
  - **Synthetic replay** passes `attempt_id` as `attempt_ref` and an injected clock set to `attempted_at`.
- **Consequences:** Every A2 variant produces `duplicate_credential_id`. The A2 detector reads `audit_events.attempt_ref` directly, without JSON parsing. The audit hash includes `attempt_ref`.
- **Rejected:** An `issuance_attempts` table (a 10th entity; the audit log already serves the purpose). Storing the reference only in `details_json` (it could not be constrained unique).

## ADR-024 Synthetic issuer and key isolation
*Decision D8. Amends ADR-005 and ADR-013.*
- **Context:** The generator wrote seed-derived keys into the platform key folder with IDs that could collide with platform issuers, which could overwrite a real private key.
- **Decision:**
  - **ID ranges:** platform issuers use `ISS-001`–`ISS-899` (the sequence never allocates 900 or above). Synthetic issuers use `ISS-901`–`ISS-999`. CHECK `ck_issuers_synthetic_range` enforces `(record_source = 'synthetic_import') = (issuer_id GLOB 'ISS-9[0-9][0-9]')`.
  - **Key directories** (all under the git-ignored `backend/keys/`):
    - platform: `backend/keys/platform/<signing_key_id>.pem` (`CREDSURE_SIGNING_KEY_DIR`)
    - synthetic: `backend/keys/synthetic/<profile>/<signing_key_id>.pem` (`CREDSURE_SYNTHETIC_KEY_DIR`)
  - The signing service chooses the directory from the issuer's `record_source`.
  - **Generator:** writes keys only under the synthetic directory. It resolves and verifies every target path to be inside that directory, and **never opens, reads, writes, reuses or deletes** anything in the platform directory. It never emits issuers outside the synthetic range.
  - **Platform key creation** uses exclusive-create mode (`open(path, "xb")`) and fails rather than overwrite an existing file.
  - **Loader:** accepts only issuer rows in the synthetic range with `record_source='synthetic_import'`. Conflicting primary keys abort that file's transaction unless `--reset` is given.
- **Consequences:** Platform and synthetic credentials can coexist in one database without sharing keys. Synthetic keys are derivable from the public seed and therefore **forgeable**. They are for development and demonstration only, and must never be presented as a real institution.
- **Rejected:** A shared key directory with distinct file names (one bug away from overwriting). Random synthetic keys (would break byte-identical regeneration).
