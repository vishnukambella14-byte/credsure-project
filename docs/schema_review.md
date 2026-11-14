# CredSure: Pre-Implementation Schema Design Review

| Item | Value |
|------|-------|
| Scope | The nine-table design in [data_dictionary.md](data_dictionary.md), checked against all specification documents in `docs/` |
| Reviewed | PRD, business_rules, user_stories, architecture, data_dictionary, exception_catalogue, api_contract, roadmap, architecture_decisions |
| Code inspected (read-only) | `backend/app/database/session.py`, `backend/app/database/base.py`, `backend/app/models/__init__.py`, `database/migrations/env.py`, `backend/alembic.ini` |
| Date | 2026-11-14 |
| Status | Review only. No specification, code, model or migration was changed. |
| Resolution (2026-11-14) | Decisions **D1–D8 accepted** and recorded as [ADR-017 – ADR-024](architecture_decisions.md#pre-t02-decisions-d1d8), with the specification documents updated. SR-01–SR-11 and SR-13–SR-16 are resolved by the decisions. SR-17 is partly resolved: the shared trigger module and inventory test are recorded, and foreign-key handling for future batch migrations is deferred. SR-12 is resolved: internal reason `already_issued`, HTTP `409`, API error code `credential_already_issued` (BR-04). The other MEDIUM/LOW items in §11 stay deferred unless listed in an ADR. |

## Severity scale

| Severity | Meaning |
|----------|---------|
| **BLOCKER** | Must be decided before the T02 migration is written. SQLite cannot `ALTER` constraints, so changing it later means rebuilding the table, and that rebuild silently drops triggers (verified, §6.1). |
| **HIGH** | Causes incorrect behaviour, a security gap, or a failed demo or test target if not fixed. Must be resolved in T02 or before the task that depends on it. |
| **MEDIUM** | Weakens integrity or causes friction. Fix if time allows; cheap now, expensive later. |
| **LOW** | Polish, wording or defensive hardening. Can be deferred. |

**Finding types:**
- **Contradiction**: two documents disagree. These are confirmed by quoting both sides.
- **Defect**: the specification, as written, produces wrong results.
- **Gap**: the specification is silent where the implementation needs a decision.
- **Recommendation**: an improvement that no document contradicts.

### Evidence base

The key claims were verified with the project's own interpreter (Python 3.10.0, bundled
**SQLite 3.35.5**, SQLAlchemy 2.0.57, Alembic 1.20.1). The probes ran in a scratch directory
outside the repository.

| Probe | Result |
|-------|--------|
| JSON1 functions available | `json_extract` works |
| `INSERT OR REPLACE` on a table with a `BEFORE DELETE` abort trigger, `recursive_triggers` OFF (default) | **The row is silently rewritten.** The trigger does not fire. |
| Same, with `PRAGMA recursive_triggers=ON` | Blocked: `append-only` |
| Partial unique index `(learner, course) WHERE status='active'` | Second active credential rejected. A re-issue after `status='revoked'` is accepted. |
| pysqlite (Python 3.10) transaction state after a `SELECT` | `in_transaction = False`. A transaction starts only at the first DML statement. |
| JSON number round trip: Python `{"score":80.0}` versus a JavaScript-serialised `{"score":80}` | Canonical strings differ, so SHA-256 differs |
| Python precedence of `previous_hash or "" + canonical` | Evaluates to `previous_hash` alone. The event content is not hashed. |
| Alembic autogenerate, unnamed `CheckConstraint` with the project's naming convention | `InvalidRequestError: … requires that constraint is explicitly named` |
| Alembic autogenerate with a custom `TypeDecorator` | Rendered as `<module>.UTCDateTime()` with no import in the migration |
| Alembic autogenerate with `Index(..., sqlite_where=...)` | Rendered correctly |
| Alembic `batch_alter_table(recreate="always")` on a table with a trigger | **Trigger silently removed** |

---

## 1. Overall assessment

The nine-table design is **sufficient and fundamentally sound** for every documented workflow.
No tenth table is needed. The core integrity decisions are correct and suit SQLite:
- prefixed TEXT keys
- immutable versioned rules
- superseding issuer rows
- a revocations table as the source of truth
- partial unique indexes for "one active" rules
- an append-only audit log with triggers
- canonical payload text stored verbatim

The weaknesses are not in the table inventory. They are in **five areas**:

1. **Cross-entity consistency is unenforced.** A credential can reference a completion record or rule belonging to a different learner or course, or a signing key belonging to a different issuer (SR-02).
2. **The transaction model is not implementable as written.** pysqlite does not open a transaction for `SELECT`s, so the eligibility check, `MAX(id)+1` and the audit chain tip are all read outside the write lock (SR-04). The audit hash chain can then fork, and the hash formula as written does not hash the event content (SR-05, SR-06).
3. **The triggers have bypasses.** `INSERT OR REPLACE` rewrites "append-only" rows (SR-03). Most `credentials` columns remain mutable (SR-07). Future batch migrations erase all triggers (SR-17).
4. **Hashing can become non-deterministic.** Float scores (SR-09) and mixed timestamp precision (SR-10) will produce false `tampered` results.
5. **Three confirmed cross-document contradictions:** rule activation time versus completion-time rule selection (SR-08), issuance check order versus A2 detection (SR-11), and the `already_issued` error code (SR-12).

**Verdict: T02 can proceed** once the four T02 decisions in §10 are recorded (about 15
minutes). The other HIGH items do not change the schema and can be settled before T03/T04.

### Answers to the twelve review questions

| # | Question | Answer | Findings |
|---|----------|--------|----------|
| 1 | Are the nine tables and their relationships sufficient? | **Yes.** Every workflow maps to existing tables. Gaps are in constraints and service contracts, not entities. | SR-02, SR-11, SR-22 |
| 2 | Are PKs, FKs, UNIQUEs, CHECKs, indexes and nullability correct? | **Mostly.** PKs and nullability are right. Composite FKs, several CHECKs and some indexes are missing. ID-format CHECKs are weak. | SR-01, SR-02, SR-14, SR-15, SR-23, SR-31, SR-32 |
| 3 | How should SQLite enforce one active credential per learner and course? | A partial unique index, as specified (verified on 3.35.5). It must be explicitly named. The service must map its `IntegrityError` to `409`. It cannot express expiry. | §5.2, SR-21 |
| 4 | How does re-issue after revocation work? | It works (verified): revoking removes the row from the partial index. There is no link between the old and new credential, renewal of *expired* credentials is impossible, and completion corrections after revocation rewrite the revoked credential's evidence. | SR-21 |
| 5 | Is rule-version selection consistently based on completion time? | **No.** BR-07 selects by `completed_at`, but activation always sets `effective_from = now`, so backdated completions find no applicable rule. Synthetic data uses backdated rules that the API cannot create. | SR-08 (contradiction) |
| 6 | Does issuer replacement preserve historical verification? | **Yes in principle.** Rows are retained and the payload embeds a key snapshot. However, `signing_key_id` is not tied to `issuer_id`, supersession can fork, and synthetic keys can overwrite platform keys. | SR-02, SR-13, SR-23 |
| 7 | Is audit hash chaining correctly designed? | **No.** The formula is defective as written, field serialisation is unspecified, the chain tip is read outside the write lock, and nothing in the database prevents forks. | SR-04, SR-05, SR-06 |
| 8 | Are append-only triggers sufficient for the threat model? | **Against application bugs, nearly** (after the REPLACE fix). **Against anyone with file access, no.** Triggers can be dropped, tail truncation and full chain recomputation go undetected, and batch migrations remove triggers. The documents overstate this. | SR-03, SR-17, SR-18, SR-19 |
| 9 | Is canonical JSON specified precisely enough? | **No.** Float formatting, timestamp precision, null/missing keys, NaN, base64 variant and Unicode normalisation are unspecified. | SR-09, SR-10, SR-39 |
| 10 | Can the four anomalies be represented without weakening constraints? | **Yes**, including with every constraint recommended here. A2 needs service-contract changes, and A1 needs `recorded_at` adjustment. | §8 |
| 11 | Do the API, generator and schema agree on names, IDs, timestamps and states? | **Mostly.** Mismatches: `already_issued` versus `credential_already_issued`, timestamp precision, ground-truth path, `course_inactive` versus `archived`, key-ID sequencing, loader-required columns. | §9, SR-22 |
| 12 | Can Alembic discover models under `backend/app/models/`? | **Yes, with conditions.** `env.py` imports `app.models.Base`, and `prepend_sys_path` plus the explicit `sys.path` insert make `app` importable. Every model module must be imported in `models/__init__.py`, every CHECK must be named, custom types need hand-editing, and triggers must be added by hand. | SR-16 |

