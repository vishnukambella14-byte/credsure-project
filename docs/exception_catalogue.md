# CredSure: Exception Catalogue

This catalogue covers three kinds of exception:
1. **API errors**: requests the platform rejects.
2. **Verification outcomes**: negative results that are *not* errors.
3. **Data anomalies**: faulty records that the detectors must find.

## 1. API errors

All errors use the existing envelope from `backend/app/core/errors.py`:

```json
{"error": {"code": "not_eligible", "message": "Learner is not eligible for this credential.", "details": {"reasons": ["score_below_minimum"]}}}
```

Codes are lowercase snake_case. The column **Audited** shows whether the rejection writes an
audit event (`outcome=rejected`).

### 1.1 Generic

| Code | HTTP | Trigger | Audited |
|------|------|---------|:-------:|
| `validation_error` | 422 | Request body or query fails the Pydantic schema (existing handler) | – |
| `http_404` | 404 | Unknown route (existing handler) | – |
| `actor_required` | 401 | Protected endpoint called without `X-Actor-Id`/`X-Actor-Role`, or with an unknown role | – |
| `permission_denied` | 403 | Role not allowed for this action, or a learner/instructor outside their scope | ✓ `access.denied` |
| `internal_server_error` | 500 | Unhandled exception (existing handler; logged with stack trace) | – |

### 1.2 Master data

| Code | HTTP | Trigger |
|------|------|---------|
| `learner_not_found` / `course_not_found` / `issuer_not_found` / `rule_not_found` / `completion_not_found` / `credential_not_found` | 404 | Unknown ID in path or body |
| `learner_email_exists` | 409 | Duplicate learner email |
| `learner_external_ref_exists` | 409 | Duplicate student number |
| `course_code_exists` | 409 | Duplicate `course_code` |
| `issuer_inactive` | 422 | Course created or re-pointed to a retired issuer |
| `issuer_immutable` | 409 | Attempt to change issuer fields other than status or `valid_to` (BR-17) |
| `rule_version_immutable` | 409 | Edit of an `active` or `retired` rule (BR-06) |
| `rule_not_draft` | 409 | Activating a rule that is not `draft` |
| `rule_has_no_criteria` | 422 | Activating a rule with neither `min_score` nor `min_attendance_pct` |
| `rule_effective_date_invalid` | 422 | Activation `effective_from` is in the future, or not later than the current active version's `effective_from` (D5) |
| `rule_effective_date_conflict` | 409 | Activation would change the applicable rule of an already recorded completion: a completion of the course has `completed_at ≥ effective_from`. `details.conflicting_completion_ids` lists them (D5). |

### 1.3 Completion records

| Code | HTTP | Trigger |
|------|------|---------|
| `completion_invalid_state` | 422 | `status=completed` without `completed_at`, or score/attendance outside 0–100 |
| `completion_in_future` | 422 | `completed_at` later than the current time |
| `completion_attempt_exists` | 409 | Duplicate (`learner_id`, `course_id`, `attempt_number`) |
| `completion_locked` | 409 | Correction of a record referenced by an active credential (BR-20). Audited as `completion.correction_rejected`. |
| `course_scope_denied` | 403 | Instructor recording for a course with a different `lead_instructor_id` |

### 1.4 Issuance

Checks run in this order (D7, BR-26): schema → `attempt_ref` → credential ID → eligibility →
signing → insert. The first failing check determines the error. Every audited rejection carries
`attempt_ref`.

| Code | HTTP | Trigger | Audited |
|------|------|---------|:-------:|
| `duplicate_attempt_ref` | 409 | Supplied `attempt_ref` already has a terminal audit event (D7). The request is not a new attempt. | – (logged) |
| `duplicate_credential_id` | 409 | Supplied credential ID already exists (case-insensitive, BR-02). Checked **before** eligibility, so it is reported even for ineligible learners. | ✓ `credential.issue_rejected` |
| `not_eligible` | 422 | Eligibility failed. `details.reasons` lists the BR-04 reason codes (including `no_applicable_rule`, D5). | ✓ `credential.issue_rejected` |
| `credential_already_issued` | 409 | An active credential exists for (learner, course) (BR-05). Raised whenever the internal eligibility reason `already_issued` is present, even alongside other reasons, which are all listed in `details.reasons` (SR-12). | ✓ `credential.issue_rejected` |
| `issuer_inactive` | 422 | Course issuer is retired | ✓ |
| `signing_key_unavailable` | 503 | Private key file missing or unreadable. The credential is never issued unsigned (BR-10). | ✓ outcome `error` |

