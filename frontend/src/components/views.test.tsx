import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { CredentialDetail, Eligibility, Evaluation, MetricsSummary, VerifyResponse } from '../api/types'
import { parsePayload, percent, toneFor } from '../format'
import { RoleSwitcher } from './RoleSwitcher'
import { CredentialView, EligibilityChecklist, EvaluationView, MetricsView, VerificationResultView } from './views'

const verifyResponse = (overrides: Partial<VerifyResponse>): VerifyResponse => ({
  verification_id: 'VER-0000001',
  credential_id: 'CRED-7K2M9QXR4T1B',
  result: 'valid',
  checks: { found: true, hash_match: true, signature_valid: true, is_revoked: false, is_expired: false },
  message: 'This credential is authentic and active.',
  credential: {
    credential_type: 'certificate', course_title: 'Data Foundations', learner_name: 'Asha Rao',
    issuer_name: 'CredSure Institute', issued_at: '2026-11-14T12:00:00Z', expires_at: null,
  },
  requested_at: '2026-11-14T12:00:00Z',
  ...overrides,
})

describe('formatting helpers', () => {
  it('maps statuses to tones and formats percentages', () => {
    expect(toneFor('valid')).toBe('green')
    expect(toneFor('revoked')).toBe('red')
    expect(toneFor('tampered')).toBe('red')
    expect(toneFor('expired')).toBe('amber')
    expect(percent(0.9512)).toBe('95.1%')
  })
  it('parses pasted payloads', () => {
    expect(parsePayload('  ')).toBeNull()
    expect(parsePayload('{"score":"82.50"}')).toEqual({ score: '82.50' })
    expect(() => parsePayload('[1]')).toThrow('JSON object')
  })
})

describe('views', () => {
  it('renders the metrics dashboard', () => {
    const metrics: MetricsSummary = {
      computed_at: '2026-11-14T12:00:00Z',
      counts: { learners: 20000, credentials: 10000, credentials_revoked: 298, verifications: 5000 },
      rates: { revocation_rate: { value: 0.0298, numerator: 'revoked credentials', denominator: 'all credentials' } },
      verifications_by_result: { valid: 4500, revoked: 250 },
      credentials_issued_by_month: [{ month: '2026-01', count: 410 }],
    }
    const html = renderToStaticMarkup(<MetricsView metrics={metrics} />)
    expect(html).toContain('20,000')
    expect(html).toContain('Revocation rate')
    expect(html).toContain('3.0%')
    expect(html).toContain('revoked credentials / all credentials')
    expect(html).toContain('2026-01')
  })

  it('explains an ineligible learner', () => {
    const result: Eligibility = {
      learner_id: 'LRN-0000002', course_id: 'CRS-00001', eligible: false, rule_id: 'RUL-00001-V1',
      rule_version: 1, completion_id: 'CMP-0000002', reasons: ['score_below_minimum'],
      checks: { completed: true, score_ok: false, attendance_ok: null }, evaluated_at: '2026-11-14T12:00:00Z',
    }
    const html = renderToStaticMarkup(<EligibilityChecklist result={result} />)
    expect(html).toContain('Not eligible')
    expect(html).toContain('score below minimum')
    expect(html).toContain('aria-label="failed"')
    expect(html).toContain('aria-label="not evaluated"')
  })

  it('shows hash, signature and revocation on the credential detail', () => {
    const credential: CredentialDetail = {
      credential_id: 'CRED-7K2M9QXR4T1B', learner_id: 'LRN-0000001', course_id: 'CRS-00001',
      completion_id: 'CMP-0000001', rule_id: 'RUL-00001-V1', issuer_id: 'ISS-001', credential_type: 'certificate',
      status: 'revoked', issued_at: '2026-11-14T12:00:00Z', expires_at: null, revoked_at: '2026-11-15T09:00:00Z',
      payload_hash: 'a'.repeat(64), signing_key_id: 'ISS-001-K1', payload: { score: '82.50' }, signature: 'S'.repeat(88),
      schema_version: '1.0', issued_by: 'AA-001',
      revocation: { revocation_id: 'REV-000001', credential_id: 'CRED-7K2M9QXR4T1B', reason_code: 'issued_in_error',
        reason_text: null, requested_by: null, approved_by: 'AA-001', revoked_at: '2026-11-15T09:00:00Z' },
    }
    const html = renderToStaticMarkup(<CredentialView credential={credential} />)
    expect(html).toContain('a'.repeat(64))
    expect(html).toContain('Revoked (issued in error)')
    expect(html).toContain('&quot;score&quot;: &quot;82.50&quot;')
  })

  it('renders valid, revoked and tampered verification results', () => {
    const valid = renderToStaticMarkup(<VerificationResultView response={verifyResponse({})} />)
    expect(valid).toContain('data-result="valid"')
    expect(valid).toContain('Asha Rao')

    const revoked = renderToStaticMarkup(
      <VerificationResultView response={verifyResponse({
        result: 'revoked', message: 'This credential was revoked on 2026-11-15.',
        checks: { found: true, hash_match: true, signature_valid: true, is_revoked: true, is_expired: false },
      })} />,
    )
    expect(revoked).toContain('REVOKED')
    expect(revoked).toContain('revoked on 2026-11-15')

    const tampered = renderToStaticMarkup(
      <VerificationResultView response={verifyResponse({ result: 'tampered', message: 'The credential data has been altered.', credential: null })} />,
    )
    expect(tampered).toContain('TAMPERED')
    expect(tampered).not.toContain('Asha Rao') // altered data is never echoed back
  })

  it('renders evaluation scores against the target', () => {
    const scores = { tp: 3, fp: 0, fn: 0, tn: 97, precision: 1, recall: 1, f1: 1 }
    const evaluation: Evaluation = {
      profile: 'starter', by_type: { issued_before_completion: scores }, overall: { ...scores, tn: 195 },
      false_positives: [], false_negatives: [],
    }
    const html = renderToStaticMarkup(<EvaluationView evaluation={evaluation} />)
    expect(html).toContain('issued before completion')
    expect(html).toContain('100.0%')
    expect(html).toContain('Target met')
    expect(html).toContain('195')
  })

  it('offers the six roles in the role switcher', () => {
    const html = renderToStaticMarkup(
      <RoleSwitcher actor={{ actorId: 'AA-001', role: 'academic_admin' }} onChange={() => {}} />,
    )
    expect(html.match(/<option/g)).toHaveLength(6)
    expect(html).toContain('External Verifier (public)')
  })
})