---

## 2. Table-by-table review

### 2.1 `issuers`
- **Good:** immutable history through `supersedes_issuer_id`, public key only, no-delete trigger.
- SR-23 (MEDIUM): `supersedes_issuer_id` is not UNIQUE, so one issuer can be superseded twice (a fork). There is no CHECK against self-reference. The mutable-column rule (only `status` and `valid_to`) is enforced only in the service. `status='retired'` is not tied to `valid_to`.
- SR-27 (LOW, contradiction): key-ID format `<issuer_id>-K\d+` says "sequence per issuer", but ADR-006 creates a new issuer row for every new key, so `K` is always `1`.
- SR-02: needs `UNIQUE (issuer_id, signing_key_id)` so credentials can reference the pair.

### 2.2 `learners`
- **Good:** ID is separated from names, email is unique, name index exists for search.
- SR-31 (LOW): `GLOB 'LRN-[0-9]*'` accepts `LRN-1abc` because `*` matches anything. Use an exact 7-digit pattern. `email` "stored lower-case" has no CHECK.

### 2.3 `courses`
- **Good:** single current issuer, status flag.
- SR-30 (LOW, contradiction): the status value `archived` versus the eligibility reason `course_inactive`.
- SR-32 (LOW): `issuer_id` is indexed. OK.

### 2.4 `credential_rules`
- **Good:** `(course_id, version)` UNIQUE, partial unique index for the active rule, effective dates.
- SR-08 (HIGH): the effective-date semantics conflict with completion-time selection.
- SR-15 (MEDIUM): missing CHECKs:
  - `retired ⇒ effective_to NOT NULL`
  - `active ⇒ effective_to IS NULL`
  - `draft ⇔ effective_from IS NULL`
  - non-draft must have at least one criterion
  - `rule_id` should be derivable from `course_id` and `version`

  BR-06 (a mandatory rule) is enforced only in the service, so a database trigger is warranted. Effective windows of one course may overlap or leave gaps with nothing to detect it.
- SR-02: needs `UNIQUE (rule_id, course_id)` as a composite FK target.

### 2.5 `completion_records`
- **Good:** attempt uniqueness, score and attendance range CHECKs, `completed ⇒ completed_at`, `completed_at ≤ recorded_at`.
- SR-02: needs `UNIQUE (completion_id, learner_id, course_id)` as a composite FK target.
- SR-21 (MEDIUM): the BR-20 lock covers only *active* credentials, so a correction after revocation rewrites the revoked credential's evidence. It can also create A1 false positives.
- SR-40 (LOW): A1 variant 1 must also move `recorded_at`, or the CHECK `completed_at ≤ recorded_at` fails.
- SR-32 (LOW): `course_id` FK has no leading index; the `(learner_id, course_id)` index does not cover course-first lookups.

### 2.6 `credentials`
- **Good:** upper-case PK, partial unique index for the active credential, canonical payload stored as text, consistency between `status` and `revoked_at`.
- SR-02 (BLOCKER): `learner_id`/`course_id` are not tied to `completion_id`, `course_id` is not tied to `rule_id`, and `signing_key_id` is not tied to `issuer_id`.
- SR-07 (HIGH): the trigger protects only payload, hash, signature, key ID and ID. `learner_id`, `course_id`, `completion_id`, `rule_id`, `issuer_id`, `issued_at`, `expires_at` and `credential_type` stay mutable. `status` can move `revoked → active`, and nothing requires a revocation row.
- SR-10 (HIGH): the `issued_at` column (microseconds) disagrees with the payload `issued_at` (seconds).
- SR-31 (LOW): the CHECK enforces only upper case. The Crockford format, hash hex and signature length are unchecked.
- SR-32 (LOW): add indexes on `completion_id`, `rule_id` and `issuer_id`.

### 2.7 `revocations`
- **Good:** `credential_id` UNIQUE, reason CHECK, immutable through triggers.
- SR-07: `credentials.revoked_at` should equal `revocations.revoked_at`; check this in the status-transition trigger.
- SR-24 (LOW): if `reason_code` is typed as a Pydantic `Enum`, an unknown code returns `validation_error` without an audit event, while the catalogue says `revocation_reason_required` is audited.

### 2.8 `verification_requests`
- **Good:** raw `submitted_credential_id` kept, nullable resolved FK, every check stored as a flag.
- SR-14 (MEDIUM): `result` is not constrained by the flags. A CHECK that encodes the BR-13 precedence is compatible with A3 and A4 injection (§8). The rows have no update or delete protection, although BR-18 forbids deletes and A3/A4 detection depends on these rows staying unchanged.
- SR-22 (LOW): the `channel` value for synthetic rows is unspecified (`api` or `portal` only).

### 2.9 `audit_events`
- **Good:** integer ordering key, polymorphic reference without FKs, append-only triggers, hash columns.
- SR-05 (HIGH): the hash formula is defective as written and its input serialisation is unspecified.
- SR-06 (HIGH): no database protection against chain forks. `previous_hash` is not UNIQUE. `AUTOINCREMENT` is not requested; SQLAlchemy needs `sqlite_autoincrement=True`.
- SR-03 (HIGH): the REPLACE bypass.
- SR-37 (LOW): `eligibility.evaluated` uses `entity_type=credential` with `entity_id = learner_id:course_id`, even after a credential ID exists.

---

## 3. Relationship and cardinality review

