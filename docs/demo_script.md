# CredSure: End-to-End Demo Script

The intended live demonstration, based on the demo scenario in
[user_stories.md](user_stories.md#demo-acceptance-scenario-end-to-end). It runs entirely on a
**disposable database** and a **scratch platform-key folder**, so neither
`backend/data/credsure.db` nor `backend/keys/platform/` is touched.

> **Verification status (T09, 2026-11-14)**
>
> | What | Status |
> |------|--------|
> | Sections 1–2 (disposable backend, API seeding) | **Passed (T09).** The commands below were executed as written; only the folder path differed. Result: `ISS-001`, `LRN-0000201`, `CRS-00021`, `RUL-00021-V1`, learner eligible, platform key written only to the scratch folder. |
> | The API calls behind the browser steps in section 4 | **Passed (T08).** Run against a live backend through the Vite proxy using the frontend's own API client (`frontend/src/demo.live.test.ts`): eligibility, issue, detail and audit trail, valid / tampered / revoked verification, revoke and repeated revoke (409), anomaly run, starter evaluation. The backend log showed every expected call and status. |
> | Automated tests | **Passed.** Backend 332, frontend 20 (+1 opt-in live test). |
> | Clicking through the section 4 steps in a real browser | **Not performed (outstanding).** No browser-control tool was available. The visual behaviour described below is the *intended* behaviour, not an observed one. Rehearse it once in a browser before presenting. |

---

## 0. Prerequisites

- Backend venv and frontend `node_modules` installed (see the [README](../README.md#installation)).
- The **starter** synthetic dataset generated once, needed for the anomaly part (steps 10–11):
  ```powershell
  cd backend; .\venv\Scripts\Activate.ps1
  python -m app.datagen generate --profile starter --seed 20261022
  ```

## 1. Start a disposable backend (Terminal 1)

```powershell
cd backend
.\venv\Scripts\Activate.ps1
$demo = "C:/Temp/credsure-demo"
New-Item -ItemType Directory -Force $demo | Out-Null
Remove-Item "$demo/demo.db*" -ErrorAction SilentlyContinue
$env:CREDSURE_DATABASE_URL    = "sqlite:///$demo/demo.db"      # disposable database
$env:CREDSURE_SIGNING_KEY_DIR = "$demo/platform-keys"          # scratch platform keys
python -m alembic upgrade head
python -m app.datagen load --profile starter                   # 200 learners, 100 credentials, anomalies
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expected: the load prints `"replay_issued": 2` and `"duplicate_credential_id": 2`, and
`GET http://127.0.0.1:8000/api/health` returns `database: initialized`.

## 2. Seed the platform demo data through the API (Terminal 2)

The UI has no master-data forms, so the demo data is created with the real REST API. This
follows user_stories steps 1–4.

```powershell
$api = "http://127.0.0.1:8000/api/v1"
function As($role, $id) { @{ "X-Actor-Role" = $role; "X-Actor-Id" = $id } }
function Post($path, $headers, $body) {
  Invoke-RestMethod -Method Post -Uri "$api$path" -Headers $headers -ContentType "application/json" -Body ($body | ConvertTo-Json)
}
$PA = As "platform_admin" "PA-001"; $AA = As "academic_admin" "AA-001"; $INS = As "instructor" "INS-001"

$issuer  = Post "/issuers" $PA @{ name = "CredSure Demo Institute"; legal_name = "CredSure Demo Institute (demo)" }
$learner = Post "/learners" $AA @{ given_name = "Asha"; family_name = "Demo"; email = "asha.demo@example.org" }
$course  = Post "/courses" $AA @{ course_code = "DEMO-101"; title = "Data Foundations (demo)"; issuer_id = $issuer.issuer_id }
$rule    = Post "/courses/$($course.course_id)/rules" $AA @{ credential_type = "certificate"; min_score = 70 }
Post "/rules/$($rule.rule_id)/activate" $AA @{ effective_from = "2026-01-01T00:00:00Z" } | Out-Null
Post "/completions" $INS @{ learner_id = $learner.learner_id; course_id = $course.course_id; attempt_number = 1;
  status = "completed"; score = 65; attendance_pct = 92; completed_at = "2026-10-01T10:00:00Z" } | Out-Null
Post "/completions" $INS @{ learner_id = $learner.learner_id; course_id = $course.course_id; attempt_number = 2;
  status = "completed"; score = 82; attendance_pct = 95; completed_at = "2026-11-01T10:00:00Z" } | Out-Null
"Learner: $($learner.learner_id)  Course: $($course.course_id)  Issuer: $($issuer.issuer_id)"
```

Expected IDs on a fresh database with the starter dataset loaded: issuer `ISS-001` (platform
range, key in the scratch folder), learner `LRN-0000201`, course `CRS-00021`, rule `RUL-00021-V1`.
Adjust the `completed_at` dates if today is before 2026-11-01: they must not be in the future and
must fall on or after the rule's `effective_from`.

Optional: to show a rejection first, check eligibility *before* recording attempt 2. It returns
`eligible: false` with reason `score_below_minimum` (user_stories step 3).

## 3. Start the frontend (Terminal 3)

```powershell
cd frontend
npm run dev
```

Open **http://localhost:5173**.

## 4. Demo steps (browser; intended behaviour, not yet observed)

| # | Role (header switcher) | Screen and action | Expected result |
|---|------------------------|-------------------|-----------------|
| 1 | Academic Administrator (`AA-001`) | **Dashboard** | Metric cards (200+ learners, 100 credentials), three rates with numerator and denominator, verifications by result, monthly issuance bars, recent-credentials table. |
| 2 | Academic Administrator | **Issue credential** → search "Demo" → learner `LRN-0000201`, course `CRS-00021` → **Check eligibility** | Green "Eligible" badge, all nine checks ticked, rule `RUL-00021-V1` (v1), completion of attempt 2. |
| 3 | Academic Administrator | **Issue credential** | Green success box with the credential ID, SHA-256 hash and attempt reference. |
| 4 | Academic Administrator | **View credential** (opens Credential detail) | Status *active*, payload hash, Ed25519 signature, signed payload JSON (score `"82.50"`), audit trail with `eligibility.evaluated` and `credential.issued`. |
| 5 | (any) | **Verify by ID** | Verify screen prefilled. **Verify** shows a green **VALID** panel: all checks yes, learner/course/issuer summary. |
| 6 | (any) | Credential detail → **Verify with payload** → change `"score": "82.50"` to `"99.00"` → **Verify** | Red **TAMPERED** panel ("The credential data has been altered."), hash matches = no, no credential summary. |
| 7 | Academic Administrator | Credential detail → reason *issued in error* → **Revoke credential** | Success message; the page reloads with status *revoked*, a revocation line and a `credential.revoked` audit event. A second revoke is impossible (the form is replaced by "Revocation is permanent"). |
| 8 | External Verifier (public) | **Verify** the same ID | Red **REVOKED** panel: "This credential was revoked on <date>." The reason is not disclosed. |
| 9 | Learner (`LRN-0000201`) | **Dashboard** | Red permission error (metrics are for administrators and support staff); shows role enforcement. |
| 10 | Platform Administrator (`PA-001`) | **Anomalies** → **Run detectors** | Finding counts: issued before completion 3, duplicate credential ID 2, revoked verified as valid 2, tampered payload not detected 2; findings table with evidence. |
| 11 | Academic Administrator | **Anomalies** → profile *starter* → **Evaluate** | Per-type table with TP/FP/FN/TN; precision, recall and F1 100.0%; badge "Target met". |

Talking points:
- Steps 5–8 show BR-11 and BR-12: altered or revoked credentials never verify as valid.
- Step 7 shows that revocation is atomic and irreversible (D1).
- Steps 10–11 show detection quality measured against separate ground truth (BR-24).
- The audit trail in step 4 is append-only and hash-chained; `GET /api/v1/audit-events/verify-chain` (platform admin) returns `valid: true`.

## 5. Automated rehearsal (API level)

With Terminals 1 and 3 running and step 2 completed:

```powershell
cd frontend
$env:VITE_DEMO_API_BASE = "http://127.0.0.1:5173/api/v1"
$env:VITE_DEMO_LEARNER  = "LRN-0000201"
$env:VITE_DEMO_COURSE   = "CRS-00021"
npm run test
```

`demo.live.test.ts` issues a **new** credential each run, so re-seed (repeat steps 1–2) before
running it again or before the browser demo. Otherwise the learner already holds an active
credential (`409 credential_already_issued`), or a revoked one if the test ran last.

## 6. Clean up

Stop the three terminals and delete `C:/Temp/credsure-demo`. Nothing else was written, except
the starter dataset files under `data/` and `backend/keys/synthetic/starter/`, which are
regenerated deterministically.
