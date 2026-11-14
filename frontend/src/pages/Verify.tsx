import { ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { useActor } from '../actor'
import { ApiError, api, toApiError } from '../api/client'
import type { VerifyResponse } from '../api/types'
import { Button, Card, ErrorAlert, Field, Loading } from '../components/ui'
import { VerificationResultView } from '../components/views'
import { inputClass, parsePayload } from '../format'
import type { VerifyPrefill } from '../navigation'

/** Public verification (US-12–US-14): credential ID, optionally a presented payload/signature. */
export function Verify({ prefill }: { prefill?: VerifyPrefill }) {
  const { actor } = useActor()
  const [credentialId, setCredentialId] = useState(prefill?.credentialId ?? '')
  const [verifierName, setVerifierName] = useState('Jordan Lee')
  const [organization, setOrganization] = useState('Acme Corp')
  const [payloadText, setPayloadText] = useState(prefill?.payload ? JSON.stringify(prefill.payload, null, 2) : '')
  const [signature, setSignature] = useState(prefill?.signature ?? '')
  const [response, setResponse] = useState<VerifyResponse | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)

  async function verify() {
    setBusy(true)
    setError(null)
    setResponse(null)
    try {
      let payload
      try {
        payload = parsePayload(payloadText)
      } catch (caught) {
        throw new ApiError(0, 'invalid_payload_json', caught instanceof Error ? caught.message : 'Invalid JSON')
      }
      setResponse(
        await api.verify(actor, {
          credential_id: credentialId,
          payload,
          signature: signature.trim() || null,
          verifier: { name: verifierName, organization: organization || null },
          purpose: 'employment',
        }),
      )
    } catch (caught) {
      setError(toApiError(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card title="Verify a credential">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            void verify()
          }}
        >
          <Field label="Credential ID">
            <input className={`${inputClass} font-mono`} value={credentialId} onChange={(e) => setCredentialId(e.target.value)} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Verifier name">
              <input className={inputClass} value={verifierName} onChange={(e) => setVerifierName(e.target.value)} />
            </Field>
            <Field label="Organisation">
              <input className={inputClass} value={organization} onChange={(e) => setOrganization(e.target.value)} />
            </Field>
          </div>
          <Field label="Presented payload (optional JSON; edit it to simulate tampering)">
            <textarea
              className={`${inputClass} h-40 font-mono text-xs`}
              value={payloadText}
              onChange={(e) => setPayloadText(e.target.value)}
              placeholder="Leave empty to verify the stored payload by ID"
            />
          </Field>
          <Field label="Presented signature (optional)">
            <input className={`${inputClass} font-mono text-xs`} value={signature} onChange={(e) => setSignature(e.target.value)} />
          </Field>
          <Button type="submit" disabled={busy || !credentialId || !verifierName}>
            <ShieldCheck className="h-4 w-4" aria-hidden="true" /> Verify
          </Button>
          <p className="text-xs text-slate-500">
            Public endpoint: as an external verifier no actor headers are sent.
          </p>
        </form>
      </Card>
      <div className="space-y-4">
        {busy && <Loading label="Verifying…" />}
        {error && <ErrorAlert error={error} />}
        {response && <VerificationResultView response={response} />}
      </div>
    </div>
  )
}