| Relationship (data_dictionary §2) | Assessment |
|-----------------------------------|------------|
| issuers 1:N courses, 1:N credentials | Correct. `credentials.issuer_id` correctly records history and is independent of `courses.issuer_id` after re-pointing. |
| issuers 0..1:N issuers (supersedes) | **Should be 0..1:0..1.** Each issuer is superseded at most once. Add `UNIQUE(supersedes_issuer_id)` (SR-23). |
| courses 1:N credential_rules (max one active) | Correct; the partial unique index enforces it. |
| learners/courses 1:N completion_records | Correct. |
| completion_records 1:0..N credentials | Correct cardinality (N > 1 only after re-issue). **The learner and course of both sides are not forced to match** (SR-02). |
| credential_rules 1:N credentials | Correct cardinality. **The rule's course is not forced to match the credential's course** (SR-02). |
| credentials 1:0..1 revocations | Correct (UNIQUE FK). |
| credentials 1:0..N verification_requests (nullable) | Correct. A CHECK should tie `credential_id IS NULL` to `result='not_found'` (SR-14). |
| audit_events polymorphic | Correct choice. FKs would block writes and couple audit to entity lifecycles. |
| Signing key reference | `credentials.signing_key_id` has **no relationship at all**. Make it part of a composite FK to `issuers(issuer_id, signing_key_id)` (SR-02). |

---

## 4. Findings: BLOCKER and HIGH

### SR-01 · BLOCKER · Gap. Constraint set must be final before the migration is generated
- **Affected:** roadmap §5 (T02), ADR-002, data_dictionary §3.
- **Finding:** SQLite cannot add or alter CHECK, UNIQUE or FK constraints. Alembic does it with a batch "move and copy" rebuild, which **silently drops every trigger on the table** (verified). Each constraint added after 0001 therefore costs a rebuild and re-creation of all triggers. The current dictionary lacks several cheap constraints (SR-02, SR-07, SR-14, SR-15, SR-23, SR-31).
- **Recommendation:** approve the consolidated constraint list in §5 before writing models. Put trigger DDL in a single module (e.g. `app/database/triggers.py`) that is reusable by later migrations. Add a migration test that asserts the exact trigger inventory in `sqlite_master` after `upgrade head`.

### SR-02 · BLOCKER · Defect. Cross-entity consistency is not enforced
- **Affected:** data_dictionary §3.4–3.6; BR-03 (evidence), BR-06, BR-17; ADR-008.
- **Finding:** A `credentials` row can reference:
  - a completion record of another learner or course
  - a rule of another course
  - a `signing_key_id` belonging to another issuer

  The services prevent this on the API path. The synthetic loader explicitly bypasses service checks (FR-25), and nothing else protects the stored evidence, which is the basis of BR-03 and the A1 detector.
- **Recommendation:** add composite keys. They need no new columns and are fully supported by SQLite.
  - `completion_records`: `UNIQUE (completion_id, learner_id, course_id)`
  - `credential_rules`: `UNIQUE (rule_id, course_id)`
  - `issuers`: `UNIQUE (issuer_id, signing_key_id)`
  - `credentials`: **replace** the single-column FKs with
    - `FOREIGN KEY (completion_id, learner_id, course_id) → completion_records`
    - `FOREIGN KEY (rule_id, course_id) → credential_rules`
    - `FOREIGN KEY (issuer_id, signing_key_id) → issuers`

  Keep the separate `learner_id → learners` and `course_id → courses` FKs. Do not duplicate the single-column versions of the composite FKs, or the naming convention `fk_<table>_<column_0>_<referred>` will produce clashing names. A1 injection stays possible because anomalous credentials still reference their own learner's completion record (§8).

### SR-03 · HIGH · Defect. `INSERT OR REPLACE` bypasses the delete triggers
- **Affected:** data_dictionary §3.6, §3.7, §3.9; BR-02, BR-22; `backend/app/database/session.py`.
- **Finding:** SQLite's REPLACE conflict resolution deletes the conflicting row **without firing delete triggers unless `recursive_triggers` is ON**. It is OFF by default, and the current `_set_sqlite_pragmas` does not set it. Verified: an "append-only" audit row was silently rewritten. The same applies to `credentials` (which is exactly the A2 "overwrite" fault) and `revocations`.
- **Recommendation:** in T02, add `PRAGMA recursive_triggers=ON` to the per-connection pragmas. Forbid `INSERT OR REPLACE`, `prefix_with("OR REPLACE")` and `on_conflict_do_update` on protected tables. Add a test showing that REPLACE on `audit_events` and `credentials` raises an error.

### SR-04 · HIGH · Gap. `BEGIN IMMEDIATE` is not implementable with the current engine configuration
- **Affected:** ADR-002, architecture §4 (Transactions), BR-03, data_dictionary §1 (`MAX(id)+1`), BR-22.
- **Finding:** Python 3.10's `sqlite3` begins a transaction only before DML (verified: `in_transaction=False` after `SELECT`). As a result:
  - The eligibility reads, the `MAX(id)+1` sequence read and the audit chain-tip read all run **outside** the transaction that later writes.
  - Emitting `BEGIN IMMEDIATE` through `session.execute` conflicts with the driver's own implicit `BEGIN`.
  - Consequences: duplicate sequence IDs (surfacing as 500 errors), two concurrent issuances both passing eligibility (the partial index catches the second, but as an unmapped `IntegrityError`), and audit-chain forks (SR-06). FastAPI runs sync endpoints in a thread pool, so concurrency is real even locally.
- **Recommendation:** use the standard SQLAlchemy pysqlite recipe in `session.py` (T02, small change):
  ```python
  @event.listens_for(engine, "connect")
  def _on_connect(dbapi_conn, _):
      dbapi_conn.isolation_level = None          # disable pysqlite's implicit BEGIN
      # existing pragmas + PRAGMA recursive_triggers=ON

  @event.listens_for(engine, "begin")
  def _on_begin(conn):
      write = conn.get_execution_options().get("sqlite_write", False)
      conn.exec_driver_sql("BEGIN IMMEDIATE" if write else "BEGIN")
  ```
  Expose `WriteSessionLocal = sessionmaker(bind=engine.execution_options(sqlite_write=True))` for write services. Map `IntegrityError` from the partial unique indexes to `409 credential_already_issued` and `409 duplicate_credential_id`.

### SR-05 · HIGH · Defect. The audit hash formula is defective and underspecified
- **Affected:** data_dictionary §3.9 (`event_hash`), BR-22, ADR-009.
- **Finding:** `sha256(previous_hash or "" + canonical({...}))` evaluates in Python as `sha256(previous_hash)` whenever a previous hash exists (verified). Every event after the first would then hash only its predecessor's hash, ignoring its own content. BR-22 writes `sha256(previous_hash + canonical(...))`, which is a different notation. The serialisation of `occurred_at`, a null `reason_code` and `details_json` (string or object) is also unspecified, so writer and verifier can disagree.
- **Recommendation:** define the hash in one place:
  ```
  event_hash = sha256_hex(canonical_json({
      "previous_hash": previous_hash,          # null for the first event
      "event_uuid": ..., "event_type": ..., "entity_type": ..., "entity_id": ...,
      "actor_id": ..., "actor_role": ..., "outcome": ..., "reason_code": ...,   # null allowed
      "details_json": <the stored canonical string, as a string>,
      "occurred_at": "YYYY-MM-DDTHH:MM:SSZ"   # same formatter as payloads (SR-10)
  }))
  ```
  Use the same `canonical_json()` as credential payloads, so one tested function covers both.

