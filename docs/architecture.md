# CredSure: Architecture

## 1. Overview

CredSure is a **three-tier modular monolith** that runs entirely on a local Windows machine.
It has one FastAPI process, one SQLite file and one Vite-served React application. There is no
Docker, message broker, PostgreSQL or cloud dependency. Decisions are recorded in
[architecture_decisions.md](architecture_decisions.md).

## 2. Three-tier architecture

```mermaid
flowchart LR
    subgraph T1["Tier 1: Presentation (frontend/)"]
        UI["React 19 + TypeScript<br/>Tailwind CSS · Lucide"]
        RS["Role switcher<br/>(X-Actor-Id / X-Actor-Role)"]
        UI --- RS
    end

    subgraph T2["Tier 2: Application (backend/app)"]
        API["api/routes<br/>(FastAPI routers, role checks)"]
        SCH["schemas<br/>(Pydantic v2)"]
        SVC["services<br/>(eligibility, issuance, crypto,<br/>verification, revocation, audit,<br/>metrics, anomalies)"]
        VAL["validators<br/>(domain checks)"]
        ORM["models + database<br/>(SQLAlchemy 2, sessions, pragmas)"]
        API --> SCH
        API --> SVC
        SVC --> VAL
        SVC --> ORM
    end

    subgraph T3["Tier 3: Data"]
        DB[("SQLite<br/>backend/data/credsure.db<br/>FK ON · WAL · triggers")]
        KEYS[/"backend/keys/*.pem<br/>(Ed25519 private keys,<br/>git-ignored)"/]
        GEN[/"data/generated/<br/>data/ground_truth/<br/>(CSV)"/]
    end

    CLI["CLI: python -m app.datagen<br/>generate · load"]

    UI -- "REST/JSON /api/v1 (Vite proxy :5173 → :8000)" --> API
    ORM -- "SQLAlchemy ORM / Core" --> DB
    SVC -- "sign only" --> KEYS
    CLI --> GEN
    CLI --> SVC
    SVC -- "evaluation reads labels" --> GEN
```

**Tier boundaries:**
- The frontend never touches the database or keys. It only calls the REST API.
- Routers stay thin: they validate input, check the role and call one service function. Business rules live only in services and validators, so the API, the CLI loader and the tests share the same logic.
- Only `services/crypto` reads private keys. Only `services/anomalies/evaluation` reads ground truth.

## 3. Backend module layout

This layout extends the existing structure from Task 01. Files marked † are new; this task
creates none of them.

```
backend/app/
├── main.py                     (exists) app factory, CORS, logging, error handlers
├── core/
│   ├── config.py               (exists) + signing_key_dir (platform), synthetic_key_dir, data_dir settings †
│   ├── errors.py               (exists) + DomainError handler, IntegrityError → domain-code map (D3) †
│   ├── security.py †           Actor dependency: parse headers, require_roles(...)
│   └── timeutil.py †           utc_now, to_utc, format_utc, parse_utc: the only time helpers (D2)
├── database/
│   ├── base.py                 (exists)
│   ├── session.py              (exists) + recursive_triggers, isolation_level=None, begin event,
│   │                           WriteSessionLocal (BEGIN IMMEDIATE) / SessionLocal (BEGIN) (D3) †
│   ├── types.py †              UTCDateTime (whole-second UTC, 'YYYY-MM-DD HH:MM:SS') (D2)
│   └── triggers.py †           CREATE/DROP statements for the 16 triggers + 2 indexes (data_dictionary §6)
├── models/ †                   issuer.py, learner.py, course.py, credential_rule.py,
│                               completion_record.py, credential.py, revocation.py,
│                               verification_request.py, audit_event.py, enums.py
├── schemas/ †                  one module per resource (Create / Update / Read models)
├── validators/ †               completion.py, rule.py, ids.py (formats, normalisation)
├── services/ †
│   ├── crypto.py               canonical_json(), sha256_hex(), sign(), verify(), key loading
│   ├── ids.py                  next_sequence_id(), new_credential_id()
│   ├── audit.py                record_event() with hash chain; verify_chain()
│   ├── eligibility.py          evaluate(learner_id, course_id, at) -> EligibilityResult
│   ├── issuance.py             issue_credential(...)
│   ├── verification.py         verify_credential(...)
│   ├── revocation.py           revoke_credential(...)
│   ├── master_data.py          issuers, learners, courses, rules, completions
│   ├── metrics.py              summary() via SQL aggregates
│   └── anomalies/              detectors.py (A1–A4), evaluation.py
├── api/routes/ †               issuers, learners, courses, rules, completions,
│                               eligibility, credentials, verifications, audit,
│                               metrics, anomalies  (+ existing health.py)
└── datagen/ †                  __main__.py (CLI), config.py (profiles), generator.py,
                                injector.py, loader.py
```

