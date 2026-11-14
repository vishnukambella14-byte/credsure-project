import { UserCog } from 'lucide-react'
import { ROLES, defaultActorFor, isRole } from '../actor'
import type { Actor } from '../api/types'
import { inputClass } from '../format'

/** Simulated role switcher (ADR-010): sets X-Actor-Role / X-Actor-Id for every request. */
export function RoleSwitcher({ actor, onChange }: { actor: Actor; onChange: (actor: Actor) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <UserCog className="h-4 w-4 text-slate-400" aria-hidden="true" />
      <label className="sr-only" htmlFor="role">
        Role
      </label>
      <select
        id="role"
        className={`${inputClass} w-auto py-1.5`}
        value={actor.role}
        onChange={(event) => {
          if (isRole(event.target.value)) onChange(defaultActorFor(event.target.value))
        }}
      >
        {ROLES.map((entry) => (
          <option key={entry.role} value={entry.role}>
            {entry.label}
          </option>
        ))}
      </select>
      <label className="sr-only" htmlFor="actor-id">
        Actor ID
      </label>
      <input
        id="actor-id"
        className={`${inputClass} w-36 py-1.5`}
        value={actor.actorId}
        disabled={actor.role === 'external_verifier'}
        onChange={(event) => onChange({ ...actor, actorId: event.target.value.trim() })}
        title="X-Actor-Id (learners use their learner ID)"
      />
    </div>
  )
}