### SR-06 · HIGH · Gap. Nothing prevents audit chain forks under concurrent writes
- **Affected:** data_dictionary §3.9, BR-21/22, architecture §4 (rejection events in a separate transaction).
- **Finding:** Without SR-04, two requests can read the same chain tip and insert two events with the same `previous_hash`. The chain is then permanently broken, and `verify-chain` reports corruption that no attacker caused. Rejection events are written in a follow-up transaction, so they face the same race.
- **Recommendation:** in addition to SR-04, enforce linearity in the database (T02 migration):
  ```sql
  CREATE TRIGGER trg_audit_events_chain_link BEFORE INSERT ON audit_events
  WHEN NEW.previous_hash IS NOT (SELECT event_hash FROM audit_events ORDER BY event_id DESC LIMIT 1)
  BEGIN SELECT RAISE(ABORT, 'audit chain head mismatch'); END;
  ```
  - Add `UNIQUE (previous_hash)`.
  - Declare the table with `sqlite_autoincrement=True`. `sqlite_sequence` then keeps the high-water mark, so `verify-chain` can also detect naive tail deletion (SR-18).
  - Read the tip **inside** the `BEGIN IMMEDIATE` transaction.

### SR-07 · HIGH · Defect. `credentials` is only partially immutable, and status transitions are unconstrained
- **Affected:** data_dictionary §3.6 (`trg_credentials_payload_immutable`), BR-08, BR-12, BR-16, ADR-008.
- **Finding:**
  - The trigger fires only on `UPDATE OF payload_json, payload_hash, signature, signing_key_id, credential_id`. The evidence columns (`learner_id`, `course_id`, `completion_id`, `rule_id`, `issuer_id`, `issued_at`, `expires_at`, `credential_type`) remain updatable.
  - `status` can be set back from `revoked` to `active` (the CHECK only pairs it with `revoked_at`). That would make status-based listings and metrics show a revoked credential as active. Verification stays correct only because it also reads `revocations` (BR-12).
- **Recommendation:** replace the column-list trigger with two triggers:
  ```sql
  -- only status and revoked_at may ever change
  CREATE TRIGGER trg_credentials_immutable BEFORE UPDATE ON credentials
  WHEN NEW.credential_id IS NOT OLD.credential_id OR NEW.learner_id IS NOT OLD.learner_id
    OR NEW.course_id IS NOT OLD.course_id OR NEW.completion_id IS NOT OLD.completion_id
    OR NEW.rule_id IS NOT OLD.rule_id OR NEW.issuer_id IS NOT OLD.issuer_id
    OR NEW.credential_type IS NOT OLD.credential_type OR NEW.issued_at IS NOT OLD.issued_at
    OR NEW.expires_at IS NOT OLD.expires_at OR NEW.payload_json IS NOT OLD.payload_json
    OR NEW.payload_hash IS NOT OLD.payload_hash OR NEW.signature IS NOT OLD.signature
    OR NEW.signing_key_id IS NOT OLD.signing_key_id OR NEW.schema_version IS NOT OLD.schema_version
    OR NEW.issued_by IS NOT OLD.issued_by OR NEW.record_source IS NOT OLD.record_source
    OR NEW.created_at IS NOT OLD.created_at
  BEGIN SELECT RAISE(ABORT, 'credentials: only status/revoked_at may change'); END;

  -- the only legal transition is active -> revoked, backed by a revocation row
  CREATE TRIGGER trg_credentials_status_transition BEFORE UPDATE OF status, revoked_at ON credentials
  WHEN NOT (OLD.status = 'active' AND NEW.status = 'revoked' AND EXISTS (
        SELECT 1 FROM revocations r WHERE r.credential_id = NEW.credential_id
        AND r.revoked_at = NEW.revoked_at))
  BEGIN SELECT RAISE(ABORT, 'credentials: only active -> revoked with matching revocation'); END;
  ```
  This matches the revocation sequence in architecture §5.3, which inserts the revocation row before updating the credential. The rule applies to updates only; the loader inserts final-state rows (§8).

### SR-08 · HIGH · Contradiction. Completion-time rule selection versus activation-time effective dates
- **Affected:** BR-07, AS-03, ADR-007 versus FR-04, US-03 ("`effective_from = now`"), api_contract §4.6; also api_contract §4.5 (completion `2026-03-14` in an example dated 2026-11-14) and data_dictionary §5.1 (synthetic rules effective from course creation).
- **Finding:**
  - The applicable rule is the one whose window contains `completed_at` (BR-07), but activation always sets `effective_from` to the activation time.
  - A completion recorded with a past `completed_at`, earlier than the first activation, gets `no_applicable_rule`. That is the normal case for backfilled results, and the API example itself.
  - The US demo (activate the rule, then record a completion) works only if `completed_at` is after the activation instant.
  - The generator produces backdated rule windows that the platform API cannot create, so the two paths support different capabilities.
- **Recommendation (decision D5, before T03/T05):**
  - Allow an optional `effective_from` on `POST /rules/{id}/activate`, with these conditions:
    - It defaults to `now`.
    - It must be no later than `now`.
    - It must be later than the previous version's `effective_from`.
    - The previous version's `effective_to` is set to the same instant.
    - The action is audited.
  - Define a deterministic tie-break: the highest `version` whose window contains `completed_at`.
  - Add a test case: completion before v2 activation → v1 applies; completion before v1 → `no_applicable_rule`.
  - No schema change is needed.

### SR-09 · HIGH · Defect. Floats in the signed payload make hashing non-deterministic
- **Affected:** BR-08, ADR-004, data_dictionary §4 (`score` as a number), api_contract §4.3 (verifier presents a payload as a JSON object).
- **Finding:** Python serialises `80.0` as `80.0`; JavaScript's `JSON.stringify` serialises it as `80`. A payload pasted into the Verify page and re-sent by the browser canonicalises to different bytes, so the result is a **false `tampered`** (verified). Rounding to 2 dp does not fix the textual representation.
- **Recommendation (decision D6, before T03):**
  - Carry `score` in the payload as a fixed-format string (`"82.50"`) or `null`. Keep the `REAL` column for comparisons.
  - Allow only strings, integers, booleans and `null` in payloads, and enforce this in `canonical_json()`.
  - Call `json.dumps(..., allow_nan=False)`.
  - All schema keys are always present, with explicit `null`.
  - Any extra or missing key is treated as `tampered`.
  - Use standard base64 (RFC 4648, with padding) for signatures and public keys.

### SR-10 · HIGH · Contradiction. Timestamp precision differs between the database and the payload
- **Affected:** BR-08 and ADR-004 (payload: whole seconds) versus data_dictionary §1 and ADR-014 (`UTCDateTime` storing microseconds); architecture §5.4 (loader); exception_catalogue A1 (SQL comparison `c.issued_at < cr.completed_at`).
- **Finding:**
  - The same instant is stored with microseconds in `credentials.issued_at` and with seconds in the payload, so the column and the signed value disagree.
  - Sub-second differences can flip A1 hard negatives ("issued minutes after completion" is safe; "seconds after" is not).
  - If the loader inserts raw CSV strings (`2026-03-14T10:00:00Z`), SQLite stores a second text format. Lexical comparisons with SQLAlchemy's `YYYY-MM-DD HH:MM:SS.ffffff` format then break, because `'T'` sorts after `' '`.
- **Recommendation (decision D2, T02):**
  - `UTCDateTime.process_bind_param` rejects naive values, converts to UTC and truncates to whole seconds for **all** columns.
  - Use one formatter, `to_iso_z()` (`%Y-%m-%dT%H:%M:%SZ`), for the API, payloads, the audit hash and CSVs.
  - The loader parses every CSV timestamp into `datetime` before inserting.
  - Detectors compare column to column, or bind typed parameters. Never compare against string literals.