Dependencies point one way: `api → services → (validators, models, database)`. Services
never import from `api`.

## 4. Cross-cutting design

| Concern | Design |
|---------|--------|
| Transactions (D3) | Each connection runs `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=30000` and `recursive_triggers=ON`. The driver's implicit transactions are disabled (`isolation_level=None`), and the engine `begin` event emits `BEGIN IMMEDIATE` for `WriteSessionLocal` and `BEGIN` for `SessionLocal`. Every write-critical operation uses `WriteSessionLocal`, so its reads, ID allocation and inserts happen under one write lock. Success audit events are inserted in the same transaction. See [ADR-019](architecture_decisions.md#adr-019-sqlite-transaction-configuration). |
| Rejection auditing | On a `DomainError`, or an `IntegrityError` at insert, the transaction rolls back. The audit service then writes the `*_rejected` event in a new `BEGIN IMMEDIATE` transaction, carrying the same `attempt_ref` for issuance (D7). |
| Errors | Services raise `DomainError(code, http_status, message, details)`. One handler converts it to the existing envelope. Plain `INSERT` only: no `INSERT OR REPLACE`, upsert or `merge()` on immutable or audit-sensitive tables. `IntegrityError`s are mapped by constraint or trigger name ([exception_catalogue §1.7](exception_catalogue.md#17-database-constraint-mapping)). |
| Authorisation | `Actor` dependency from headers, plus `require_roles(...)` per route. Ownership checks (learner's own records, instructor's course) happen in services. Denials write `access.denied`. |
| Cryptography | Canonical JSON → SHA-256 (`hashlib`) → Ed25519 (`cryptography.hazmat.primitives.asymmetric.ed25519`). One shared implementation is used by the API, the generator and the detectors. `canonical_json()` rejects floats; scores are fixed-point strings (D6). |
| Keys (D8) | Platform keys: `backend/keys/platform/<signing_key_id>.pem`, created in exclusive mode and never overwritten. Synthetic keys: `backend/keys/synthetic/<profile>/`. Both are PKCS8, unencrypted for local development, and git-ignored. The directory is chosen by the issuer's `record_source`. The generator writes only to the synthetic directory. The issuer row stores only the public key. |
| Immutability (D1) | 16 SQLite triggers from `database/triggers.py` ([data_dictionary §6](data_dictionary.md#6-reference-ddl-triggers-and-expression-indexes)):<br>• `audit_events`: append-only plus chain link<br>• `revocations`, `verification_requests`: no UPDATE/DELETE<br>• `credentials`: no DELETE; only `active → revoked` with a matching revocation<br>• `issuers`: no DELETE; only `active → retired`<br>• `credential_rules`: criteria frozen after activation; effective-date guard<br>• `completion_records`: locked while an active credential references them<br>A migration test asserts the trigger inventory. |
| Audit chain (D4) | Linear chain enforced by `AUTOINCREMENT`, `UNIQUE(previous_hash)`, the single-genesis index and `trg_audit_events_chain_link`. Audit writes happen only inside `BEGIN IMMEDIATE` transactions that read the head in the same transaction. |
| Time (D2) | All timestamps are UTC with whole seconds, via `core/timeutil.py`. `utc_now()` is the injectable clock, so tests, the generator and the replay are deterministic. Storage text is `YYYY-MM-DD HH:MM:SS`. The wire and payload format is `YYYY-MM-DDTHH:MM:SSZ`. |
| Logging | Existing root logger. Log IDs and result codes, never payload personal data or keys. |
| Performance | Indexes listed in the data dictionary. Bulk load uses SQLAlchemy Core `insert()` with `executemany` in one transaction per file. Detectors are set-based SQL. |

## 5. Key workflows

### 5.1 Credential issuance

```mermaid
sequenceDiagram
    autonumber
    actor AA as Academic Admin
    participant UI as React UI
    participant API as POST /api/v1/credentials
    participant ISS as IssuanceService
    participant ELG as EligibilityService
    participant CR as CryptoService
    participant AUD as AuditService
    participant DB as SQLite

    AA->>UI: Select learner + course, click "Issue"
    UI->>API: {learner_id, course_id, credential_id?, attempt_ref?} + actor headers
    API->>API: require_roles(academic_admin)
    API->>ISS: issue_credential(...)
    ISS->>DB: BEGIN IMMEDIATE (WriteSessionLocal)
    ISS->>DB: attempt_ref = supplied ?? new ATT-ref, already used?
    alt attempt_ref already used
        ISS->>DB: ROLLBACK
        API-->>UI: 409 duplicate_attempt_ref (logged, not a new attempt)
    else new attempt
        ISS->>DB: credential_id = upper(supplied) ?? new_credential_id(), exists?
        alt duplicate credential_id (checked BEFORE eligibility, D7)
            ISS->>DB: ROLLBACK
            ISS->>AUD: credential.issue_rejected (duplicate_credential_id, attempt_ref) in own BEGIN IMMEDIATE txn
            API-->>UI: 409 duplicate_credential_id
        else unique
            ISS->>ELG: evaluate(learner, course, utc_now())
            ELG->>DB: learner, course, latest completed attempt,<br/>rule effective on/before completed_at, active credential?
            ELG-->>ISS: EligibilityResult(eligible, reasons, rule, completion)
            alt not eligible
                ISS->>DB: ROLLBACK
                ISS->>AUD: credential.issue_rejected (not_eligible / credential_already_issued, attempt_ref)
                API-->>UI: 422 not_eligible {reasons} / 409 credential_already_issued
            else eligible
                ISS->>CR: build payload (score "82.50", format_utc times) → canonical_json
                CR-->>ISS: canonical bytes, sha256 hex
                ISS->>CR: sign(canonical bytes, platform issuer key)
                CR-->>ISS: Ed25519 signature (base64)
                ISS->>DB: INSERT credentials (plain INSERT)
                ISS->>AUD: eligibility.evaluated + credential.issued (attempt_ref, same txn)
                ISS->>DB: COMMIT
                API-->>UI: 201 credential + payload + hash + signature + attempt_ref
            end
        end
    end
    Note over ISS,DB: IntegrityError at INSERT (e.g. race on uq_credentials_active_learner_course)<br/>→ ROLLBACK, then credential.issue_rejected with the same attempt_ref
```

### 5.2 Third-party verification

```mermaid
sequenceDiagram
    autonumber
    actor EV as External Verifier
    participant API as POST /api/v1/verifications
    participant VS as VerificationService
    participant CR as CryptoService
    participant DB as SQLite
    participant AUD as AuditService

    EV->>API: {credential_id, payload?, signature?, verifier}
    API->>VS: verify_credential(...)
    VS->>DB: SELECT credential by upper(credential_id)
    alt not found
        VS-->>VS: result = not_found
    else found
        VS->>CR: sha256(canonical(payload ?? stored payload_json))
        VS-->>VS: hash_match = (computed == stored payload_hash)
        VS->>DB: SELECT issuer public key (by signing_key_id)
        VS->>CR: Ed25519 verify(signature ?? stored, canonical bytes)
        VS->>DB: revocation exists? OR status = revoked
        VS-->>VS: is_expired = expires_at < now
        VS-->>VS: result by precedence:<br/>tampered → invalid_signature → revoked → expired → valid
    end
    VS->>DB: INSERT verification_requests (flags, result)
    VS->>AUD: verification.performed (same txn)
    VS->>DB: COMMIT
    API-->>EV: 200 {result, checks, message, credential summary if trustworthy}
```

### 5.3 Revocation

```mermaid
sequenceDiagram
    autonumber
    actor AA as Academic Admin
    participant API as POST /credentials/{id}/revoke
    participant RS as RevocationService
    participant DB as SQLite
    participant AUD as AuditService

    AA->>API: {reason_code, reason_text?, requested_by?}
    API->>API: require_roles(academic_admin)
    API->>RS: revoke_credential(...)
    RS->>DB: BEGIN IMMEDIATE
    RS->>DB: SELECT credential + existing revocation
    alt not found
        RS-->>API: 404 credential_not_found
    else already revoked
        RS->>DB: ROLLBACK
        RS->>AUD: credential.revoke_rejected (credential_already_revoked)
        RS-->>API: 409 credential_already_revoked
    else invalid reason
        RS->>AUD: credential.revoke_rejected (revocation_reason_required)
        RS-->>API: 422 revocation_reason_required
    else ok
        RS->>DB: INSERT revocations (UNIQUE credential_id)
        RS->>DB: UPDATE credentials SET status='revoked', revoked_at
        RS->>AUD: credential.revoked (same txn)
        RS->>DB: COMMIT
        RS-->>API: 200 credential (revoked) + revocation
    end
    Note over DB: Later verifications return "revoked"<br/>(revocations table is authoritative)
```

Revocation is atomic and irreversible (D1). The three writes in the `ok` branch commit together.
`trg_credentials_status_transition` accepts only `active → revoked`, and only when the matching
revocation row (same `revoked_at`) already exists in the transaction. Every later attempt to
change the status is rejected by the database.

### 5.4 Synthetic data, anomaly detection and evaluation

```mermaid
flowchart LR
    G["generate<br/>seed 20261022<br/>profile starter|full"] --> V["valid dataset<br/>(signed credentials)"]
    V --> INJ["inject A1–A4<br/>+ hard negatives"]
    INJ --> INP[/"data/generated/&lt;profile&gt;/*.csv<br/>manifest.json"/]
    INJ --> GT[/"data/ground_truth/&lt;profile&gt;/<br/>anomaly_labels.csv"/]
    INP --> L["load<br/>bulk insert (record_source=synthetic_import)<br/>replay issuance_attempts via IssuanceService"]
    L --> DB[("SQLite")]
    DB --> D["detectors A1–A4<br/>(SQL over source records)"]
    D --> F["findings"]
    F --> E["evaluation<br/>precision / recall / F1"]
    GT --> E
    DB --> M["metrics (on demand)"]
```

## 6. Frontend structure (target)

```
frontend/src/
├── api/client.ts          fetch wrapper: base '/api/v1', actor headers, error envelope → typed error
├── api/types.ts           TypeScript types mirroring the API schemas
├── context/ActorContext   current role + actor ID (role switcher, persisted in localStorage)
├── components/            Layout, NavBar, RoleSwitcher, StatusBadge, ResultBanner, DataTable, JsonViewer
└── pages/
    ├── Dashboard.tsx         metrics cards + verification results breakdown
    ├── IssueCredential.tsx   learner/course pickers → eligibility checklist → issue
    ├── CredentialDetail.tsx  payload, hash, signature, revoke dialog, audit trail
    ├── Verify.tsx            public form: credential ID ± pasted payload JSON
    ├── Anomalies.tsx         run detectors, findings table, evaluation metrics
    └── AuditLog.tsx          filterable event list + chain status (S)
```

Navigation uses simple state-based routing (no router dependency) unless `react-router` is
explicitly approved. That keeps to "no unnecessary dependencies" ([ADR-015](architecture_decisions.md#adr-015-frontend-minimal-dependencies-and-state-based-navigation)).

## 7. Security model (MVP)

| Threat | Mitigation |
|--------|------------|
| Payload tampering by holder or verifier | Hash and signature checks (BR-11). Altered data is never echoed back as valid. |
| Database tampering of payloads | Immutability triggers stop application-level edits. The **Ed25519 signature**, checked against `issuers.public_key` on every verification, is the integrity control. The stored hash is only a fast consistency check, because someone who edits the file can change both payload and hash. |
| Audit history rewrite | Append-only triggers (`recursive_triggers=ON` closes the REPLACE bypass), a database-enforced linear hash chain, and the `verify-chain` endpoint. **Limit (D4):** this detects application-level tampering, but not an attacker with unrestricted file access, who can rewrite or truncate the whole chain or restore an older file. |
| Key leakage | Keys only in git-ignored files. Never in the database, logs or responses. |
| Synthetic key misuse (D8) | Synthetic issuers use `ISS-9nn` and `backend/keys/synthetic/<profile>/`. The generator never touches platform keys. Synthetic keys are seed-derived and forgeable: development only. |
| Privilege misuse | Role map plus separation of duties: Platform Admin cannot issue or revoke; Academic Admin cannot manage keys. Denials are audited. |
| Spoofed actor headers | **Accepted risk** for a local demo (AS-01). It would be replaced by real authentication (OIDC/JWT) in production. |
