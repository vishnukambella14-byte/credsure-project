// Presentational views (pure: props in, markup out) used by the pages and the Vitest tests.
import { Check, Minus, X } from 'lucide-react'
import type { CredentialDetail, Eligibility, Evaluation, MetricsSummary, VerifyResponse } from '../api/types'
import { humanize, percent } from '../format'
import { JsonBlock, KeyValues, StatusBadge, Table } from './ui'

const COUNT_LABELS: [string, string][] = [
  ['learners', 'Learners'],
  ['courses', 'Courses'],
  ['active_rules', 'Active rules'],
  ['completions', 'Completions'],
  ['credentials', 'Credentials'],
  ['credentials_active', 'Active credentials'],
  ['credentials_revoked', 'Revoked credentials'],
  ['verifications', 'Verifications'],
]

const RATE_LABELS: Record<string, string> = {
  revocation_rate: 'Revocation rate',
  verification_failure_rate: 'Verification failure rate',
  issuance_conversion_rate: 'Issuance conversion rate',
}

export function MetricsView({ metrics }: { metrics: MetricsSummary }) {
  const months = metrics.credentials_issued_by_month.slice(-12)
  const maxMonth = Math.max(1, ...months.map((m) => m.count))
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {COUNT_LABELS.map(([key, label]) => (
          <div key={key} className="rounded-lg border border-slate-200 bg-white p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
            <p className="mt-1 text-2xl font-semibold text-slate-900">{(metrics.counts[key] ?? 0).toLocaleString()}</p>
          </div>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="text-sm font-semibold text-slate-900">Rates</h3>
          {Object.entries(metrics.rates).map(([key, rate]) => (
            <div key={key}>
              <p className="flex justify-between text-sm">
                <span className="text-slate-600">{RATE_LABELS[key] ?? humanize(key)}</span>
                <span className="font-semibold text-slate-900">{percent(rate.value)}</span>
              </p>
              <p className="text-xs text-slate-400">
                {rate.numerator} / {rate.denominator}
              </p>
            </div>
          ))}
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="mb-3 text-sm font-semibold text-slate-900">Verifications by result</h3>
          <ul className="space-y-2 text-sm">
            {Object.entries(metrics.verifications_by_result).map(([result, count]) => (
              <li key={result} className="flex items-center justify-between">
                <StatusBadge value={result} />
                <span className="font-medium text-slate-900">{count.toLocaleString()}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="mb-3 text-sm font-semibold text-slate-900">Credentials issued (last 12 months with issuance)</h3>
          <ul className="space-y-1 text-xs">
            {months.map((m) => (
              <li key={m.month} className="flex items-center gap-2">
                <span className="w-16 text-slate-500">{m.month}</span>
                <span className="h-2 rounded bg-slate-700" style={{ width: `${(m.count / maxMonth) * 100}%` }} />
                <span className="text-slate-700">{m.count}</span>
              </li>
            ))}
            {months.length === 0 && <li className="text-slate-500">No credentials yet.</li>}
          </ul>
        </div>
      </div>
    </div>
  )
}

const CHECK_LABELS: Record<string, string> = {
  learner_active: 'Learner exists and is active',
  course_active: 'Course exists and is active',
  completed: 'Completed attempt recorded',
  completion_not_future: 'Completion is not in the future',
  rule_found: 'Rule in effect at completion time',
  score_ok: 'Score meets the minimum',
  attendance_ok: 'Attendance meets the minimum',
  not_already_issued: 'No active credential yet',
  issuer_ready: 'Issuer active and signing key available',
}

export function EligibilityChecklist({ result }: { result: Eligibility }) {
  return (
    <div className="space-y-3">
      <p className="flex items-center gap-2 text-sm">
        <StatusBadge value={result.eligible ? 'eligible' : 'rejected'} label={result.eligible ? 'Eligible' : 'Not eligible'} />
        {result.rule_id && (
          <span className="text-slate-500">
            Rule {result.rule_id} (v{result.rule_version}) · completion {result.completion_id}
          </span>
        )}
      </p>
      <ul className="space-y-1 text-sm">
        {Object.entries(result.checks).map(([key, value]) => (
          <li key={key} className="flex items-center gap-2">
            {value === true && <Check className="h-4 w-4 text-emerald-600" aria-label="passed" />}
            {value === false && <X className="h-4 w-4 text-red-600" aria-label="failed" />}
            {value === null && <Minus className="h-4 w-4 text-slate-400" aria-label="not evaluated" />}
            <span className={value === false ? 'text-red-700' : 'text-slate-700'}>{CHECK_LABELS[key] ?? humanize(key)}</span>
          </li>
        ))}
      </ul>
      {result.reasons.length > 0 && (
        <p className="text-sm text-red-700">Reasons: {result.reasons.map(humanize).join(', ')}</p>
      )}
    </div>
  )
}

export function CredentialView({ credential }: { credential: CredentialDetail }) {
  return (
    <div className="space-y-4">
      <KeyValues
        rows={[
          ['Credential ID', <code>{credential.credential_id}</code>],
          ['Status', <StatusBadge value={credential.status} />],
          ['Learner', credential.learner_id],
          ['Course', credential.course_id],
          ['Rule / completion', `${credential.rule_id} · ${credential.completion_id}`],
          ['Issuer / key', `${credential.issuer_id} · ${credential.signing_key_id}`],
          ['Issued at', credential.issued_at],
          ['Expires at', credential.expires_at ?? 'Never'],
          ['Revoked at', credential.revoked_at ?? '—'],
          ['SHA-256 payload hash', <code className="text-xs">{credential.payload_hash}</code>],
          ['Ed25519 signature', <code className="text-xs">{credential.signature}</code>],
        ]}
      />
      {credential.revocation && (
        <p className="text-sm text-red-700">
          Revoked ({humanize(credential.revocation.reason_code)}) by {credential.revocation.approved_by}
          {credential.revocation.reason_text ? `: ${credential.revocation.reason_text}` : ''}
        </p>
      )}
      <div>
        <h3 className="mb-2 text-sm font-semibold text-slate-900">Signed payload</h3>
        <JsonBlock value={credential.payload} />
      </div>
    </div>
  )
}

const RESULT_STYLES: Record<string, string> = {
  green: 'border-emerald-300 bg-emerald-50 text-emerald-900',
  red: 'border-red-300 bg-red-50 text-red-900',
  amber: 'border-amber-300 bg-amber-50 text-amber-900',
}

export function VerificationResultView({ response }: { response: VerifyResponse }) {
  const tone = response.result === 'valid' ? 'green' : ['expired', 'not_found'].includes(response.result) ? 'amber' : 'red'
  const flags: [string, boolean | null][] = [
    ['Credential found', response.checks.found],
    ['Hash matches', response.checks.hash_match],
    ['Signature valid', response.checks.signature_valid],
    ['Revoked', response.checks.is_revoked],
    ['Expired', response.checks.is_expired],
  ]
  return (
    <div className={`space-y-3 rounded-xl border p-5 ${RESULT_STYLES[tone]}`} data-result={response.result}>
      <p className="flex items-center gap-2 text-lg font-semibold">
        <StatusBadge value={response.result} label={humanize(response.result).toUpperCase()} />
        {response.message}
      </p>
      <ul className="grid grid-cols-2 gap-1 text-sm sm:grid-cols-5">
        {flags.map(([label, value]) => (
          <li key={label}>
            {label}: <strong>{value === null ? '—' : value ? 'yes' : 'no'}</strong>
          </li>
        ))}
      </ul>
      {response.credential && (
        <KeyValues
          rows={[
            ['Learner', response.credential.learner_name],
            ['Course', response.credential.course_title],
            ['Issuer', response.credential.issuer_name],
            ['Type', response.credential.credential_type],
            ['Issued', response.credential.issued_at],
            ['Expires', response.credential.expires_at ?? 'Never'],
          ]}
        />
      )}
      <p className="text-xs opacity-70">
        Verification {response.verification_id} · {response.requested_at}
      </p>
    </div>
  )
}

export function EvaluationView({ evaluation }: { evaluation: Evaluation }) {
  const rows = [...Object.entries(evaluation.by_type), ['overall', evaluation.overall] as const]
  return (
    <div className="space-y-4">
      <Table
        headers={['Anomaly', 'TP', 'FP', 'FN', 'TN', 'Precision', 'Recall', 'F1']}
        rows={rows.map(([kind, s]) => [
          <span className={kind === 'overall' ? 'font-semibold' : ''}>{humanize(kind)}</span>,
          s.tp,
          s.fp,
          s.fn,
          s.tn,
          percent(s.precision),
          percent(s.recall),
          percent(s.f1),
        ])}
      />
      <p className="text-sm text-slate-600">
        Target: recall 100% and precision ≥ 95%.{' '}
        {evaluation.overall.recall === 1 && evaluation.overall.precision >= 0.95 ? (
          <StatusBadge value="success" label="Target met" />
        ) : (
          <StatusBadge value="rejected" label="Target not met" />
        )}
      </p>
      {(evaluation.false_positives.length > 0 || evaluation.false_negatives.length > 0) && (
        <Table
          headers={['Kind', 'Record type', 'Record ID', 'Anomaly']}
          rows={[
            ...evaluation.false_positives.map((f) => ['False positive', f.record_type, f.record_id, humanize(f.anomaly_type)]),
            ...evaluation.false_negatives.map((f) => ['False negative', f.record_type, f.record_id, humanize(f.anomaly_type)]),
          ]}
        />
      )}
    </div>
  )
}
