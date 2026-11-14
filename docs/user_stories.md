# CredSure: User Stories

Format: *As a \<actor\>, I want \<capability\> so that \<benefit\>.*
Priority: **M** must (demo-critical) · **S** should · **C** could.
Acceptance criteria use Given / When / Then. The links point to functional requirements (FR) in
the [PRD](PRD.md) and business rules (BR) in [business_rules.md](business_rules.md).

---

## Academic Administrator

### US-01 Create a learner (M) · FR-01, BR-01
As an academic administrator, I want to register learners with a system-generated ID, so that each learner is identified unambiguously.
- **Given** valid names and a unique email, **when** I submit, **then** a learner is created with ID `LRN-nnnnnnn` and `learner.created` is audited.
- **Given** a learner named "Asha Rao" exists, **when** I create another "Asha Rao" with a different email, **then** a second, distinct learner ID is created.
- **Given** an email already in use, **when** I submit, **then** I receive `409 learner_email_exists`.

### US-02 Create a course (M) · FR-02
As an academic administrator, I want to create courses linked to an active issuer, so that credentials for the course are issued by the right institution.
- A unique `course_code` is required; a duplicate returns `409 course_code_exists`.
- A retired issuer returns `422 issuer_inactive`.

### US-03 Define and version a credential rule (M) · FR-04, BR-06, BR-07
As an academic administrator, I want to define completion criteria as versioned rules, so that learners are judged by the rule in force when they completed.
- **Given** a course, **when** I create a rule, **then** it is `draft` with the next version number.
- **When** I activate v2 without a date, **then** v2 is `active` with `effective_from = now`, and v1 becomes `retired` with `effective_to` equal to that instant.
- **When** I activate v2 with a backdated `effective_from` that is later than every recorded completion of the course, **then** it is accepted (D5).
- **When** the backdated `effective_from` is at or before a recorded completion's `completed_at`, **then** I receive `409 rule_effective_date_conflict` listing those completions, and nothing changes (D5).
- **When** I try to edit an active rule, **then** I receive `409 rule_version_immutable`.
- **Given** a learner who completed while v1 was in effect, **when** eligibility is evaluated after v2 is activated, **then** v1 is applied.
- **Given** a completion dated before any rule version was effective, **then** the completion is recorded with `applicable_rule_id: null` and warning `no_applicable_rule`, and eligibility fails with `no_applicable_rule` (D5).

### US-04 Check eligibility before issuing (M) · FR-08, BR-04
As an academic administrator, I want to see a checklist explaining eligibility, so that I understand why a learner can or cannot receive a credential.
- The response lists every check with pass or fail, the applied rule version and the completion used.
- All failing reasons are returned, not just the first.

### US-05 Issue a credential (M) · FR-09–FR-11, BR-02, BR-03, BR-05, BR-08, BR-09
As an academic administrator, I want to issue a signed credential to an eligible learner, so that the learner holds tamper-evident proof of achievement.
- **Given** an eligible learner, **when** I issue, **then** a credential is created with a unique `CRED-` ID, a canonical payload, a 64-character SHA-256 hash and an Ed25519 signature, and `credential.issued` is audited.
- **Given** an ineligible learner, **when** I issue, **then** I receive `422 not_eligible` with reasons, no credential exists, and `credential.issue_rejected` is audited.
- **Given** the learner already holds an active credential for the course, **when** I issue, **then** I receive `409 credential_already_issued`.
- **Given** a supplied credential ID that already exists (any letter case), **when** I issue, **then** I receive `409 duplicate_credential_id`, even if the learner is not eligible (checked first, D7), and the existing credential is unchanged.
- Every issuance response and audited rejection carries the attempt's `attempt_ref`. The audit log shows exactly one terminal event per attempt (D7).
- The signed payload shows the score as a fixed-point string such as `"82.50"` (D6).

### US-06 Revoke a credential (M) · FR-16, BR-12, BR-16
As an academic administrator, I want to revoke a credential with a reason, so that it can no longer be presented as valid.
- **Given** an active credential, **when** I revoke with reason `academic_misconduct`, **then** a revocation is stored, the credential status becomes `revoked`, and `credential.revoked` is audited.
- **When** I revoke without a reason, or with `other` and no text, **then** I receive `422 revocation_reason_required`.
- **When** I revoke an already revoked credential, **then** I receive `409 credential_already_revoked`.
- **Then** any later verification returns `revoked`.
- The revocation record and the status change commit together or not at all. A revoked credential can never be reinstated; to correct an error, a new credential is issued (D1).

## Instructor

### US-07 Record completion and assessment (M) · FR-06, BR-19
As an instructor, I want to record a learner's completion status, score and attendance, so that eligibility can be evaluated from real assessment data.
- `status=completed` without `completed_at` returns `422 completion_invalid_state`.
- A score outside 0–100 returns `422`.
- A future `completed_at` returns `422 completion_in_future`.
- `completion.recorded` is audited with the instructor's actor ID.

### US-08 Correct a completion record (S) · FR-07, BR-20
As an instructor, I want to correct a mistaken score, so that records are accurate.
- **Given** no active credential references the record, **when** I correct it, **then** the change is saved and `completion.corrected` is audited with before and after values.
- **Given** an active credential references it, **when** I correct it, **then** I receive `409 completion_locked`.

### US-09 Stay within my courses (C) · AS-10
As an instructor, I can only record completions for courses where I am the lead instructor (when one is assigned). Otherwise I receive `403 course_scope_denied`.