### SR-11 · HIGH · Contradiction. Issuance check order defeats A2 detection
- **Affected:** architecture §5.1 (eligibility evaluated **before** the duplicate-ID check) versus exception_catalogue A2 (variants `exact_duplicate`, `case_variant`, `same_learner_retry`, with every attempt expected to produce `duplicate_credential_id`); PRD C-02; data_dictionary §5.1 (`issuance_attempts.csv`).
- **Finding:**
  - A duplicate-ID attempt against an ineligible pair, or the `same_learner_retry` variant (the learner already holds an active credential), fails eligibility first. It is recorded as `not_eligible`, and the A2 detector, which keys on `reason_code='duplicate_credential_id'`, misses it. Recall drops below the 1.0 target (PRD §8).
  - The detector maps findings through `details.attempt_id`, but neither the issuance API nor the service contract accepts an attempt reference.
- **Recommendation (decision D7, before T04):**
  - Validate a supplied `credential_id` (format, normalisation, uniqueness) **before** eligibility. An ID collision is an input error, independent of the learner.
  - Add an optional `attempt_ref` (or `correlation_id`) parameter to the issuance service, recorded in audit `details_json`. The CLI replay passes `attempt_id`. The public API may leave it out.
  - Replay uses an injected clock set to `attempted_at`, so replayed credentials are deterministic.

### SR-12 · MEDIUM · Contradiction. `already_issued` is both an eligibility reason and a 409 error
- **Affected:** BR-04 check 8 (`already_issued` → part of `422 not_eligible`), api_contract §4.1 (`not_already_issued` check) versus exception_catalogue §1.4, api_contract §4.2 and US-05 (`409 credential_already_issued`).
- **Recommendation:** the eligibility endpoint reports reason `already_issued`. Issuance returns `409 credential_already_issued` whenever that reason is present, even alongside other reasons. Document the mapping. The partial-index `IntegrityError` maps to the same code (SR-04).

### SR-13 · HIGH · Gap. Synthetic issuer IDs and keys collide with platform issuers
- **Affected:** ADR-005 ("generator … writes them to the same folder"), AS-11, data_dictionary §1 (`ISS-001` sequence), user_stories demo step 9, api_contract §3 (`load [--reset]`).
- **Finding:**
  - The generator writes `backend/keys/ISS-001-K1.pem`. If the platform has already created `ISS-001` (demo steps 1–8), generation **overwrites the platform's private key**. Platform credentials then fail signature verification, and loading fails on the issuer primary key.
  - Seed-derived keys can be recreated by anyone who knows the public seed, so synthetic credentials are forgeable by design.
- **Recommendation (decision D8, before T06):**
  - Store synthetic keys under `backend/keys/synthetic/<profile>/`, resolved by `record_source`.
  - Reserve a distinct ID range for synthetic issuers (e.g. `ISS-9nn`).
  - The loader refuses to load into a database that already holds conflicting IDs unless `--reset` is given.
  - Document that synthetic issuers are forgeable and must never be treated as real.

---

## 5. Constraint and index recommendations (consolidated)

This is the list proposed for decision D1. Everything here is compatible with A1–A4 injection
(§8). Items marked † come from MEDIUM or LOW findings and are optional.

### 5.1 Keys and foreign keys
| Table | Add | Finding |
|-------|-----|---------|
| issuers | `UNIQUE (issuer_id, signing_key_id)`; `UNIQUE (supersedes_issuer_id)`; `CHECK (supersedes_issuer_id IS NULL OR supersedes_issuer_id <> issuer_id)` | SR-02, SR-23 |
| credential_rules | `UNIQUE (rule_id, course_id)` | SR-02 |
| completion_records | `UNIQUE (completion_id, learner_id, course_id)` | SR-02 |
| credentials | Composite FKs to the three targets above, **replacing** the single-column FKs on `completion_id`, `rule_id`, `issuer_id`/`signing_key_id` | SR-02 |
| audit_events | `UNIQUE (previous_hash)`; `sqlite_autoincrement=True` | SR-06 |

### 5.2 Partial unique indexes (explicit names; `sqlite_where`)
- `uq_credentials_active_learner_course` on `credentials (learner_id, course_id) WHERE status = 'active'`. Verified working on 3.35.5. Name it explicitly; the `uq` naming convention applies only to `UniqueConstraint`, not to `Index`.
- `uq_credential_rules_active_course` on `credential_rules (course_id) WHERE status = 'active'`.

### 5.3 CHECK constraints (all explicitly named, as SR-16 requires)
| Table | CHECK | Finding |
|-------|-------|---------|
| issuers | `(status = 'retired') = (valid_to IS NOT NULL)`; `signing_key_id GLOB issuer_id \|\| '-K[0-9]*'` | SR-23 |
| learners | Exact ID pattern `GLOB 'LRN-[0-9][0-9][0-9][0-9][0-9][0-9][0-9]'`; `email = lower(email)` † | SR-31 |
| courses | Exact ID pattern † | SR-31 |
| credential_rules | `(status = 'draft') = (effective_from IS NULL)`; `status <> 'retired' OR effective_to IS NOT NULL`; `status <> 'active' OR effective_to IS NULL`; `status = 'draft' OR min_score IS NOT NULL OR min_attendance_pct IS NOT NULL`; `rule_id = 'RUL-' \|\| substr(course_id, 5) \|\| '-V' \|\| version` | SR-15 |
| credentials | Full Crockford pattern (`'CRED-'` + 12 × `[0-9A-HJKMNP-TV-Z]`); `length(payload_hash) = 64 AND payload_hash NOT GLOB '*[^0-9a-f]*'`; `length(signature) = 88` † | SR-31 |
| verification_requests | `result = CASE WHEN credential_id IS NULL THEN 'not_found' WHEN hash_match = 0 THEN 'tampered' WHEN signature_valid = 0 THEN 'invalid_signature' WHEN is_revoked = 1 THEN 'revoked' WHEN is_expired = 1 THEN 'expired' ELSE 'valid' END`; `credential_id IS NULL OR (hash_match IS NOT NULL AND signature_valid IS NOT NULL AND is_revoked IS NOT NULL AND is_expired IS NOT NULL)` | SR-14 |

The verification CHECK makes each stored result the BR-13 function of its own stored flags.
A3 and A4 remain representable because the faulty system records wrong *flags* (`is_revoked=0`,
`hash_match=1`). The detectors find them by recomputing the flags from source records.

### 5.4 Triggers (one module, re-created by any future batch migration)
| Trigger | Purpose | Status |
|---------|---------|--------|
| `trg_audit_events_no_update`, `trg_audit_events_no_delete` | Append-only | Existing spec |
| `trg_audit_events_chain_link` | Linear chain (SR-06) | **New** |
| `trg_revocations_no_update`, `trg_revocations_no_delete` | Immutable revocations | Existing spec |
| `trg_credentials_immutable` | Replaces `trg_credentials_payload_immutable` with a full-row rule (SR-07) | **Changed** |
| `trg_credentials_status_transition` | `active → revoked` only, with a matching revocation (SR-07) | **New** |
| `trg_credentials_no_delete` | | Existing spec |
| `trg_issuers_no_delete` | | Existing spec |
| `trg_issuers_immutable` | Only `status` (`active → retired`) and `valid_to` change (SR-23) | **New** † |
| `trg_credential_rules_immutable` | Criteria frozen once non-draft. Transitions only `draft → active → retired` (SR-15, BR-06 is a mandatory rule). | **New** |
| `trg_completion_records_locked` | BR-20 in the database: `WHEN EXISTS (SELECT 1 FROM credentials WHERE completion_id = OLD.completion_id AND status = 'active')` | **New** † |
| `trg_verification_requests_no_update`, `_no_delete` | Keep A3/A4 evidence stable (BR-18) | **New** † |

