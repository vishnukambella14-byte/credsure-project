// Fetch wrapper for the CredSure REST API (docs/architecture.md §6, docs/api_contract.md).
// Requests go to /api/v1 (the Vite dev server proxies /api to FastAPI). Actor headers simulate
// roles (ADR-010); the public verifier sends none. Error envelopes become ApiError.

import type {
  Actor,
  AnomalyRun,
  AuditEvent,
  Course,
  CredentialDetail,
  CredentialSummaryRow,
  Eligibility,
  Evaluation,
  IssuedCredential,
  Learner,
  MetricsSummary,
  Page,
  RevokeRequest,
  RevokeResponse,
  VerifyRequest,
  VerifyResponse,
} from './types'

export class ApiError extends Error {
  status: number
  code: string
  details: unknown

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }

  /** Eligibility reasons carried by not_eligible / credential_already_issued errors. */
  get reasons(): string[] {
    const details = this.details as { reasons?: unknown } | undefined
    return Array.isArray(details?.reasons) ? (details.reasons as string[]) : []
  }
}

let apiBase = '/api/v1'

/** Override the base URL (used by tests running outside the browser). */
export function setApiBase(base: string): void {
  apiBase = base.replace(/\/$/, '')
}

/** X-Actor-* headers; the anonymous external verifier sends none (public endpoints). */
export function actorHeaders(actor: Actor): Record<string, string> {
  if (actor.role === 'external_verifier') return {}
  return { 'X-Actor-Id': actor.actorId, 'X-Actor-Role': actor.role }
}

type Query = Record<string, string | number | undefined | null>

function withQuery(path: string, query?: Query): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `${path}?${text}` : path
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  const message = error instanceof Error ? error.message : String(error)
  return new ApiError(0, 'network_error', `The API could not be reached: ${message}`)
}

async function request<T>(
  method: 'GET' | 'POST',
  path: string,
  actor: Actor,
  options: { body?: unknown; query?: Query } = {},
): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${apiBase}${withQuery(path, options.query)}`, {
      method,
      headers: {
        Accept: 'application/json',
        ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...actorHeaders(actor),
      },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    })
  } catch (error) {
    throw toApiError(error)
  }
  const text = await response.text()
  const data: unknown = text ? JSON.parse(text) : null
  if (!response.ok) {
    const envelope = (data as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error
    throw new ApiError(
      response.status,
      envelope?.code ?? `http_${response.status}`,
      envelope?.message ?? response.statusText,
      envelope?.details,
    )
  }
  return data as T
}

export const api = {
  metrics: (actor: Actor) => request<MetricsSummary>('GET', '/metrics/summary', actor),
  learners: (actor: Actor, search?: string) =>
    request<Page<Learner>>('GET', '/learners', actor, { query: { search, limit: 50 } }),
  courses: (actor: Actor) => request<Page<Course>>('GET', '/courses', actor, { query: { limit: 200 } }),
  eligibility: (actor: Actor, learnerId: string, courseId: string) =>
    request<Eligibility>('GET', '/eligibility', actor, {
      query: { learner_id: learnerId, course_id: courseId },
    }),
  issue: (actor: Actor, body: { learner_id: string; course_id: string }) =>
    request<IssuedCredential>('POST', '/credentials', actor, { body }),
  credentials: (actor: Actor, query: Query = {}) =>
    request<Page<CredentialSummaryRow>>('GET', '/credentials', actor, { query }),
  credential: (actor: Actor, credentialId: string) =>
    request<CredentialDetail>('GET', `/credentials/${encodeURIComponent(credentialId)}`, actor),
  revoke: (actor: Actor, credentialId: string, body: RevokeRequest) =>
    request<RevokeResponse>('POST', `/credentials/${encodeURIComponent(credentialId)}/revoke`, actor, { body }),
  verify: (actor: Actor, body: VerifyRequest) => request<VerifyResponse>('POST', '/verifications', actor, { body }),
  auditEvents: (actor: Actor, query: Query = {}) =>
    request<Page<AuditEvent>>('GET', '/audit-events', actor, { query }),
  runAnomalies: (actor: Actor) => request<AnomalyRun>('POST', '/anomalies/run', actor),
  evaluation: (actor: Actor, profile: 'starter' | 'full') =>
    request<Evaluation>('GET', '/anomalies/evaluation', actor, { query: { profile } }),
}
