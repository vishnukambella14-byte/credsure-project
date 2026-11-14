// Non-component helpers shared by components (kept separate for React Fast Refresh).
import type { Payload } from './api/types'

export const TONES = {
  green: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  red: 'bg-red-50 text-red-700 ring-red-600/20',
  amber: 'bg-amber-50 text-amber-800 ring-amber-600/20',
  slate: 'bg-slate-100 text-slate-700 ring-slate-500/20',
} as const

export type Tone = keyof typeof TONES

/** Status colour for credential statuses and verification results. */
export function toneFor(value: string): Tone {
  if (['valid', 'active', 'success', 'eligible'].includes(value)) return 'green'
  if (['revoked', 'tampered', 'invalid_signature', 'rejected', 'error'].includes(value)) return 'red'
  if (['expired', 'not_found', 'draft'].includes(value)) return 'amber'
  return 'slate'
}

export const inputClass =
  'w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500'

export const percent = (value: number) => `${(value * 100).toFixed(1)}%`
export const humanize = (value: string) => value.replaceAll('_', ' ')

/** Parse a pasted payload; returns null for an empty field, throws a readable error otherwise. */
export function parsePayload(text: string): Payload | null {
  if (!text.trim()) return null
  const value: unknown = JSON.parse(text)
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('The payload must be a JSON object.')
  }
  return value as Payload
}