That makes 7 triggers in the current spec and 10–14 with these recommendations. Each is a few
lines of SQL; together they add roughly 10 minutes to T02.

### 5.5 Indexes
| Index | Reason | Finding |
|-------|--------|---------|
| `credentials (completion_id)`, `credentials (rule_id)`, `credentials (issuer_id)` | FK child columns are not auto-indexed in SQLite; A1 join; metrics | SR-32 |
| `completion_records (course_id)` | Course-first lookups and FK checks | SR-32 |
| `audit_events (event_type, reason_code)` | A2 detector, rejection listings | SR-32 |
| `verification_requests (credential_id, requested_at)` | Replaces the single-column `credential_id` index; A3 join | SR-32 |

---

## 6. SQLite-specific considerations

### 6.1 Verified behaviours that shape the design
| Topic | Consequence | Finding |
|-------|-------------|---------|
| Constraints cannot be altered; batch rebuild drops triggers (verified) | Finalise constraints in 0001. Re-create triggers in any batch migration. Test the trigger inventory. | SR-01, SR-17 |
| REPLACE skips delete triggers unless `recursive_triggers=ON` (verified) | Enable the pragma | SR-03 |
| pysqlite opens no transaction on `SELECT` (verified) | Use the `isolation_level=None` plus `begin` event recipe | SR-04 |
| SQLite 3.35.5 bundled | Partial indexes, CHECK with `CASE`, triggers, JSON1, window functions (useful for "latest completed attempt") and `RETURNING` are available. **`STRICT` tables (3.37+) are not**, so declared lengths and types are advisory. Use CHECKs where length matters (SR-31). | SR-31 |
| WAL plus `busy_timeout=30000` (configured) | With `BEGIN IMMEDIATE`, writers queue instead of failing with `SQLITE_BUSY_SNAPSHOT` | SR-04 |
| DATETIME stored as TEXT | Only one text format may exist, or comparisons break. See SR-10. | SR-10 |

### 6.2 Other considerations
- **SR-17 · MEDIUM · Recommendation.** `env.py` runs migrations on the application engine, which enables `PRAGMA foreign_keys=ON`. For future batch migrations of parent tables (`issuers`, `courses`, `credentials`), Alembic recommends turning foreign keys off during the rebuild, or the drop-and-rename can fail. *Recommendation:* in later migrations wrap batch operations with `PRAGMA foreign_keys=OFF` (outside a transaction) and run `PRAGMA foreign_key_check` afterwards. The initial migration is unaffected.
- **Sequence IDs.** `MAX(id)+1` is safe only inside `BEGIN IMMEDIATE` (SR-04). Zero-padded fixed width keeps lexical `MAX` correct; `LRN-` allows 9,999,999 learners.
- **`upper()`** is ASCII-only in SQLite, which is sufficient for Crockford base32.
- **REAL scores.** `score >= min_score` on 2-dp rounded REALs is reliable at this precision. Round on write, not on compare (LOW, SR-34).
- **JSON1** (verified available) lets the A2 detector read `json_extract(details_json, '$.attempt_ref')`.

---

## 7. Security and cryptographic concerns

| ID | Sev | Type | Affected | Finding | Recommendation |
|----|-----|------|----------|---------|----------------|
| SR-18 | MEDIUM | Contradiction | BR-22 ("detects any out-of-band edit … never silent"), ADR-009, architecture §7 | The hash chain is unkeyed and unanchored. Three things go undetected: deleting the newest events (the chain stays valid), recomputing the whole chain after an edit, and replacing the database file with an older copy. Triggers stop only application code; anyone with file access can `DROP TRIGGER`. | Reword BR-22 to "tamper-evident against edits that do not rewrite the entire chain." Use `sqlite_autoincrement` and check `sqlite_sequence.seq = MAX(event_id)` with no gaps in `verify-chain`, which catches naive truncation. Optional (deferred): record the head hash outside the database, e.g. in the anomaly-run output or a log line, or use an HMAC chain keyed from a key file. |
| SR-19 | MEDIUM | Contradiction | architecture §7 ("Database tampering of payloads → … hash recompute"), BR-11 integrity sweep | `payload_hash` is stored next to `payload_json`. An attacker who edits both consistently passes the hash check. Only the **Ed25519 signature** protects against database tampering, and only while the key file stays secret; it sits on the same machine. | State that the signature is the integrity control and the stored hash is a fast consistency check. Verification must always verify the signature against `issuers.public_key` (as architecture §5.2 already does), including lookup-only verification. |
| SR-13 | HIGH | Gap | ADR-005, AS-11 | Synthetic keys overwrite platform keys and are publicly forgeable | See §4 |
| SR-03 | HIGH | Defect | BR-22 | REPLACE bypass | See §4 |
| SR-39 | LOW | Recommendation | BR-08 | Unicode normalisation is unspecified. A name written in NFD and in NFC looks identical but hashes differently. | NFC-normalise strings when **building** a payload at issuance. Never normalise a presented payload during verification; that would accept altered bytes. |
| SR-35 | LOW | Recommendation | FR-15, BR-14 | The public, unauthenticated verification endpoint writes two rows per call, so audit-log growth is attacker-controlled. | Accepted locally (open question 4). Note it in the README as a production gap. |
| – | Info | – | ADR-004 | **Positive:** Ed25519 signatures are deterministic, so A1 re-signing and generator output stay byte-identical (BR-25). Embedding `signing_key_id` in the signed payload prevents key-substitution attacks. | None |
| – | Info | – | ADR-005 | Unencrypted PKCS8 PEM files are acceptable for local development only | Already documented |

---

## 8. Anomaly-injection feasibility review

