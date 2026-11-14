import { describe, expect, it } from 'vitest'
import { DEFAULT_ACTOR, ROLES, defaultActorFor, loadActor, saveActor } from './actor'

function memoryStorage(initial: Record<string, string> = {}) {
  const data = { ...initial }
  return {
    data,
    getItem: (key: string) => data[key] ?? null,
    setItem: (key: string, value: string) => {
      data[key] = value
    },
  }
}

describe('simulated actors', () => {
  it('offers exactly the six documented roles', () => {
    expect(ROLES.map((r) => r.role).sort()).toEqual(
      ['academic_admin', 'external_verifier', 'instructor', 'learner', 'platform_admin', 'support_staff'],
    )
  })

  it('persists and restores the actor', () => {
    const storage = memoryStorage()
    saveActor(storage, { actorId: 'LRN-0000042', role: 'learner' })
    expect(loadActor(storage)).toEqual({ actorId: 'LRN-0000042', role: 'learner' })
  })

  it('falls back to the default actor for missing, unknown or corrupt values', () => {
    expect(loadActor(undefined)).toEqual(DEFAULT_ACTOR)
    expect(loadActor(memoryStorage({ 'credsure.actor': '{"role":"root","actorId":"x"}' }))).toEqual(DEFAULT_ACTOR)
    expect(loadActor(memoryStorage({ 'credsure.actor': 'not json' }))).toEqual(DEFAULT_ACTOR)
  })

  it('uses the anonymous identity for the public verifier', () => {
    expect(defaultActorFor('external_verifier')).toEqual({ actorId: 'anonymous', role: 'external_verifier' })
  })
})
