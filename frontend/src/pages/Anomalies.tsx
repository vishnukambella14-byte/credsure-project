import { Play, Scale } from 'lucide-react'
import { useState } from 'react'
import { useActor } from '../actor'
import { ApiError, api, toApiError } from '../api/client'
import type { AnomalyRun, Evaluation } from '../api/types'
import { Button, Card, ErrorAlert, Loading, Table } from '../components/ui'
import { EvaluationView } from '../components/views'
import { humanize, inputClass } from '../format'

/** Run detectors A1–A4 and evaluate them against ground truth (US-20, US-21). */
export function Anomalies() {
  const { actor } = useActor()
  const [run, setRun] = useState<AnomalyRun | null>(null)
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null)
  const [profile, setProfile] = useState<'starter' | 'full'>('starter')
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState<'run' | 'evaluate' | null>(null)

  async function act(kind: 'run' | 'evaluate') {
    setBusy(kind)
    setError(null)
    try {
      if (kind === 'run') setRun(await api.runAnomalies(actor))
      else setEvaluation(await api.evaluation(actor, profile))
    } catch (caught) {
      setError(toApiError(caught))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-6">
      <Card
        title="Anomaly detection"
        actions={
          <Button onClick={() => act('run')} disabled={busy !== null}>
            <Play className="h-4 w-4" aria-hidden="true" /> Run detectors
          </Button>
        }
      >
        <p className="mb-4 text-sm text-slate-500">Requires the platform administrator role. Detectors read only stored records.</p>
        {busy === 'run' && <Loading label="Running detectors…" />}
        {run && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {Object.entries(run.findings_by_type).map(([kind, count]) => (
                <div key={kind} className="rounded-lg border border-slate-200 p-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{humanize(kind)}</p>
                  <p className="mt-1 text-2xl font-semibold text-slate-900">{count}</p>
                </div>
              ))}
            </div>
            <Table
              headers={['Anomaly', 'Record type', 'Record ID', 'Evidence']}
              rows={run.findings.slice(0, 50).map((f) => [
                humanize(f.anomaly_type),
                f.record_type,
                <code className="text-xs">{f.record_id}</code>,
                <code className="text-xs text-slate-500">{JSON.stringify(f.evidence)}</code>,
              ])}
              empty="No findings."
            />
            {run.findings.length > 50 && <p className="text-xs text-slate-500">Showing 50 of {run.findings.length} findings.</p>}
          </div>
        )}
      </Card>
      <Card
        title="Evaluation against ground truth"
        actions={
          <div className="flex gap-2">
            <select
              className={`${inputClass} w-auto py-1.5`}
              value={profile}
              onChange={(event) => setProfile(event.target.value === 'full' ? 'full' : 'starter')}
              aria-label="Dataset profile"
            >
              <option value="starter">starter</option>
              <option value="full">full</option>
            </select>
            <Button variant="secondary" onClick={() => act('evaluate')} disabled={busy !== null}>
              <Scale className="h-4 w-4" aria-hidden="true" /> Evaluate
            </Button>
          </div>
        }
      >
        {busy === 'evaluate' && <Loading label="Evaluating…" />}
        {evaluation ? (
          <EvaluationView evaluation={evaluation} />
        ) : (
          <p className="text-sm text-slate-500">Academic or platform administrators can compare findings with the profile's ground truth.</p>
        )}
      </Card>
      {error && <ErrorAlert error={error} />}
    </div>
  )
}
