import { AlertTriangle, CheckCircle2, Loader2, XCircle } from 'lucide-react'
import type { ReactNode } from 'react'
import type { ApiError } from '../api/client'
import { TONES, toneFor } from '../format'

export function StatusBadge({ value, label }: { value: string; label?: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[toneFor(value)]}`}
    >
      {label ?? value.replaceAll('_', ' ')}
    </span>
  )
}

export function Card({ title, actions, children }: { title?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-5 py-3">
          {title && <h2 className="text-sm font-semibold text-slate-900">{title}</h2>}
          {actions}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  )
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <p role="status" className="flex items-center gap-2 text-sm text-slate-500">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> {label}
    </p>
  )
}

export function ErrorAlert({ error }: { error: ApiError }) {
  return (
    <div role="alert" className="flex gap-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
      <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div>
        <p className="font-medium">
          {error.message} <code className="ml-1 text-xs">({error.code})</code>
        </p>
        {error.reasons.length > 0 && (
          <p className="mt-1">Reasons: {error.reasons.map((r) => r.replaceAll('_', ' ')).join(', ')}</p>
        )}
      </div>
    </div>
  )
}

export function SuccessAlert({ children }: { children: ReactNode }) {
  return (
    <div role="status" className="flex gap-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div>{children}</div>
    </div>
  )
}

export function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="flex gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div>{children}</div>
    </div>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium text-slate-700">{label}</span>
      {children}
    </label>
  )
}

export function Button({
  children,
  onClick,
  disabled,
  variant = 'primary',
  type = 'button',
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  variant?: 'primary' | 'secondary' | 'danger'
  type?: 'button' | 'submit'
}) {
  const styles = {
    primary: 'bg-slate-900 text-white hover:bg-slate-700',
    secondary: 'bg-white text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50',
    danger: 'bg-red-600 text-white hover:bg-red-500',
  }[variant]
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium shadow-sm disabled:cursor-not-allowed disabled:opacity-50 ${styles}`}
    >
      {children}
    </button>
  )
}

export function KeyValues({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
      {rows.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="font-medium text-slate-500">{key}</dt>
          <dd className="break-all text-slate-900">{value ?? '—'}</dd>
        </div>
      ))}
    </dl>
  )
}

export function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-80 overflow-auto rounded-lg bg-slate-950 p-3 text-xs leading-relaxed text-slate-100">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

export function Table({ headers, rows, empty = 'No records.' }: { headers: string[]; rows: ReactNode[][]; empty?: string }) {
  if (rows.length === 0) return <p className="text-sm text-slate-500">{empty}</p>
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <thead>
          <tr>
            {headers.map((header) => (
              <th key={header} className="whitespace-nowrap px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((cells, index) => (
            <tr key={index} className="hover:bg-slate-50">
              {cells.map((cell, cellIndex) => (
                <td key={cellIndex} className="whitespace-nowrap px-3 py-2 text-slate-700">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