### 1.5 Revocation

| Code | HTTP | Trigger | Audited |
|------|------|---------|:-------:|
| `credential_already_revoked` | 409 | Credential has already been revoked (BR-16) | ✓ `credential.revoke_rejected` |
| `revocation_reason_required` | 422 | Missing or unknown `reason_code`, or `other` without `reason_text` | ✓ |

There is no reinstatement operation (D1). Any direct attempt to move a credential out of
`revoked` is rejected by `trg_credentials_status_transition` and surfaces as `record_immutable`.

### 1.6 Audit and data operations

| Code | HTTP | Trigger |
|------|------|---------|
| `audit_immutable` | 409 | Any API attempt to modify or delete audit events. No such endpoint exists; this code maps a SQLite trigger `ABORT` raised as an `IntegrityError`. |
| `record_immutable` | 409 | An immutability trigger blocked a change to a credential (anything but `active → revoked`), revocation, verification request, issuer, or non-draft rule |
| `database_busy` | 503 | The write lock could not be obtained within `busy_timeout`, or `trg_audit_events_chain_link` rejected an insert (serialised writes make this a defect, never an expected path; logged as an error) |
| `ground_truth_unavailable` | 404 | Evaluation requested but the profile's `anomaly_labels.csv` is missing |

### 1.7 Database constraint mapping

