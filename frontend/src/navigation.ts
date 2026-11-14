// State-based navigation (ADR-015: no router dependency).
import type { Payload } from './api/types'

export type PageId = 'dashboard' | 'issue' | 'credential' | 'verify' | 'anomalies'

export interface VerifyPrefill {
  credentialId: string
  payload?: Payload
  signature?: string
}

export interface NavState {
  page: PageId
  credentialId?: string
  verify?: VerifyPrefill
}

export type Navigate = (state: NavState) => void

export const PAGES: { id: PageId; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'issue', label: 'Issue credential' },
  { id: 'credential', label: 'Credential detail' },
  { id: 'verify', label: 'Verify' },
  { id: 'anomalies', label: 'Anomalies' },
]
