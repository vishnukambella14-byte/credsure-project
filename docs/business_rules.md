# CredSure: Business Rules

Each rule has a stable ID, a source tag, the layer that enforces it, and the test that must
prove it.

**Source tags:**
- `[Mandatory]`: stated in the business brief.
- `[Derived]`: necessary to make a mandatory rule implementable.
- `[Assumption]`: a working decision. See [PRD §9](PRD.md#9-assumptions).
- `[Decision Dn]`: settled by the pre-T02 decisions D1–D8 ([ADR-017 – ADR-024](architecture_decisions.md#pre-t02-decisions-d1d8)). It takes precedence over any earlier wording.

**Enforcement layers:**
- **DB**: a constraint, index or trigger in the Alembic migration.
- **SVC**: a service-layer check in `backend/app/services` or `validators`.
- **API**: a request schema or role check.
- **DET**: an anomaly detector.

---

## 1. Identity and uniqueness

<a id="br-01"></a>
### BR-01 Learners are identified by unique IDs, not names `[Mandatory]`
- `learner_id` is system-assigned (`LRN-` + 7 digits), the primary key, and immutable.
- Names are never unique and never used as lookup keys or in foreign keys. Two learners may share a full name.
- `email` and the optional `external_ref` (student number) are unique but are secondary identifiers.
- **Enforced:** DB (PK, UNIQUE, `CHECK learner_id GLOB 'LRN-[0-9]*'`), SVC (all lookups by ID), API (path params are IDs).
- **Test:** two learners with identical names are created and receive distinct IDs; credentials reference IDs.

<a id="br-02"></a>
### BR-02 Credential IDs are unique `[Mandatory]`
- Format: `CRED-` + 12 Crockford base32 characters, generated with `secrets` (platform) or a seeded RNG (generator).
- Stored in upper case. The service normalises a supplied ID to upper case. The format CHECK `ck_credentials_credential_id_format` accepts only `CRED-` plus 12 upper-case Crockford characters, so uniqueness is effectively case-insensitive.
- `[Decision D7]` A supplied ID is checked for format and uniqueness **before** eligibility is evaluated (BR-26). An ID that already exists is rejected with `409 duplicate_credential_id`, whatever the learner's eligibility. The first credential is never overwritten (plain `INSERT` only; D3). A `credential.issue_rejected` audit event carrying the attempt reference is written.
- **Enforced:** DB (PK + format CHECK), SVC (normalisation and pre-check, before eligibility), DET (A2).
- **Test:** issuing twice with the same ID (also with different letter case, and also for an ineligible learner) returns 409 `duplicate_credential_id`; the stored credential is unchanged; a rejection audit event with the attempt reference exists.

## 2. Eligibility and issuance

<a id="br-03"></a>
### BR-03 Credentials cannot be issued before eligibility is established `[Mandatory]`
- Issuance evaluates eligibility (BR-04) inside the same `BEGIN IMMEDIATE` write transaction as the insert (`[Decision D3]`, ADR-019). The order of checks is in BR-26.
- The credential stores the evidence used: `completion_id` and `rule_id`.
- If evaluation fails, nothing is inserted, the response is `422 not_eligible` with reasons, and a rejection is audited.
- **Enforced:** SVC, DET (A1 for imported data).
- **Test:** issuing for an in-progress completion, a below-threshold score, or a missing rule returns 422 and creates no row.

<a id="br-04"></a>
### BR-04 Eligibility criteria `[Derived]`
A learner L is eligible for course C at evaluation time T only if **all** of these hold:

| # | Check | Failure reason code |
|---|-------|---------------------|
| 1 | L exists and `status=active` | `learner_not_found` / `learner_inactive` |
| 2 | C exists and `status=active` | `course_not_found` / `course_inactive` |
| 3 | A completion record exists for (L, C) with `status=completed` (the latest completed attempt is used, AS-04) | `no_completed_record` |
| 4 | `completed_at` ≤ T (not in the future) | `completion_in_future` |
| 5 | A rule version of C was effective on or before `completed_at` and still in effect at that instant (BR-07) | `no_applicable_rule` |
| 6 | `score ≥ rule.min_score` (if `min_score` is set; a NULL score fails) | `score_below_minimum` |
| 7 | `attendance_pct ≥ rule.min_attendance_pct` (if set; NULL attendance fails) | `attendance_below_minimum` |
| 8 | No **active** credential exists for (L, C) (BR-05) | `already_issued` |
| 9 | C's issuer is `active` and its signing key is available | `issuer_inactive` / `signing_key_unavailable` |

All failing checks are returned, not just the first, so the UI can explain the decision.

**Terminology for check 8 (SR-12, resolved):**

| Layer | Name |
|-------|------|
| Internal eligibility outcome and reason code | `already_issued` |
| HTTP status | `409` |
| API error code | `credential_already_issued` |

When `already_issued` is among the failing reasons, issuance returns
`409 credential_already_issued`, even if other reasons are also present, and lists all
reasons in `details.reasons`. Otherwise a failed eligibility returns `422 not_eligible`. The
eligibility endpoint itself returns `200` with `eligible=false` and reason `already_issued`.

<a id="br-05"></a>
### BR-05 One active credential per learner per course `[Assumption]`
- **Enforced:** DB partial unique index `uq_credentials_active_learner_course` on `(learner_id, course_id) WHERE status='active'`. SVC check 8 in BR-04.
- Re-issue (for example after correcting an error) requires revoking the existing credential first.

<a id="br-06"></a>
### BR-06 Credential rule versions are preserved `[Mandatory]`
- Rules have `(course_id, version)` UNIQUE. The version starts at 1 and increments.
- Only `draft` rules can be edited. `active` and `retired` rules are immutable; changing criteria means creating a new draft version.
- Activating version N+1 atomically sets the previous active version to `status=retired` with `effective_to = new.effective_from`.
- At most one active rule per course: partial unique index on `(course_id) WHERE status='active'`.
- Each credential stores `rule_id`. The signed payload contains `rule_id` and `rule_version`.
- **Enforced:** DB (`trg_credential_rules_immutable`, rule-state CHECKs, partial unique index; `[Decision D1]`), SVC.
- **Test:** editing an active rule returns `409 rule_version_immutable`; activating v2 retires v1; a credential issued under v1 still references v1.

<a id="br-07"></a>
### BR-07 Applicable rule = the version in effect at completion time `[Decision D5]`
- **Selection:** the applicable rule is the version of the course with `status IN ('active','retired')` and `effective_from ≤ completed_at < COALESCE(effective_to, +∞)`. A version applies only if it was effective **on or before** the completion timestamp. Windows never overlap, so at most one version matches.
- A learner who completed under v1 is judged by v1 even if v2 is now active.
- **Activation date:** `effective_from` is optional and defaults to now. It may be backdated, but must not be in the future (`422 rule_effective_date_invalid`) and must be later than the predecessor's `effective_from`. The predecessor is retired with `effective_to = effective_from` in the same transaction.
- **Stability:** activating a rule is rejected with `409 rule_effective_date_conflict` (listing the completion IDs) if any recorded completion of the course has `completed_at ≥ effective_from`. A new or backdated rule therefore never changes the applicable rule of an already recorded completion. Windows are immutable afterwards.
- **No applicable rule:** the completion can still be recorded, and the response shows `applicable_rule_id: null` with warning `no_applicable_rule`. Eligibility fails with `no_applicable_rule`; issuance returns `422 not_eligible` (audited). There is no fallback to another version and no automatic rebinding.
- **Enforced:** SVC (pre-check with the conflict list), DB (`trg_credential_rules_effective_date_guard_update` / `_insert`, `ck_credential_rules_window`).
- **Test:** completion before v2 activation → v1 applies; backdated activation over a recorded completion → 409 and nothing changes; completion before v1 → `no_applicable_rule`.

<a id="br-08"></a>
### BR-08 Canonical payload and SHA-256 hash `[Mandatory]`
- The payload is built only from source records at issuance time. Its fields are listed in [data_dictionary §4](data_dictionary.md#4-canonical-credential-payload).
- Canonical form: `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` encoded as UTF-8. Every schema key is always present, with explicit `null` when empty.
- `[Decision D2]` Timestamps are `YYYY-MM-DDTHH:MM:SSZ` (UTC, whole seconds) from the shared `format_utc`.
- `[Decision D6]` Scores are fixed-point decimal strings with two decimals (`"82.50"`), or `null`. **No floating-point value may appear in a signed payload**; `canonical_json()` raises on any `float`.
- `payload_hash = sha256(canonical_bytes).hexdigest()` (64 lowercase hex characters).
- `payload_json` is stored as the exact canonical **string**, not as a JSON column, so the bytes never change.
- **Enforced:** SVC (one shared function used by the platform, generator and detectors), DB (`trg_credentials_immutable` freezes every column except `status` and `revoked_at`; `[Decision D1]`).
- **Test:** a payload whose score is `80` round-trips through a JSON client unchanged as `"80.00"`; `canonical_json({"score": 80.0})` raises.

<a id="br-09"></a>
### BR-09 Ed25519 digital signature `[Mandatory]`
- `signature = base64(Ed25519.sign(issuer_private_key, canonical_bytes))`.
- `signing_key_id` identifies the issuer key. The public key is stored on the issuer row.
- **Enforced:** SVC.
- **Test:** the signature verifies with the issuer public key; changing one byte of the payload makes verification fail.

<a id="br-10"></a>
### BR-10 Private keys are never exposed `[Derived]`
- Private keys live in `backend/keys/platform/<signing_key_id>.pem` (platform issuers) or `backend/keys/synthetic/<profile>/<signing_key_id>.pem` (synthetic issuers, BR-27). Both are git-ignored. Keys are never stored in the database, logged, or returned by the API. New platform key files are created in exclusive mode and never overwritten.
- A missing key file returns `503 signing_key_unavailable`. It never falls back to an unsigned credential.

## 3. Verification

<a id="br-11"></a>
### BR-11 Altered signed payloads must be detected `[Mandatory]`
- Verification always recomputes the SHA-256 hash of the payload: the presented payload if given, otherwise the stored payload.
- A hash different from the stored `payload_hash` returns `tampered`, never `valid`.
- The signature is then verified against the issuer public key. Failure returns `invalid_signature`.
- **Enforced:** SVC, DET (A4).
- **Test:** a presented payload with a changed `score` returns `tampered`; a stored payload altered behind the triggers (test bypass) also returns `tampered`.

<a id="br-12"></a>
### BR-12 Revoked credentials cannot verify as valid `[Mandatory]`
- Verification checks the `revocations` table **and** `credentials.status`. Either one marks the credential revoked.
- `[Decision D1]` The stored verification `result` must equal the BR-13 precedence function of the stored flags (`ck_verification_requests_result_precedence`). Verification records are immutable.
- **Enforced:** SVC, DB, DET (A3).
- **Test:** after revocation, verification returns `revoked` even if the denormalised status is manually reset in a test.

<a id="br-13"></a>
### BR-13 Verification result precedence `[Derived]`
All checks run and are stored as flags. The single `result` uses the first matching state in this order:

`not_found` → `tampered` → `invalid_signature` → `revoked` → `expired` → `valid`

`valid` is returned only when the credential exists, the hash matches, the signature is valid,
it is not revoked, and it is not expired.

<a id="br-14"></a>
### BR-14 Every verification is recorded `[Derived]`
- Every request, including `not_found`, creates a `verification_requests` row and a `verification.performed` audit event.
- `submitted_credential_id` keeps the raw input even when it matches no credential.

<a id="br-15"></a>
### BR-15 Expiry `[Assumption]`
- If the applied rule has `validity_days`, then `expires_at = issued_at + validity_days`. Verification after `expires_at` returns `expired`.
- Expiry is derived. No job changes stored status.

## 4. Revocation

<a id="br-16"></a>
### BR-16 Revocation requires authority and a reason `[Assumption]`
- Only `academic_admin` can revoke.
- `reason_code` must be one of: `issued_in_error`, `academic_misconduct`, `learner_request`, `data_correction`, `fraud`, `other`.
- `reason_text` is required when the code is `other`.
- At most one revocation per credential (`credential_id` UNIQUE). Revoking twice returns `409 credential_already_revoked`.
- `[Decision D1]` **Atomic:** one `BEGIN IMMEDIATE` transaction does three things in order: inserts the `revocations` row, performs the only permitted credential transition `active → revoked` (with `revoked_at` equal to the revocation's `revoked_at`), and inserts the `credential.revoked` audit event. A failure at any step rolls back all three.
- `[Decision D1]` **Irreversible:** a revoked credential can never be reinstated. There is no API for it, `revocations` rows cannot be updated or deleted, and `trg_credentials_status_transition` rejects every status change except `active → revoked` backed by a matching revocation. Re-issue creates a new credential (BR-05).
- **Enforced:** SVC, DB (UNIQUE, `trg_credentials_status_transition`, `trg_revocations_no_update`/`_no_delete`).
- **Test:** a non-admin gets 403; a missing reason gets 422; a second revocation gets 409; both tables are consistent after success; a direct `UPDATE credentials SET status='active'` on a revoked credential fails; a status change without a revocation row fails.

## 5. Issuers and history

<a id="br-17"></a>
### BR-17 Historical issuer information is retained `[Mandatory]`
- Issuer rows are never deleted. Only `status` (`active` → `retired`) and `valid_to` may change.
- Changes to name or key create a new issuer row with `supersedes_issuer_id` pointing to the old one. Existing courses may be re-pointed; existing credentials keep their original `issuer_id`.
- The signed payload embeds `issuer_id`, `issuer_name` and `signing_key_id`, which provides a tamper-evident snapshot.
- A retired issuer cannot issue, but its public key stays available, so its credentials keep verifying.
- `[Decision D1]` A credential's `(issuer_id, signing_key_id)` must reference the same issuer row (composite FK). An issuer is superseded at most once (`supersedes_issuer_id` UNIQUE).
- **Enforced:** DB (`ON DELETE RESTRICT`, `trg_issuers_no_delete`, `trg_issuers_immutable`, composite FK), SVC.

<a id="br-18"></a>
### BR-18 No hard deletes of core records `[Assumption]`
- Learners, courses, issuers, rules, completion records, credentials, revocations, verifications and audit events are never deleted through the application.
- Deactivation uses `status`. All foreign keys are `ON DELETE RESTRICT`.

## 6. Completion records

<a id="br-19"></a>
### BR-19 Completion data validity `[Derived]`
- `score` and `attendance_pct` are within 0–100 (inclusive) or NULL.
- `status=completed` requires `completed_at`. `completed_at` must not be later than the time it was recorded.
- `(learner_id, course_id, attempt_number)` is unique.
- **Enforced:** DB (CHECKs, UNIQUE), API (Pydantic), SVC.

<a id="br-20"></a>
### BR-20 Completion records referenced by an active credential are locked `[Assumption]`
- An update to a completion record with an active credential returns `409 completion_locked`.
- Allowed corrections write `completion.corrected` with `before` and `after` in `details_json`, including the `applicable_rule_id` before and after (`[Decision D5]`), so a change of applicable rule is never silent.
- **Enforced:** SVC, DB (`trg_completion_records_locked`; composite FK `ON UPDATE RESTRICT` on `learner_id`/`course_id`).

## 7. Audit

<a id="br-21"></a>
### BR-21 Important decisions create audit events `[Mandatory]`
Required event types are listed in [data_dictionary §3.9](data_dictionary.md#39-audit_events).
At minimum the following are audited:
- creation or activation of master data and rules
- completion recorded or corrected
- eligibility evaluated as part of issuance
- credential issued, or issuance rejected
- credential revoked, or revocation rejected
- every verification
- permission denied
- dataset imported
- anomaly run completed

Success events are written in the **same transaction** as the change. Rejection events are
written in a separate transaction after rollback, so the rejection survives.

<a id="br-22"></a>
### BR-22 Historical audit events cannot be silently overwritten `[Mandatory]` `[Decision D4]`
- `audit_events` is append-only. SQLite triggers `BEFORE UPDATE` and `BEFORE DELETE` call `RAISE(ABORT, 'audit_events is append-only')`. `PRAGMA recursive_triggers=ON` on every connection stops `INSERT OR REPLACE` from bypassing them (D3), and the application only ever uses plain `INSERT`.
- **Single linear chain, enforced by the database:**
  - `event_id` is `AUTOINCREMENT`.
  - `previous_hash` is UNIQUE, so no two events extend the same predecessor.
  - `uq_audit_events_single_genesis` allows exactly one genesis event (`previous_hash IS NULL`).
  - `trg_audit_events_chain_link` rejects any insert whose `previous_hash` is not the newest event's hash.
- **Hash:** `event_hash = sha256_hex(canonical_json({...event fields..., "previous_hash": ...}))`, with the exact field list in [data_dictionary §3.9](data_dictionary.md#39-audit_events).
- **Serialised writes:** audit events are written only inside `BEGIN IMMEDIATE` transactions that read the chain head in the same transaction. Rejection events get their own `BEGIN IMMEDIATE` transaction.
- **Detection scope:** the chain-verification endpoint detects application-level tampering, and edits made with the triggers bypassed that do not rewrite the whole chain. It also checks `sqlite_sequence` against `MAX(event_id)` for naive tail deletion. It does **not** protect against an attacker with unrestricted access to the database file, who can rewrite the whole chain, truncate it consistently, or restore an older file ([ADR-020](architecture_decisions.md#adr-020-database-enforced-linear-audit-chain)).
- **Test:** UPDATE, DELETE and REPLACE on `audit_events` raise an error; a second genesis event and a fork from a non-head event are rejected; tampering with a row (triggers dropped in the test) is reported by chain verification at the correct `event_id`.

## 8. Metrics and data

<a id="br-23"></a>
### BR-23 Derived metrics come from source records `[Mandatory]`
- Every metric is computed on request with SQL over the source tables. There is no metrics table, cached counter or hand-entered figure.
- Each metric response includes `computed_at` and the definition (numerator and denominator) used.

<a id="br-24"></a>
### BR-24 Ground truth is kept separate from input features `[Mandatory]`
- Labels are only written to `data/ground_truth/anomaly_labels.csv`. Input CSVs have no label, injection or "is_anomaly" columns.
- Labels are never imported into database tables. Detectors never read the ground-truth directory; only the evaluation step does.

<a id="br-25"></a>
### BR-25 Synthetic data is reproducible and valid first `[Mandatory]`
- One `random.Random(20261022)` instance drives all choices. Timestamps are relative to the fixed reference instant `2026-10-22T00:00:00Z`, never the wall clock.
- The generator produces a fully valid dataset, then copies and mutates selected records to inject anomalies.
- Same seed and profile give byte-identical files (verified by the manifest's SHA-256 checksums).

## 9. Issuance attempts, synthetic isolation and time

<a id="br-26"></a>
### BR-26 Every issuance attempt is traceable, and duplicate IDs are checked first `[Decision D7]`
- Every issuance attempt has a unique `attempt_ref`. It is supplied by the caller (it must be unused, else `409 duplicate_attempt_ref`) or generated (`ATT-` + 12 Crockford characters). Synthetic replay uses the dataset's `attempt_id`.
- Order of checks inside one `BEGIN IMMEDIATE` transaction:
  1. Schema.
  2. `attempt_ref` uniqueness.
  3. **Credential ID format and uniqueness** (`409 duplicate_credential_id`).
  4. Eligibility (BR-04).
  5. Sign.
  6. Insert.
- Every attempt that passes step 2 must end with exactly one terminal audit event, `credential.issued` or `credential.issue_rejected`, with `audit_events.attempt_ref` set. The guarantee is split between two layers:
  - The **database** enforces **at most one** terminal event per `attempt_ref` (`uq_audit_events_attempt_outcome`), and requires the reference on those event types (`ck_audit_events_attempt_ref_required`).
  - The **application's transaction logic** ensures that every recorded attempt receives one: the success event commits with the credential; on rejection or insert failure, the rollback is followed by the rejection event in its own transaction.
- **Enforced:** SVC (exactly one), DB (at most one; reference required).
- **Test:** a duplicate-ID attempt for an ineligible learner yields `duplicate_credential_id`; the rejection event carries the attempt reference; a second terminal event for the same reference is rejected by the database; a simulated insert failure still leaves a rejection event.

<a id="br-27"></a>
### BR-27 Synthetic issuers and keys are isolated from platform issuers `[Decision D8]`
- Platform issuers use IDs `ISS-001`–`ISS-899`. Synthetic issuers use `ISS-901`–`ISS-999` with `record_source='synthetic_import'` (CHECK `ck_issuers_synthetic_range`).
- Synthetic private keys live only under `backend/keys/synthetic/<profile>/`. The generator never opens, reads, writes, reuses or deletes anything under `backend/keys/platform/`, and never emits issuers outside the synthetic range. Platform key files are created in exclusive mode.
- Synthetic keys are derived from the public seed, so they are forgeable and for development only.
- **Enforced:** DB (CHECK), SVC (key-directory selection by `record_source`, path containment check in the generator, loader accepts synthetic-range issuers only).
- **Test:** generating a dataset leaves every file in the platform key directory byte-identical; the loader rejects a platform-range issuer row; inserting a platform issuer in the synthetic range fails.

<a id="br-28"></a>
### BR-28 Timestamps are UTC with whole-second precision `[Decision D2]`
- Every persisted timestamp and every signed-payload timestamp is UTC, truncated to whole seconds.
- One module (`app/core/timeutil.py`: `utc_now`, `to_utc`, `format_utc`, `parse_utc`) is used by the API, services, generator, loader and verification.
- Naive datetimes are rejected. Fractional seconds on input are truncated.
- **Enforced:** SVC (shared module, `UTCDateTime` type, Pydantic type).
- **Test:** `credentials.issued_at` equals the payload `issued_at`; a naive datetime is rejected; `2026-03-14T10:00:00.999Z` is stored as `2026-03-14 10:00:00`.

## 10. Traceability summary

| Mandatory rule (brief) | Business rules | Detector | Key test |
|------------------------|----------------|----------|----------|
| No issuance before eligibility | BR-03, BR-04 | A1 | `test_issue_rejected_when_not_eligible` |
| Unique credential IDs | BR-02 | A2 | `test_duplicate_credential_id_rejected` |
| Learners identified by unique IDs | BR-01 | – | `test_same_name_learners_distinct_ids` |
| Rule versions preserved | BR-06, BR-07 | – | `test_credential_keeps_original_rule_version` |
| Revoked never valid | BR-12, BR-13, BR-16 | A3 | `test_verify_revoked_returns_revoked` |
| Altered payload detected | BR-08, BR-09, BR-11 | A4 | `test_verify_tampered_payload` |
| Issuer history retained | BR-17 | – | `test_retired_issuer_credentials_still_verify` |
| Important decisions audited | BR-21 | – | `test_issue_writes_audit_event` |
| Audit not silently overwritten | BR-22 | – | `test_audit_update_blocked`, `test_chain_detects_tamper` |
| Metrics from source records | BR-23 | – | `test_metrics_match_source_counts` |

### Pre-T02 decisions

| Decision | Business rules |
|----------|----------------|
| D1 Final constraints; atomic, irreversible revocation | BR-06, BR-08, BR-12, BR-16, BR-17, BR-20 |
| D2 Whole-second UTC timestamps | BR-08, BR-28 |
| D3 SQLite transaction configuration | BR-02, BR-03, BR-22 |
| D4 Linear audit chain | BR-22 |
| D5 Rule effective dates | BR-04 (#5), BR-07, BR-20 |
| D6 Fixed-point score strings | BR-08 |
| D7 Duplicate-ID check first; traceable attempts | BR-02, BR-26 |
| D8 Synthetic issuer isolation | BR-10, BR-27 |
