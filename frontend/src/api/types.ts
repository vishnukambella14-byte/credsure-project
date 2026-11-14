// TypeScript mirrors of the backend schemas (docs/api_contract.md §4).

export type Role =
  | 'learner'
  | 'instructor'
  | 'academic_admin'
  | 'support_staff'
  | 'platform_admin'
  | 'external_verifier'

export interface Actor {
  actorId: string
  role: Role
}

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface Learner {
  learner_id: string
  given_name: string
  family_name: string
  email: string
  status: string
}

export interface Course {
  course_id: string
  course_code: string
  title: string
  issuer_id: string
  status: string
}

export interface Eligibility {
  learner_id: string
  course_id: string
  eligible: boolean
  rule_id: string | null
  rule_version: number | null
  completion_id: string | null
  reasons: string[]
  checks: Record<string, boolean | null>
  evaluated_at: string
}

export type Payload = Record<string, unknown>

export interface IssuedCredential {
  credential_id: string
  learner_id: string
  course_id: string
  completion_id: string
  rule_id: string
  issuer_id: string
  credential_type: string
  status: string
  issued_at: string
  expires_at: string | null
  payload: Payload
  payload_hash: string
  signature: string
  signing_key_id: string
  attempt_ref: string
}

export interface CredentialSummaryRow {
  credential_id: string
  learner_id: string
  course_id: string
  completion_id: string
  rule_id: string
  issuer_id: string
  credential_type: string
  status: string
  issued_at: string
  expires_at: string | null
  revoked_at: string | null
  payload_hash: string
  signing_key_id: string
}

export interface Revocation {
  revocation_id: string
  credential_id: string
  reason_code: string
  reason_text: string | null
  requested_by: string | null
  approved_by: string
  revoked_at: string
}

export interface CredentialDetail extends CredentialSummaryRow {
  payload: Payload
  signature: string
  schema_version: string
  issued_by: string
  revocation: Revocation | null
}

export interface RevokeRequest {
  reason_code: string
  reason_text?: string | null
  requested_by?: string | null
}

export interface RevokeResponse {
  credential: CredentialSummaryRow
  revocation: Revocation
}

export type VerificationResultCode =
  | 'valid'
  | 'revoked'
  | 'expired'
  | 'tampered'
  | 'invalid_signature'
  | 'not_found'

export interface VerifyRequest {
  credential_id: string
  payload?: Payload | null
  signature?: string | null
  verifier: { name: string; organization?: string | null; email?: string | null }
  purpose?: 'employment' | 'admission' | 'licensing' | 'other' | null
}

export interface VerifyResponse {
  verification_id: string
  credential_id: string | null
  result: VerificationResultCode
  checks: {
    found: boolean
    hash_match: boolean | null
    signature_valid: boolean | null
    is_revoked: boolean | null
    is_expired: boolean | null
  }
  message: string
  credential: {
    credential_type: string
    course_title: string
    learner_name: string
    issuer_name: string
    issued_at: string
    expires_at: string | null
  } | null
  requested_at: string
}

export interface AuditEvent {
  event_id: number
  event_type: string
  entity_type: string
  entity_id: string
  actor_id: string
  actor_role: string
  outcome: string
  reason_code: string | null
  attempt_ref: string | null
  details: Record<string, unknown>
  occurred_at: string
}

export interface Rate {
  value: number
  numerator: string
  denominator: string
}

export interface MetricsSummary {
  computed_at: string
  counts: Record<string, number>
  rates: Record<string, Rate>
  verifications_by_result: Record<string, number>
  credentials_issued_by_month: { month: string; count: number }[]
}

export interface Finding {
  record_type: string
  record_id: string
  anomaly_type: string
  evidence: Record<string, unknown>
}

export interface AnomalyRun {
  run_at: string
  findings_by_type: Record<string, number>
  findings: Finding[]
}

export interface TypeScores {
  tp: number
  fp: number
  fn: number
  tn: number
  precision: number
  recall: number
  f1: number
}

export interface Evaluation {
  profile: string
  by_type: Record<string, TypeScores>
  overall: TypeScores
  false_positives: Omit<Finding, 'evidence'>[]
  false_negatives: Omit<Finding, 'evidence'>[]
}
