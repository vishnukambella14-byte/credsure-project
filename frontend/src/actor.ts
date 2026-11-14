// Simulated actors (ADR-010): the six documented roles, editable actor IDs, persisted locally.
import { createContext, useContext } from 'react'
import type { Actor, Role } from './api/types'

export const ROLES: { role: Role; label: string; defaultId: string }[] = [
  { role: 'academic_admin', label: 'Academic Administrator', defaultId: 'AA-001' },
  { role: 'platform_admin', label: 'Platform Administrator', defaultId: 'PA-001' },
  { role: 'support_staff', label: 'Support Staff', defaultId: 'SUP-001' },
  { role: 'instructor', label: 'Instructor', defaultId: 'INS-001' },
  { role: 'learner', label: 'Learner', defaultId: 'LRN-0000001' },
  { role: 'external_verifier', label: 'External Verifier (public)', defaultId: 'anonymous' },
]

export const DEFAULT_ACTOR: Actor = { actorId: 'AA-001', role: 'academic_admin' }
const STORAGE_KEY = 'credsure.actor'

export function isRole(value: unknown): value is Role {
  return ROLES.some((entry) => entry.role === value)
}

export function defaultActorFor(role: Role): Actor {
  return { role, actorId: ROLES.find((entry) => entry.role === role)?.defaultId ?? 'anonymous' }
}

export function loadActor(storage: Pick<Storage, 'getItem'> | undefined): Actor {
  try {
    const parsed = JSON.parse(storage?.getItem(STORAGE_KEY) ?? 'null') as Partial<Actor> | null
    if (parsed && isRole(parsed.role) && typeof parsed.actorId === 'string' && parsed.actorId) {
      return { role: parsed.role, actorId: parsed.actorId }
    }
  } catch {
    // ignore corrupt storage and fall back to the default actor
  }
  return DEFAULT_ACTOR
}

export function saveActor(storage: Pick<Storage, 'setItem'> | undefined, actor: Actor): void {
  storage?.setItem(STORAGE_KEY, JSON.stringify(actor))
}

export interface ActorContextValue {
  actor: Actor
  setActor: (actor: Actor) => void
}

export const ActorContext = createContext<ActorContextValue>({ actor: DEFAULT_ACTOR, setActor: () => {} })

export function useActor(): ActorContextValue {
  return useContext(ActorContext)
}