Decision D3 ([ADR-019](architecture_decisions.md#adr-019-sqlite-transaction-configuration)):
the application uses plain `INSERT` and maps every `IntegrityError` explicitly by constraint or
trigger name. Unmapped integrity errors are logged and returned as `500 internal_server_error`.

| Constraint or trigger (data_dictionary §3, §6) | Domain error |
|-----------------------------------------------|--------------|
| `credentials` PK, `ck_credentials_credential_id_format` | `409 duplicate_credential_id` / `422 validation_error` |
| `uq_credentials_active_learner_course` | `409 credential_already_issued` |
| `uq_audit_events_attempt_outcome` | `409 duplicate_attempt_ref` |
| `revocations.credential_id` UNIQUE | `409 credential_already_revoked` |
| `uq_credential_rules_active_course`, `trg_credential_rules_immutable` | `409 rule_version_immutable` |
| `trg_credential_rules_effective_date_guard_*`, `ck_credential_rules_window` | `409 rule_effective_date_conflict` / `422 rule_effective_date_invalid` |
| `trg_completion_records_locked` | `409 completion_locked` |
| `uq_completion_records_attempt` | `409 completion_attempt_exists` |
| `learners.email`, `learners.external_ref`, `courses.course_code` UNIQUE | `409 learner_email_exists` / `learner_external_ref_exists` / `course_code_exists` |
| `trg_credentials_immutable`, `trg_credentials_status_transition`, `trg_revocations_*`, `trg_issuers_immutable`, `trg_verification_requests_*` | `409 record_immutable` |
| `trg_audit_events_no_update` / `_no_delete` | `409 audit_immutable` |
| `trg_audit_events_chain_link`, `uq_audit_events_previous_hash`, `uq_audit_events_single_genesis` | `503 database_busy` (logged as a defect) |
| `ck_issuers_synthetic_range` | `422 validation_error` (platform path) / loader abort (synthetic path) |
| Composite FK violations on `credentials` | `500 internal_server_error` (service bug; the service resolves evidence itself) |

## 2. Verification outcomes (not errors)

`POST /api/v1/verifications` always returns **HTTP 200** for a well-formed request, even when
the credential is invalid. Verifiers receive a result, not an error.

| Result | Meaning | Condition (precedence BR-13) | Verifier message |
|--------|---------|------------------------------|------------------|
| `not_found` | No credential with this ID | Normalised ID not in `credentials` | "No credential with this ID was issued by CredSure." |
| `tampered` | Payload differs from what was signed | `sha256(canonical(payload)) ≠ credentials.payload_hash` | "The credential data has been altered." |
| `invalid_signature` | Signature does not match issuer key | Ed25519 verify fails with the issuer public key | "The credential signature is invalid." |
| `revoked` | Credential was revoked | A `revocations` row exists **or** `status='revoked'` (at the request time) | "This credential was revoked on {date}." Reason codes are not disclosed publicly. |
| `expired` | Past its expiry date | `expires_at IS NOT NULL AND requested_at > expires_at` | "This credential expired on {date}." |
| `valid` | All checks passed | Otherwise | "This credential is authentic and active." |

## 3. Data anomaly catalogue

Each anomaly simulates a fault in a legacy or faulty system. It exists **only** in synthetic
data (see [PRD C-01](PRD.md#10-contradictions-and-how-they-are-resolved)). The live platform
must block or correctly answer the same situation, and tests prove it.

Detectors are deterministic SQL/Python functions in `backend/app/services/anomalies/`. They
recompute the correct outcome independently from source records and flag records where the
stored outcome disagrees. They never read ground truth.

### A1 Credential issued before completion

| Aspect | Specification |
|--------|---------------|
| **Ground-truth label** | `anomaly_type = issued_before_completion` · `record_type = credential` |
| **Input conditions** | A credential linked through `completion_id` to a completion record where the learner had not completed the course when the credential was issued. |
| **Variants** | `issued_before_completed_at`: `issued_at < completed_at` (completion status `completed`). `completion_not_completed`: evidence record status is `in_progress`, `failed` or `withdrawn` (`completed_at` NULL). |
| **Correct expected result** | Issuance is refused with `422 not_eligible` (`no_completed_record` or `completion_in_future`). No credential exists. |
| **Faulty behaviour** | The credential exists with `status=active` and verifies as `valid`. |
| **Detection logic** | `SELECT c.credential_id FROM credentials c JOIN completion_records cr ON cr.completion_id = c.completion_id WHERE cr.status <> 'completed' OR cr.completed_at IS NULL OR c.issued_at < cr.completed_at` |
| **Generator injection** | Take a valid credential. Variant 1: move `completed_at` to `issued_at + U(1..30) days`, and move `recorded_at` to at least `completed_at` (CHECK `ck_completion_records_recorded_after`). Variant 2: set completion `status` to `in_progress` or `failed` and `completed_at = NULL`. Rebuild and re-sign the payload with the synthetic issuer key (D8) so A1 is not mistaken for A4. Ed25519 signatures are deterministic, so output stays reproducible. The mutated completion must still fall in a valid rule window (D5); the generator keeps it within the same version's window. |
| **Hard negatives** | Credentials issued within minutes or hours **after** `completed_at`. |
| **Live-platform test** | Issuing against an `in_progress` completion returns 422 and creates no row. |

### A2 Duplicate credential ID attempt

| Aspect | Specification |
|--------|---------------|
| **Ground-truth label** | `anomaly_type = duplicate_credential_id` · `record_type = issuance_attempt` |
| **Input conditions** | A row in `issuance_attempts.csv` whose `credential_id` already belongs to an existing credential (for a different learner or course, or the same one). The ID may differ only in letter case. |
| **Variants** | `exact_duplicate`, `case_variant` (e.g. `cred-7k2m…`), `same_learner_retry` |
| **Correct expected result** | `409 duplicate_credential_id` for **every** variant: the credential ID is checked before eligibility (D7), so an ineligible learner or an existing active credential cannot mask the duplicate. The existing credential is unchanged. Exactly one `credential.issue_rejected` audit event with `reason_code = duplicate_credential_id` and `attempt_ref = <attempt_id>`. |
| **Faulty behaviour** | The second issuance overwrites the first credential (upsert), or two credentials share an ID (for example IDs that differ only in case). |
| **Detection logic** | (1) From the audit log: `SELECT attempt_ref FROM audit_events WHERE event_type = 'credential.issue_rejected' AND reason_code = 'duplicate_credential_id'`. Each `attempt_ref` is a finding (`record_type = issuance_attempt`, `record_id = attempt_ref`). (2) Defensive check on stored data: `SELECT upper(credential_id) FROM credentials GROUP BY 1 HAVING COUNT(*) > 1` must return nothing. (3) Overwrite check: the credential's `payload_hash` recorded in its `credential.issued` event equals its current `payload_hash`. |
| **Generator injection** | Pick existing credential IDs and write attempts that reuse them. The 50 valid attempts in the same file use fresh IDs for eligible learners (hard negatives). The replay passes `attempt_id` as `attempt_ref` and uses an injected clock set to `attempted_at` (D7). |
| **Live-platform test** | The same ID in different case is rejected with 409, and the original row is byte-identical afterwards. |

### A3 Revoked credential incorrectly verified as active

| Aspect | Specification |
|--------|---------------|
| **Ground-truth label** | `anomaly_type = revoked_verified_as_valid` · `record_type = verification_request` |
| **Input conditions** | A verification request for a credential with a revocation where `revoked_at ≤ requested_at`. |
| **Variants** | `status_not_updated` (the faulty system checked only a stale status flag), `cache_stale` (verified seconds or days after revocation) |
| **Correct expected result** | `result = revoked`, `is_revoked = 1`. |
| **Faulty behaviour** | `result = valid` (and `is_revoked = 0`). |
| **Detection logic** | `SELECT v.verification_id FROM verification_requests v JOIN revocations r ON r.credential_id = v.credential_id WHERE r.revoked_at <= v.requested_at AND v.result = 'valid'` |
| **Generator injection** | Take verification requests of revoked credentials made after `revoked_at` and set `result='valid'`, `is_revoked=0`. These flags satisfy `ck_verification_requests_result_precedence` (D1). The credential and revocation rows stay consistent (status `revoked`, matching `revoked_at`); only the verification record is faulty. |
| **Hard negatives** | Verifications made **before** `revoked_at` with result `valid` (correct). Verifications after revocation with result `revoked` (correct). |
| **Live-platform test** | Revoke, then verify: the result is `revoked`. Also run the test with `credentials.status` forced back to `active` (revocations table authoritative). |

### A4 Altered payload hash not detected

| Aspect | Specification |
|--------|---------------|
| **Ground-truth label** | `anomaly_type = tampered_payload_not_detected` · `record_type = verification_request` |
| **Input conditions** | A verification request whose `presented_payload_hash` differs from the referenced credential's `payload_hash`. The verifier presented a modified payload, such as a changed score, name, course or expiry date. |
| **Variants** | `score_changed`, `name_changed`, `course_changed`, `expiry_extended` |
| **Correct expected result** | `result = tampered`, `hash_match = 0`. |
| **Faulty behaviour** | `result = valid` (and `hash_match = 1` recorded incorrectly). |
| **Detection logic** | `SELECT v.verification_id FROM verification_requests v JOIN credentials c ON c.credential_id = v.credential_id WHERE v.presented_payload_hash IS NOT NULL AND v.presented_payload_hash <> c.payload_hash AND v.result = 'valid'`. Supplementary integrity sweep: recompute `sha256(payload_json)` for every credential and flag any mismatch with `payload_hash` (catches storage-level tampering). |
| **Generator injection** | Build a modified payload with the shared `canonical_json()` (score as a fixed-point string, D6; timestamps via `format_utc`, D2), compute its hash into `presented_payload_hash`, and set `result='valid'`, `hash_match=1`. These flags satisfy `ck_verification_requests_result_precedence`. |
| **Hard negatives** | Tampered presentations recorded correctly as `tampered`. Untampered presentations (`presented_payload_hash = payload_hash`) recorded `valid`. |
| **Live-platform test** | Present a payload with the `score` changed: the result is `tampered`. |

### Optional A5 (P2): issued without meeting the rule threshold
`score < rule.min_score` or `attendance_pct < rule.min_attendance_pct` on the evidence
completion. It is detected the same way as A1. It is not part of the required set and has no
ground-truth labels unless implemented.

## 4. Detection evaluation

`GET /api/v1/anomalies/evaluation?profile=full`:
1. Runs all detectors, producing a set of `(record_type, record_id, anomaly_type)` findings.
2. Loads `data/ground_truth/<profile>/anomaly_labels.csv`.
3. Per anomaly type it reports TP, FP and FN, `precision = TP/(TP+FP)`, `recall = TP/(TP+FN)` and `F1`. It also lists false positives and false negatives (capped at 50 each) for debugging.

The target is recall 1.0 and precision ≥ 0.95 ([PRD §8](PRD.md#8-success-metrics-for-the-demo)).
