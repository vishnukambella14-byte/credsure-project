import { useCallback } from 'react'
import { useActor } from '../actor'
import { api } from '../api/client'
import { Card, ErrorAlert, Loading, StatusBadge, Table } from '../components/ui'
import { MetricsView } from '../components/views'
import type { Navigate } from '../navigation'
import { useLoad } from '../useLoad'

/** Metrics from GET /metrics/summary plus the most recent credentials. */
export function Dashboard({ navigate }: { navigate: Navigate }) {
  const { actor } = useActor()
  const metrics = useLoad(useCallback(() => api.metrics(actor), [actor]))
  const recent = useLoad(useCallback(() => api.credentials(actor, { limit: 10 }), [actor]))

  return (
    <div className="space-y-6">
      <Card title="Platform metrics (computed live from source records)">
        {metrics.status === 'loading' && <Loading />}
        {metrics.status === 'error' && <ErrorAlert error={metrics.error} />}
        {metrics.status === 'ready' && <MetricsView metrics={metrics.data} />}
      </Card>
      <Card title="Most recent credentials">
        {recent.status === 'loading' && <Loading />}
        {recent.status === 'error' && <ErrorAlert error={recent.error} />}
        {recent.status === 'ready' && (
          <Table
            headers={['Credential', 'Learner', 'Course', 'Issued', 'Status']}
            rows={recent.data.items.map((c) => [
              <button
                className="font-mono text-xs text-slate-900 underline decoration-slate-300 hover:decoration-slate-900"
                onClick={() => navigate({ page: 'credential', credentialId: c.credential_id })}
              >
                {c.credential_id}
              </button>,
              c.learner_id,
              c.course_id,
              c.issued_at,
              <StatusBadge value={c.status} />,
            ])}
            empty="No credentials yet."
          />
        )}
      </Card>
    </div>
  )
}