| Anomaly | Stored where | Representable under the current spec? | Representable with the §5 constraints? | Requirements and risks |
|---------|--------------|----------------------------------------|----------------------------------------|------------------------|
| **A1** issued before completion | `credentials` + `completion_records` | Yes | **Yes.** The composite FK holds because the anomalous credential still references its own learner's completion. The status-transition trigger applies only to UPDATE. | Variant 1 must set `recorded_at ≥ completed_at` (CHECK, SR-40). Variant 2 needs `completed_at = NULL` with status `in_progress`/`failed`, which is allowed. The payload must be rebuilt and re-signed (deterministic). Completion corrections after revocation can create A1 false positives (SR-21). |
| **A2** duplicate credential ID attempt | `issuance_attempts.csv` → replay → `audit_events` | Partly | Yes | The faulty outcome (an overwrite) can never be stored, correctly. Detection relies on the platform's rejection audit events. **Needs SR-11:** duplicate check first, an `attempt_ref` in audit details, and an injected replay clock. The `case_variant` attempt is normalised to upper case before the uniqueness check; the database CHECK would otherwise reject the raw lower-case value as a different error. |
| **A3** revoked verified as valid | `verification_requests` + `revocations` | Yes | **Yes.** The flags/result CHECK holds because the injection sets `is_revoked=0` with `result='valid'`. | The loader must insert credentials in their final state (`status='revoked'` with `revoked_at` equal to the revocation's `revoked_at`) and then the revocations. Add a post-load consistency query (status revoked ⇔ revocation exists), because the status trigger does not cover INSERT. The variant names imply stale credential status, but the injection does not create it (SR-26). |
| **A4** altered payload not detected | `verification_requests.presented_payload_hash` | Yes | **Yes.** The injection records `hash_match=1` with `result='valid'`, which satisfies the precedence CHECK. | `presented_payload_hash` must be computed with the shared `canonical_json()` on the mutated payload. That requires SR-09; otherwise number formatting breaks hard negatives. |

**Conclusion:** none of the four anomalies requires weakening a production constraint, and all
remain representable after every recommendation in §5. Two further points:
- **SR-36 · LOW:** evaluation should restrict detector findings to `record_source='synthetic_import'` when scoring against a profile's ground truth. Otherwise platform-created demo records count as false positives.
- **SR-22 · MEDIUM · Gap:** the CSV columns omit required columns (`created_at`, `updated_at`, `created_by`, `issued_by`, `channel`). Specify loader defaults, e.g. `created_at` = the record's business timestamp, actors = `system`, `channel` = `api`. Also specify CSV writing as UTF-8 without BOM with `lineterminator="\n"`, for byte-identical output on Windows (FR-26), and loading in FK order.

---

## 9. Contradictions between documents (confirmed)

Each row quotes both sides. Recommendations are kept separate in §4 and §11.

| ID | Side A | Side B | Severity | Finding |
|----|--------|--------|----------|---------|
| CT-01 | BR-07 / AS-03 / ADR-007: rule in effect at `completed_at` | FR-04, US-03, api_contract §4.6: activation sets `effective_from = now`; api_contract §4.5 backdated completion; data_dictionary §5.1 backdated synthetic rules | HIGH | SR-08 |
| CT-02 | architecture §5.1: eligibility checked before duplicate ID | exception_catalogue A2: all variants (incl. `same_learner_retry`) yield `duplicate_credential_id` | HIGH | SR-11 |
| CT-03 | BR-04 #8: `already_issued` → `422 not_eligible` | exception_catalogue §1.4, api_contract §4.2, US-05: `409 credential_already_issued` | MEDIUM | SR-12 |
| CT-04 | data_dictionary §3.9: `sha256(previous_hash or "" + canonical(...))` | BR-22: `sha256(previous_hash + canonical(...))` | HIGH | SR-05 |
| CT-05 | BR-08 / ADR-004: payload timestamps at second precision | data_dictionary §1 / ADR-014: `UTCDateTime` stores microseconds for the same values | HIGH | SR-10 |
| CT-06 | BR-22: "detects any out-of-band edit … never silent"; architecture §7: hash recompute protects payloads | Capability: tail truncation, full recompute and stored-hash co-editing are undetectable | MEDIUM | SR-18, SR-19 |
| CT-07 | FR-22, BR-24: `data/ground_truth/anomaly_labels.csv` | data_dictionary §5, exception_catalogue §4: `data/ground_truth/<profile>/anomaly_labels.csv` | LOW | SR-25: use the per-profile path everywhere |
| CT-08 | data_dictionary §1: key ID "sequence per issuer" (`-K\d+`) | ADR-006: one key per issuer row; a new key means a new issuer | LOW | SR-27: fix `-K1`, or drop the suffix rule |
| CT-09 | BR-04 #9: `signing_key_unavailable` is an eligibility reason | exception_catalogue §1.4: `503 signing_key_unavailable` error | LOW | SR-29: report it in eligibility as a reason (`issuer_ready=false`); return 503 from issuance |
| CT-10 | BR-08: trigger covers `payload_json, payload_hash, signature` | data_dictionary §3.6 adds `signing_key_id, credential_id`; architecture §4 says "payload, hash, signature or ID" | LOW | Superseded by SR-07 (full-row rule) |
| CT-11 | AS-12: no hard deletes of learners, courses, issuers, rules, credentials, revocations | BR-18: also completion records, verifications, audit events | LOW | SR-28: align AS-12 to BR-18 |
| CT-12 | exception_catalogue A3 variant `status_not_updated` (stale `credentials.status`) | A3 injection mutates only verification rows | LOW | SR-26: rename the variant (e.g. `ignored_revocation`) or drop it |
| CT-13 | BR-04 reason `course_inactive` | data_dictionary §3.3 status value `archived` | LOW | SR-30: rename the reason `course_archived` |

---

## 10. Critical issues to resolve before implementation

### Decisions required before T02 (affect the schema or the T02 deliverables)

| Decision | Covers | Proposed resolution |
|----------|--------|---------------------|
| **D1** Final constraint set | SR-01, SR-02, SR-07, SR-14, SR-15, SR-23, SR-31, SR-32 | Approve §5. At minimum: composite FKs, the verification precedence CHECK, the rule-state CHECKs, the credential full-row and status-transition triggers, the chain-link trigger. |
| **D2** Timestamp precision | SR-10 | Whole seconds everywhere. One ISO `Z` formatter. The loader parses timestamps. |
| **D3** Transaction and trigger safety | SR-03, SR-04 | `recursive_triggers=ON`; the `isolation_level=None` + `BEGIN IMMEDIATE` recipe; map `IntegrityError` to 409. |
| **D4** Audit chain schema | SR-05, SR-06 | `sqlite_autoincrement`, `UNIQUE(previous_hash)`, chain-link trigger. The hash definition in SR-05 is implemented in T03. |

### Decisions required before the dependent task (no schema impact)

| Decision | Covers | Needed before | Proposed resolution |
|----------|--------|---------------|---------------------|
| **D5** Rule effective dates | SR-08 | T03 (eligibility) / T05 | Optional backdated `effective_from` on activation, with constraints; highest version wins |
| **D6** Payload value types | SR-09, SR-39 | T03 (crypto) | Score as a fixed 2-dp string; no floats; `allow_nan=False`; all keys present; NFC at issuance |
| **D7** Issuance contract | SR-11, SR-12 | T04 | Duplicate-ID check first; `attempt_ref` in audit details; injected clock for replay; `already_issued` → 409 |
| **D8** Synthetic isolation | SR-13, SR-22, SR-36 | T06 | Separate key directory and ID range; loader defaults; `--reset` required on conflict; evaluation filtered by `record_source` |

After these decisions, update the affected specification documents (data_dictionary,
business_rules, architecture, ADR-009/014, exception_catalogue, api_contract). That is outside
the scope of this review.

---

## 11. Minor improvements that can be deferred

| ID | Sev | Type | Affected | Recommendation |
|----|-----|------|----------|----------------|
| SR-14 | MEDIUM | Recommendation | data_dictionary §3.8 | Verification CHECKs and no-update/no-delete triggers. The CHECK is cheap now and costly later; include it in D1 if at all possible. |
| SR-15 | MEDIUM | Gap | data_dictionary §3.4, BR-06/07 | Detect overlapping or gapped rule windows. Service check on activation plus a test. Optional consistency query in the anomaly run. |
| SR-16 | MEDIUM | Gap | roadmap §5, ADR-002 | Alembic hygiene: (1) import every model module in `app/models/__init__.py` and assert `len(Base.metadata.tables) == 9`; (2) name every `CheckConstraint`, since unnamed ones crash autogenerate (verified); (3) hand-replace the rendered `UTCDateTime()` with `sa.DateTime()` in the migration, since the custom type is rendered without an import (verified) and migrations should not import app types; (4) add triggers with `op.execute` and drop them first in `downgrade()`; (5) use `--rev-id 0001`; (6) when tests call `command.upgrade`, `env.py`'s `fileConfig()` reconfigures logging. Guard it, e.g. skip when `config.attributes.get("skip_logging")`. |
| SR-17 | MEDIUM | Recommendation | ADR-002 | Future batch migrations: re-create triggers from the shared module, use `foreign_keys=OFF` during rebuilds, and run `foreign_key_check` afterwards. The trigger-inventory test guards against forgetting. |
| SR-21 | MEDIUM | Gap | AS-02, BR-15, BR-20 | Re-issue: (1) record `previous_credential_id` in `credential.issued` audit details when re-issuing (no schema change); (2) state that renewal of *expired* credentials is out of scope, because the partial index cannot use `now()` and expiry is scope-cut #5; (3) extend the BR-20 lock to completion records referenced by **any** credential, which avoids rewriting revoked evidence and A1 false positives. This changes an assumption, so the decision belongs to the product owner. |
| SR-22 | MEDIUM | Gap | data_dictionary §5.1, FR-25/26 | Loader defaults for NOT NULL columns, CSV encoding and line endings, FK load order (see §8) |
| SR-23 | MEDIUM | Recommendation | data_dictionary §3.1, BR-17 | Supersession uniqueness and self-reference CHECK; issuer mutable-column trigger; specify how a superseding issuer is created (`POST /issuers` with `supersedes_issuer_id`). This is missing from api_contract. |
| SR-24 | LOW | Gap | exception_catalogue §1.5 | Type `reason_code` as `str` in the request schema and validate in the service, so the rejection is audited as documented |
| SR-25…30 | LOW | Contradiction | see §9 | Wording alignments CT-07 … CT-13 |
| SR-31 | LOW | Recommendation | data_dictionary §1 | Exact ID-format CHECKs; hash and signature format CHECKs |
| SR-32 | LOW | Recommendation | data_dictionary §3 | Indexes in §5.5 |
| SR-33 | LOW | Recommendation | data_dictionary §3.2–3.3 | `email = lower(email)` CHECK; decide whether `course_code` is case-insensitive |
| SR-34 | LOW | Recommendation | data_dictionary §1 | Round scores to 2 dp on write; never compare rounded and unrounded values |
| SR-35 | LOW | Recommendation | FR-15 | Document the unauthenticated write amplification as a production gap |
| SR-36 | LOW | Recommendation | FR-22 | Filter evaluation by `record_source='synthetic_import'` |
| SR-37 | LOW | Recommendation | data_dictionary §3.9 | `eligibility.evaluated`: use `entity_type='learner'`, `entity_id=learner_id`, and put `course_id` (and the credential ID on success) in details |
| SR-38 | LOW | Recommendation | `env.py` | See SR-16 (6) |
| SR-39 | LOW | Recommendation | BR-08 | NFC normalisation at issuance only |
| SR-40 | LOW | Gap | exception_catalogue A1 | The generator also moves `recorded_at` for variant 1 |

---

## 12. Recommended implementation order for T02

Estimated effort is **about 65 minutes**, against the 50 budgeted in roadmap §1. If time is
short, defer the † items in §5; they are LOW or optional and are the cheapest to add later,
except for CHECKs.

| Step | Work | Output and checks | Est. |
|------|------|-------------------|------|
| 0 | Record decisions D1–D4 (§10) | Short decision note in the PR description or ADR addendum | 10 min |
| 1 | `database/session.py`: `recursive_triggers=ON`; `isolation_level=None` + `begin` event (`BEGIN IMMEDIATE` when `sqlite_write`); `WriteSessionLocal` | Tests: REPLACE on a protected table fails; a write session holds `RESERVED` (a second `BEGIN IMMEDIATE` times out quickly with a short `busy_timeout` in the test) | 8 min |
| 2 | `database/types.py`: `UTCDateTime` (aware-only, UTC, whole seconds) + `to_iso_z()` | Unit tests: naive input rejected, microseconds truncated, round trip | 5 min |
| 3 | `models/enums.py` (`class X(str, Enum)`) | – | 3 min |
| 4 | Models in FK dependency order: `issuer → learner → course → credential_rule → completion_record → credential → revocation → verification_request → audit_event` | Every `CheckConstraint` named. Composite UNIQUEs and FKs (SR-02). Partial unique indexes with explicit names. `sqlite_autoincrement=True` on `audit_events`. | 15 min |
| 5 | `models/__init__.py` imports all nine modules | Test: `len(Base.metadata.tables) == 9` | 1 min |
| 6 | `database/triggers.py`: `CREATE_TRIGGERS` / `DROP_TRIGGERS` lists (§5.4) | Single source for this and future migrations | 7 min |
| 7 | `alembic revision --autogenerate --rev-id 0001 -m "initial schema"`, then hand-edit | Replace `UTCDateTime()` with `sa.DateTime()`. Confirm the partial indexes and named CHECKs are rendered. Append `op.execute` triggers in `upgrade()`. Drop triggers first in `downgrade()`. | 6 min |
| 8 | Migration test fixture: temp DB + `command.upgrade(cfg, "head")` (logging guarded) | Tests: trigger inventory; FK violation; composite FK mismatch rejected; lower-case credential ID rejected; second active credential and second active rule rejected; re-issue after revoke accepted; `revoked → active` rejected; status change without a revocation row rejected; credential evidence update rejected; audit UPDATE, DELETE and REPLACE rejected; chain-link mismatch rejected; verification result inconsistent with flags rejected; `downgrade base` then `upgrade head` succeeds | 8 min |
| 9 | Health check on a migrated temp DB | `database: "initialized"` | 1 min |
| 10 | `alembic upgrade head` on the dev DB; full `python -m pytest` | Existing 9 tests and the new ones pass. The existing health test (unmigrated temp DB) still expects `not_initialized`. | 1 min |

---

## Summary

**Can T02 proceed?** Yes, after decisions **D1–D4** are recorded. They take about 15 minutes
and each has a concrete proposed answer in §10. The table inventory needs no change.

**Must be decided first (before T02):**
1. **D1:** the final constraint set (composite FKs for credential evidence and signing keys, the verification precedence CHECK, the rule-state CHECKs, full-row credential immutability and status-transition triggers). SQLite constraints cannot be changed later without a rebuild that drops triggers.
2. **D2:** whole-second UTC timestamps everywhere, with one formatter.
3. **D3:** `recursive_triggers=ON` and a working `BEGIN IMMEDIATE` recipe. Without them, append-only rows can be silently replaced and the eligibility, ID and audit-chain reads race.
4. **D4:** audit-chain schema support (`AUTOINCREMENT`, `UNIQUE(previous_hash)`, chain-link trigger).

**Must be decided before later tasks:** D5 rule effective dates (before T03), D6 payload value
types (before T03), D7 issuance check order and `attempt_ref` (before T04), D8 synthetic
issuer and key isolation (before T06).