## Learner

### US-10 View my credentials (S) · FR-12
As a learner, I want to see my credentials and their status, so that I know what I can share.
- With `X-Actor-Role: learner` I see only records where `learner_id` equals my actor ID. Requesting another learner returns `403`.

### US-11 Share a credential (M) · FR-12
As a learner, I want to download my credential's payload and signature, so that an employer can verify it independently.
- The share bundle contains the payload, hash, signature, issuer ID and key ID, and never any private key.

## External Verifier

### US-12 Verify a credential by ID (M) · FR-14, FR-15, BR-13, BR-14
As an employer, I want to enter a credential ID and get a clear result, so that I can trust a candidate's claim.
- **Given** an active, unaltered credential, **then** the result is `valid` with a summary (course, learner name, issuer, issue date).
- **Given** an unknown ID, **then** the result is `not_found` and the attempt is still recorded.
- No headers are required.

### US-13 Detect an altered credential (M) · BR-11
As an employer, I want altered credentials flagged, so that I am not deceived by edited documents.
- **Given** a presented payload with a changed score, **then** the result is `tampered`, `hash_match=false`, and no credential summary is echoed back.

### US-14 Detect a revoked credential (M) · BR-12
As an employer, I want revoked credentials flagged, so that I don't accept withdrawn qualifications.
- **Given** a revoked credential, **then** the result is `revoked` with the revocation date; the reason code is not disclosed.

## Support Staff

### US-15 Look up a learner's credential history (S)
As support staff, I want to search learners and view their credentials, verification history and audit trail, so that I can answer queries.
- Read-only. Any create, update, issue or revoke attempt returns `403 permission_denied` and is audited as `access.denied`.

### US-16 Verify on behalf of a caller (C)
As support staff, I can run a verification from the Verify screen. The request is recorded with `channel=portal`.

## Platform Administrator

### US-17 Create an issuer with a signing key (M) · FR-03, BR-10, BR-17
As a platform administrator, I want to create an issuer with an Ed25519 key pair, so that credentials can be signed.
- The public key is stored and the private key is written to a git-ignored file under `backend/keys/platform/`, never overwriting an existing file. The response never contains the private key.
- Platform issuers receive IDs in `ISS-001`–`ISS-899`; the synthetic range `ISS-901`–`ISS-999` is reserved (D8).

### US-18 Retire an issuer without breaking old credentials (S) · BR-17
- **Given** a retired issuer, **then** new issuance for its courses fails with `issuer_inactive`, while existing credentials still verify as `valid`.

### US-19 Generate and load synthetic data (M) · FR-23–FR-26, BR-24, BR-25
As a platform administrator, I want to generate starter or full datasets from seed 20261022 and load them, so that the platform can be demonstrated at realistic scale.
- Running `generate` twice with the same profile and seed produces identical checksums.
- Input CSVs contain no label columns; labels exist only in `data/ground_truth/`.
- `load` imports all files and replays issuance attempts. Duplicate-ID attempts are rejected and audited with their `attempt_id` as the attempt reference (D7).
- Synthetic issuers and their keys are separate from platform issuers. After generation, every file in `backend/keys/platform/` is unchanged (D8).
- Full profile: generation ≤ 2 min and load ≤ 2 min.

### US-20 Run anomaly detection (M) · FR-21
As a platform administrator, I want to run detectors A1–A4, so that faulty records are surfaced with evidence.
- Each finding includes the record type, record ID, anomaly type and evidence fields.
- `anomaly.run_completed` is audited with counts per type.

### US-21 Evaluate detection quality (M) · FR-22
As a platform administrator, I want precision and recall against ground truth, so that I can show the detectors are reliable.
- The output shows TP, FP, FN, precision, recall and F1 per type. The target is recall 1.0 and precision ≥ 0.95.

### US-22 Verify audit integrity (S) · FR-19, BR-22
As a platform administrator, I want to verify the audit hash chain, so that I can prove history has not been rewritten.
- An intact chain returns `valid: true`. A tampered row (in a test) returns `valid: false` with `first_broken_event_id`.

## Shared (Academic Admin, Support Staff, Platform Admin)

### US-23 Metrics dashboard (M) · FR-20, BR-23
As an administrator, I want a dashboard of credential, verification and revocation metrics computed from live records, so that I can monitor the platform.
- Each figure equals a direct SQL count over the source tables (asserted in tests).
- Each rate shows its numerator and denominator definitions.

### US-24 Audit trail per credential (M) · FR-17
As an administrator, I want to see every event for a credential (eligibility, issuance, verifications, revocation) in time order, so that I can reconstruct its history.

## Demo acceptance scenario (end to end)

1. Platform admin: an issuer exists (synthetic or created).
2. Academic admin: create a course and rule v1 → activate.
3. Instructor: record a completion with score 65 → eligibility fails (`score_below_minimum`, min 70).
4. Instructor: record attempt 2 with score 82 → eligibility passes.
5. Academic admin: issue → credential with hash and signature.
6. Verifier: verify by ID → `valid`. Paste the payload with the score edited → `tampered`.
7. Academic admin: revoke (`issued_in_error`) → verifier result `revoked`.
8. Credential detail shows the full audit trail. The chain check returns `valid`.
9. Platform admin: load the starter dataset → run anomalies → evaluation shows precision and recall.
