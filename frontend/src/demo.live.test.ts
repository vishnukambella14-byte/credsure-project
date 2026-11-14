// Live demo-flow check (opt-in): drives the real API client through the documented demo sequence
// against a running backend, e.g. via the Vite proxy:
//   VITE_DEMO_API_BASE=http://127.0.0.1:5173/api/v1 VITE_DEMO_LEARNER=LRN-0000201 VITE_DEMO_COURSE=CRS-00021 npm run test
// Skipped when VITE_DEMO_API_BASE is not set, so `npm run test` never needs a server.
import { describe, expect, it } from 'vitest'
import { ApiError, api, setApiBase } from './api/client'
import type { Actor } from './api/types'

const base = import.meta.env.VITE_DEMO_API_BASE as string | undefined
const learner = (import.meta.env.VITE_DEMO_LEARNER as string | undefined) ?? 'LRN-0000201'
const course = (import.meta.env.VITE_DEMO_COURSE as string | undefined) ?? 'CRS-00021'

const AA: Actor = { actorId: 'AA-001', role: 'academic_admin' }
const PA: Actor = { actorId: 'PA-001', role: 'platform_admin' }
const PUBLIC: Actor = { actorId: 'anonymous', role: 'external_verifier' }
const VERIFIER = { name: 'Jordan Lee', organization: 'Acme Corp' }

describe.skipIf(!base)('live demo flow against the running backend', () => {
  it('runs the user_stories.md demo sequence end to end', async () => {
    setApiBase(base!)

    // 1–2. Role selected (academic administrator); dashboard metrics load.
    const before = await api.metrics(AA)
    expect(before.counts.credentials).toBeGreaterThanOrEqual(0)

    // 3. Eligible learner → issue.
    const eligibility = await api.eligibility(AA, learner, course)
    expect(eligibility.eligible).toBe(true)
    const issued = await api.issue(AA, { learner_id: learner, course_id: course })
    expect(issued.payload_hash).toHaveLength(64)

    // 4. View the issued credential (and its audit trail).
    const detail = await api.credential(AA, issued.credential_id)
    expect(detail.status).toBe('active')
    const trail = await api.auditEvents(AA, { entity_id: issued.credential_id })
    expect(trail.items.map((e) => e.event_type)).toContain('credential.issued')

    // 5. Public verification succeeds; an edited payload is detected as tampered.
    const valid = await api.verify(PUBLIC, { credential_id: issued.credential_id, verifier: VERIFIER })
    expect(valid.result).toBe('valid')
    const tampered = await api.verify(PUBLIC, {
      credential_id: issued.credential_id, verifier: VERIFIER,
      payload: { ...detail.payload, score: '99.00' }, signature: detail.signature,
    })
    expect(tampered.result).toBe('tampered')

    // 6. Revoke.
    const revoked = await api.revoke(AA, issued.credential_id, { reason_code: 'issued_in_error' })
    expect(revoked.credential.status).toBe('revoked')
    const again = await api.revoke(AA, issued.credential_id, { reason_code: 'fraud' }).catch((e: unknown) => e)
    expect((again as ApiError).code).toBe('credential_already_revoked')

    // 7. Verify again → revoked.
    const afterRevocation = await api.verify(PUBLIC, { credential_id: issued.credential_id, verifier: VERIFIER })
    expect(afterRevocation.result).toBe('revoked')

    // 8. Run anomaly detection (platform administrator).
    const run = await api.runAnomalies(PA)
    expect(Object.keys(run.findings_by_type).sort()).toEqual([
      'duplicate_credential_id', 'issued_before_completion', 'revoked_verified_as_valid', 'tampered_payload_not_detected',
    ])

    // 9. Evaluation against the starter ground truth.
    const evaluation = await api.evaluation(AA, 'starter')
    expect(evaluation.overall.recall).toBe(1)
    expect(evaluation.overall.precision).toBeGreaterThanOrEqual(0.95)

    const after = await api.metrics(AA)
    expect(after.counts.credentials).toBe(before.counts.credentials + 1)
    expect(after.counts.credentials_revoked).toBe(before.counts.credentials_revoked + 1)
  }, 60_000)
})
