# CredSure: REST API Contract

Target contract for the MVP. Only `GET /api/health` exists today.

## 1. Conventions

| Topic | Rule |
|-------|------|
| Base URL | `http://127.0.0.1:8000`. The frontend calls relative `/api/...` through the Vite proxy. |
| Prefix | `/api/v1` for business endpoints. `/api/health` stays unversioned (existing). |
| Format | JSON, UTF-8. Field names in snake_case. |
| Timestamps (D2) | Responses always use `YYYY-MM-DDTHH:MM:SSZ` (UTC, whole seconds), e.g. `2026-03-20T09:30:00Z`. Requests accept ISO-8601 with an explicit offset (`Z` or `±HH:MM`). Values are converted to UTC and fractional seconds are truncated. Naive timestamps return `422 validation_error`. One shared formatter and parser (`app/core/timeutil.py`) is used everywhere. |
| IDs | Prefixed business IDs ([data_dictionary §1](data_dictionary.md#id-formats)). Credential IDs are normalised to upper case. |
| Pagination | `?limit=` (default 50, max 200) and `?offset=`. Response: `{"items": [...], "total": n, "limit": l, "offset": o}` |
| Errors | `{"error": {"code", "message", "details?"}}` with codes from [exception_catalogue §1](exception_catalogue.md#1-api-errors) |
| Creation | `201 Created` with the full resource |
| Idempotency | Not supported in the MVP. Duplicate creates fail with a 409 where a unique key exists. |

## 2. Actor headers and authorisation

| Header | Example | Notes |
|--------|---------|-------|
| `X-Actor-Id` | `ADM-001`, `LRN-0000042`, `INS-007` | For learners this **must** be their `learner_id` |
| `X-Actor-Role` | `academic_admin` | One of `learner`, `instructor`, `academic_admin`, `support_staff`, `platform_admin`, `external_verifier` |

- Endpoints marked **Public** need no headers; the actor defaults to `external_verifier` with `X-Actor-Id = anonymous`.
- Missing or unknown headers on protected endpoints return `401 actor_required`. A role outside the **Roles** column returns `403 permission_denied` (audited).
- This is simulated authentication for local demonstration only ([ADR-010](architecture_decisions.md#adr-010-simulated-role-based-access-via-request-headers)).

Role abbreviations: **L** learner, **I** instructor, **AA** academic_admin, **SS** support_staff, **PA** platform_admin, **EV** external_verifier / public.

## 3. Endpoint summary

| Method | Path | Roles | Purpose | FR |
|--------|------|-------|---------|----|
| GET | `/api/health` | Public | Health and database status (exists) | – |
| **Issuers** |||||
| POST | `/api/v1/issuers` | PA | Create issuer and generate its key pair | FR-03 |
| GET | `/api/v1/issuers` | AA SS PA | List issuers | FR-03 |
| GET | `/api/v1/issuers/{issuer_id}` | AA SS PA | Issuer detail (no private key) | FR-03 |
| POST | `/api/v1/issuers/{issuer_id}/retire` | PA | Retire issuer | BR-17 |
| GET | `/api/v1/issuers/{issuer_id}/public-key` | Public | `{issuer_id, signing_key_id, algorithm, public_key}` | FR-13 |
| **Learners** |||||
| POST | `/api/v1/learners` | AA | Create learner | FR-01 |
| GET | `/api/v1/learners?search=&status=` | I AA SS PA | List and search (name, email, ID) | FR-01 |
| GET | `/api/v1/learners/{learner_id}` | L(own) I AA SS PA | Detail | FR-01 |
| PATCH | `/api/v1/learners/{learner_id}` | AA | Update names, email, status (never the ID) | FR-01 |
| **Courses** |||||
| POST | `/api/v1/courses` | AA | Create course | FR-02 |
| GET | `/api/v1/courses` | I AA SS PA | List | FR-02 |
| GET | `/api/v1/courses/{course_id}` | I AA SS PA | Detail including current active rule | FR-02 |
| PATCH | `/api/v1/courses/{course_id}` | AA | Update title, description, issuer, lead instructor, status | FR-02 |
| **Credential rules** |||||
| POST | `/api/v1/courses/{course_id}/rules` | AA | Create a draft rule (next version number) | FR-04 |
| GET | `/api/v1/courses/{course_id}/rules` | I AA SS PA | All versions | FR-04 |
| PATCH | `/api/v1/rules/{rule_id}` | AA | Edit **draft** only | BR-06 |
| POST | `/api/v1/rules/{rule_id}/activate` | AA | Activate (optional backdated `effective_from`, D5); retire previous version | FR-04 |
| **Completion records** |||||
| POST | `/api/v1/completions` | I(course) AA | Record completion and assessment | FR-06 |
| GET | `/api/v1/completions?learner_id=&course_id=` | L(own) I AA SS PA | List | FR-06 |
| PATCH | `/api/v1/completions/{completion_id}` | I(course) AA | Correct (blocked if an active credential exists) | FR-07 |
| **Eligibility** |||||
| GET | `/api/v1/eligibility?learner_id=&course_id=` | L(own) I AA SS PA | Evaluate without side effects | FR-08 |
| **Credentials** |||||
| POST | `/api/v1/credentials` | AA | Issue | FR-09–11 |
| GET | `/api/v1/credentials?learner_id=&course_id=&status=` | L(own) I AA SS PA | List | – |
| GET | `/api/v1/credentials/{credential_id}` | L(own) I AA SS PA | Detail, including revocation if any | – |
| GET | `/api/v1/credentials/{credential_id}/share` | L(own) AA SS | Shareable bundle `{payload, payload_hash, signature, issuer_id, signing_key_id}` | FR-12 |
| POST | `/api/v1/credentials/{credential_id}/revoke` | AA | Revoke | FR-16 |
| GET | `/api/v1/revocations` | AA SS PA | List revocations | – |
| **Verification** |||||
| POST | `/api/v1/verifications` | Public (all roles) | Verify a credential | FR-14/15 |
| GET | `/api/v1/verifications?credential_id=&result=` | L(own credentials) AA SS PA | History | FR-15 |
| GET | `/api/v1/verifications/{verification_id}` | AA SS PA | Detail | – |
| **Audit** |||||
| GET | `/api/v1/audit-events?entity_type=&entity_id=&event_type=&actor_id=` | AA SS PA | List (newest first) | FR-17 |
| GET | `/api/v1/audit-events/verify-chain` | PA | Recompute the hash chain | FR-19 |
| **Metrics and anomalies** |||||
| GET | `/api/v1/metrics/summary` | AA SS PA | Metrics derived from source tables | FR-20 |
| POST | `/api/v1/anomalies/run` | PA | Run detectors A1–A4 | FR-21 |
| GET | `/api/v1/anomalies/evaluation?profile=starter\|full` | AA PA | Compare with ground truth | FR-22 |

Synthetic data generation and import are **CLI only**. There are deliberately no HTTP endpoints:

```powershell
python -m app.datagen generate --profile starter|full --seed 20261022
python -m app.datagen load --profile starter|full [--reset]
```

## 4. Key request and response schemas

### 4.1 Evaluate eligibility
`GET /api/v1/eligibility?learner_id=LRN-0000042&course_id=CRS-00017`
```json
{
  "learner_id": "LRN-0000042",
  "course_id": "CRS-00017",
  "eligible": false,
  "rule_id": "RUL-00017-V1",
  "rule_version": 1,
  "completion_id": "CMP-0001234",
  "reasons": ["score_below_minimum"],
  "checks": {"learner_active": true, "course_active": true, "completed": true,
             "completion_not_future": true, "rule_found": true, "score_ok": false,
             "attendance_ok": true, "not_already_issued": true, "issuer_ready": true},
  "evaluated_at": "2026-03-20T09:30:00Z"
}
```

### 4.2 Issue a credential
`POST /api/v1/credentials` (AA)
```json
{ "learner_id": "LRN-0000042", "course_id": "CRS-00017", "credential_id": null, "attempt_ref": null }
```
- `credential_id` is optional and normally omitted (system-generated). It exists for the dataset replay and for duplicate-ID tests.
- `attempt_ref` (D7) is optional. If omitted, the server generates `ATT-` + 12 Crockford characters. If supplied, it must be unused. The dataset replay supplies the attempt's `ATT-nnnnnn`.
- Rule, completion and issuer are resolved by the server and are never accepted from the client.
- **Order of checks** (D7, BR-26), all in one `BEGIN IMMEDIATE` transaction:
  1. Schema.
  2. `attempt_ref` (`409 duplicate_attempt_ref`, not audited).
  3. Credential ID format and uniqueness (`409 duplicate_credential_id`).
  4. Eligibility (`422 not_eligible` / `409 credential_already_issued`).
  5. Sign.
  6. Insert.
- Every response from step 3 onward, success or audited rejection, includes `attempt_ref` (in `error.details.attempt_ref` for errors).
- Each such attempt receives exactly one terminal audit event. The database permits at most one per `attempt_ref`; the service's transaction logic ensures that one is always written.
- Eligibility check 8 has internal reason `already_issued`. It maps to HTTP `409` with error code `credential_already_issued` (SR-12).

**201 Created**
```json
{
  "credential_id": "CRED-7K2M9QXR4T1B",
  "learner_id": "LRN-0000042",
  "course_id": "CRS-00017",
  "completion_id": "CMP-0001234",
  "rule_id": "RUL-00017-V1",
  "issuer_id": "ISS-001",
  "credential_type": "certificate",
  "status": "active",
  "issued_at": "2026-03-20T09:30:00Z",
  "expires_at": null,
  "payload": { "...": "canonical payload fields (data_dictionary §4); e.g. \"score\": \"82.50\"" },
  "payload_hash": "3f1c…64 hex…",
  "signature": "base64…88 chars…",
  "signing_key_id": "ISS-001-K1",
  "attempt_ref": "ATT-9F3K2M7QX1BZ"
}
```
In the payload, `score` is a fixed-point decimal string (`"82.50"`) or `null`, never a JSON
number (D6). Timestamps are whole-second UTC (D2).

Errors: `409 duplicate_attempt_ref`, `409 duplicate_credential_id`, `422 not_eligible` (with
`details.reasons`), `409 credential_already_issued`, `422 issuer_inactive`,
`503 signing_key_unavailable`.

### 4.3 Verify a credential
`POST /api/v1/verifications` (Public)
```json
{
  "credential_id": "CRED-7K2M9QXR4T1B",
  "payload": { "...": "optional, as presented by the holder" },
  "signature": "optional base64 signature as presented",
  "verifier": { "name": "Jordan Lee", "organization": "Acme Corp", "email": null },
  "purpose": "employment"
}
```
- Without `payload`, the stored payload is verified ("lookup verification").
- With `payload`, it is canonicalised and hashed server-side. A `credential_id` mismatch between the body and the payload returns `tampered`.
- With `signature`, the presented signature is verified; otherwise the stored signature is used.

**200 OK**
```json
{
  "verification_id": "VER-0000007",
  "credential_id": "CRED-7K2M9QXR4T1B",
  "result": "tampered",
  "checks": { "found": true, "hash_match": false, "signature_valid": true,
              "is_revoked": false, "is_expired": false },
  "message": "The credential data has been altered.",
  "credential": null,
  "requested_at": "2026-04-02T12:00:00Z"
}
```
`credential` (summary: type, course title, learner name, issuer name, issued and expiry dates)
is included **only** when `result = valid`, `revoked` or `expired`, so altered data is never
echoed back as authoritative.

### 4.4 Revoke a credential
`POST /api/v1/credentials/{credential_id}/revoke` (AA)
```json
{ "reason_code": "academic_misconduct", "reason_text": "Panel decision 2026-04-01", "requested_by": "SUP-003" }
```
**200 OK** returns `{ "credential": {..., "status": "revoked", "revoked_at": "..."}, "revocation": {"revocation_id": "REV-000031", ...} }`.
Errors: `409 credential_already_revoked`, `422 revocation_reason_required`, `404 credential_not_found`.

Revocation is atomic (D1). The revocation row, the `active → revoked` transition (with equal
`revoked_at`) and the audit event commit together or not at all. **There is no reinstatement
endpoint**, and the database rejects any transition out of `revoked`.

### 4.5 Record a completion
`POST /api/v1/completions` (I/AA)
```json
{ "learner_id": "LRN-0000042", "course_id": "CRS-00017", "attempt_number": 1,
  "status": "completed", "score": 82.5, "attendance_pct": 95.0,
  "completed_at": "2026-03-14T10:00:00Z" }
```
**201 Created** returns the completion record plus the rule it falls under (D5):
```json
{ "completion_id": "CMP-0001234", "...": "...", "completed_at": "2026-03-14T10:00:00Z",
  "applicable_rule_id": "RUL-00017-V1", "warnings": [] }
```
If no rule version of the course was effective on or before `completed_at`, the record is still
created, with `"applicable_rule_id": null` and `"warnings": ["no_applicable_rule"]`.
Eligibility for that completion then fails with `no_applicable_rule`, and nothing is rebound
later. `applicable_rule_id` is derived, not stored. Activation can never change it for a
recorded completion (§4.6).

### 4.6 Create and activate a rule
`POST /api/v1/courses/CRS-00017/rules`
```json
{ "credential_type": "certificate", "min_score": 70, "min_attendance_pct": 80,
  "validity_days": null, "description": "Pass mark 70 and 80% attendance" }
```
`POST /api/v1/rules/RUL-00017-V2/activate` (D5)
```json
{ "effective_from": "2026-04-01T00:00:00Z" }
```
- The body is optional. `effective_from` defaults to the current time.
- `effective_from` may be backdated, but:
  - not into the future (`422 rule_effective_date_invalid`)
  - must be later than the active version's `effective_from` (`422 rule_effective_date_invalid`)
  - must be later than every recorded completion of the course (`409 rule_effective_date_conflict`, `details.conflicting_completion_ids`). A new or backdated rule must never change the applicable rule of an already recorded completion.

**200 OK** → `{ "rule_id": "RUL-00017-V2", "status": "active", "effective_from": "2026-04-01T00:00:00Z", "activated_at": "2026-11-14T10:00:00Z", "retired_rule_id": "RUL-00017-V1" }`. The retired version's `effective_to` equals the new `effective_from`.

### 4.7 Metrics summary
`GET /api/v1/metrics/summary`
```json
{
  "computed_at": "2026-11-14T10:00:00Z",
  "counts": { "learners": 20000, "courses": 1000, "active_rules": 1000,
              "completions": 15000, "credentials": 10000, "credentials_active": 9700,
              "credentials_revoked": 300, "verifications": 5000 },
  "rates": {
    "revocation_rate": { "value": 0.03, "numerator": "revoked credentials", "denominator": "all credentials" },
    "verification_failure_rate": { "value": 0.10, "numerator": "verifications with result != valid", "denominator": "all verifications" },
    "issuance_conversion_rate": { "value": 0.77, "numerator": "credentials", "denominator": "completed completion records" }
  },
  "verifications_by_result": { "valid": 4500, "revoked": 250, "expired": 50, "tampered": 100, "invalid_signature": 0, "not_found": 100 },
  "credentials_issued_by_month": [ { "month": "2026-01", "count": 410 } ]
}
```

### 4.8 Anomaly run and evaluation
`POST /api/v1/anomalies/run`
```json
{ "run_at": "…", "findings_by_type": { "issued_before_completion": 100, "duplicate_credential_id": 50,
  "revoked_verified_as_valid": 50, "tampered_payload_not_detected": 50 },
  "findings": [ { "record_type": "credential", "record_id": "CRED-…", "anomaly_type": "issued_before_completion", "evidence": {"issued_at": "…", "completed_at": "…"} } ] }
```
`GET /api/v1/anomalies/evaluation?profile=full`
```json
{ "profile": "full", "by_type": { "issued_before_completion": { "tp": 100, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0 } },
  "overall": { "precision": 1.0, "recall": 1.0, "f1": 1.0 }, "false_positives": [], "false_negatives": [] }
```

### 4.9 Audit chain verification
`GET /api/v1/audit-events/verify-chain` → `{ "valid": true, "events_checked": 12873, "first_broken_event_id": null }`
