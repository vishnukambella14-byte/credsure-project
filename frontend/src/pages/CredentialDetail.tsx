import { Ban, RefreshCw, ShieldCheck } from 'lucide-react'
import { useCallback, useState } from 'react'
import { useActor } from '../actor'
import { ApiError, api, toApiError } from '../api/client'
import type { AuditEvent, CredentialDetail as Detail } from '../api/types'
import { Button, Card, ErrorAlert, Field, Loading, StatusBadge, SuccessAlert, Table } from '../components/ui'
import { CredentialView } from '../components/views'
import { humanize, inputClass } from '../format'
import type { Navigate } from '../navigation'
import { useLoad } from '../useLoad'

const REASONS = ['issued_in_error', 'academic_misconduct', 'learner_request', 'data_correction', 'fraud', 'other']

interface Loaded {
  detail: Detail
  events: AuditEvent[] | null
}

/** Payload, hash, signature, revoke dialog and audit trail (FR-28, US-06, US-24). */
export function CredentialDetail({ initialId, navigate }: { initialId?: string; navigate: Navigate }) {
  const { actor } = useActor()
  const [credentialId, setCredentialId] = useState(initialId ?? '')
  const [manual, setManual] = useState<Loaded | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const [reason, setReason] = useState('issued_in_error')
  const [reasonText, setReasonText] = useState('')
  const [revoked, setRevoked] = useState(false)

  const fetchAll = useCallback(
    async (id: string): Promise<Loaded> => {
      const detail = await api.credential(actor, id)
      let events: AuditEvent[] | null
      try {
        events = (await api.auditEvents(actor, { entity_id: detail.credential_id, limit: 50 })).items
      } catch {
        events = null // the audit log is restricted to administrators and support staff
      }
      return { detail, events }
    },
    [actor],
  )
  // A credential opened from another screen loads automatically.
  const initial = useLoad(useCallback(() => (initialId ? fetchAll(initialId) : Promise.resolve(null)), [fetchAll, initialId]))
  const current = manual ?? (initial.status === 'ready' ? initial.data : null)
  const detail = current?.detail ?? null
  const events = current?.events ?? null
  const shownError = error ?? (!manual && initial.status === 'error' ? initial.error : null)

  async function load(id = credentialId) {
    if (!id) return
    setBusy(true)
    setError(null)
    try {
      setManual(await fetchAll(id.trim()))
    } catch (caught) {
      setError(toApiError(caught))
    } finally {
      setBusy(false)
    }
  }

  async function revoke() {
    if (!detail) return
    setBusy(true)
    setError(null)
    try {
      await api.revoke(actor, detail.credential_id, { reason_code: reason, reason_text: reasonText || null })
      setRevoked(true)
      setManual(await fetchAll(detail.credential_id))
    } catch (caught) {
      setError(toApiError(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <Card title="Credential lookup">
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            setRevoked(false)
            void load()
          }}
        >
          <input
            className={`${inputClass} max-w-sm font-mono`}
            placeholder="CRED-XXXXXXXXXXXX"
            value={credentialId}
            onChange={(event) => setCredentialId(event.target.value)}
          />
          <Button type="submit" disabled={busy || !credentialId}>
            <RefreshCw className="h-4 w-4" aria-hidden="true" /> Load
          </Button>
        </form>
        {!detail && !shownError && initial.status !== 'loading' && (
          <p className="mt-3 text-sm text-slate-500">Enter a credential ID, or open one from the dashboard.</p>
        )}
      </Card>
      {(busy || (initialId && !manual && initial.status === 'loading')) && <Loading />}
      {shownError && <ErrorAlert error={shownError} />}
      {revoked && <SuccessAlert>The credential has been revoked. Verify it again to see the result.</SuccessAlert>}
      {detail && (
        <div className="grid gap-6 xl:grid-cols-3">
          <div className="xl:col-span-2">
            <Card
              title={`Credential ${detail.credential_id}`}
              actions={
                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" onClick={() => navigate({ page: 'verify', verify: { credentialId: detail.credential_id } })}>
                    <ShieldCheck className="h-4 w-4" aria-hidden="true" /> Verify by ID
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() =>
                      navigate({
                        page: 'verify',
                        verify: { credentialId: detail.credential_id, payload: detail.payload, signature: detail.signature },
                      })
                    }
                  >
                    Verify with payload
                  </Button>
                </div>
              }
            >
              <CredentialView credential={detail} />
            </Card>
          </div>
          <div className="space-y-6">
            <Card title="Revoke">
              {detail.status === 'revoked' ? (
                <p className="text-sm text-slate-600">
                  <StatusBadge value="revoked" /> Revocation is permanent; a revoked credential cannot be reinstated.
                </p>
              ) : (
                <div className="space-y-3">
                  <Field label="Reason">
                    <select className={inputClass} value={reason} onChange={(event) => setReason(event.target.value)}>
                      {REASONS.map((r) => (
                        <option key={r} value={r}>
                          {humanize(r)}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label={reason === 'other' ? 'Explanation (required)' : 'Explanation (optional)'}>
                    <input className={inputClass} value={reasonText} onChange={(event) => setReasonText(event.target.value)} />
                  </Field>
                  <Button variant="danger" onClick={revoke} disabled={busy}>
                    <Ban className="h-4 w-4" aria-hidden="true" /> Revoke credential
                  </Button>
                  <p className="text-xs text-slate-500">Requires the academic administrator role.</p>
                </div>
              )}
            </Card>
            <Card title="Audit trail">
              {events === null ? (
                <p className="text-sm text-slate-500">Visible to administrators and support staff.</p>
              ) : (
                <Table
                  headers={['When', 'Event', 'Outcome', 'Actor']}
                  rows={events.map((e) => [e.occurred_at, e.event_type, <StatusBadge value={e.outcome} />, e.actor_id])}
                  empty="No events."
                />
              )}
            </Card>
          </div>
        </div>
      )}
    </div>
  )
}
