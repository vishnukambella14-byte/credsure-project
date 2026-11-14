import { BadgeCheck, ListChecks, Search } from 'lucide-react'
import { useCallback, useState } from 'react'
import { useActor } from '../actor'
import { ApiError, api, toApiError } from '../api/client'
import type { Eligibility, IssuedCredential, Learner } from '../api/types'
import { Button, Card, ErrorAlert, Field, Loading, SuccessAlert } from '../components/ui'
import { EligibilityChecklist } from '../components/views'
import { inputClass } from '../format'
import type { Navigate } from '../navigation'
import { useLoad } from '../useLoad'

/** Learner/course pickers → eligibility checklist → issue (US-04, US-05). */
export function IssueCredential({ navigate }: { navigate: Navigate }) {
  const { actor } = useActor()
  const courses = useLoad(useCallback(() => api.courses(actor), [actor]))
  const [search, setSearch] = useState('')
  const [learners, setLearners] = useState<Learner[] | null>(null)
  const [learnerId, setLearnerId] = useState('')
  const [courseId, setCourseId] = useState('')
  const [eligibility, setEligibility] = useState<Eligibility | null>(null)
  const [issued, setIssued] = useState<IssuedCredential | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)

  async function run<T>(action: () => Promise<T>, onDone: (value: T) => void) {
    setBusy(true)
    setError(null)
    try {
      onDone(await action())
    } catch (caught) {
      setError(toApiError(caught))
    } finally {
      setBusy(false)
    }
  }

  const searchLearners = () => run(() => api.learners(actor, search), (page) => setLearners(page.items))
  const checkEligibility = () =>
    run(() => api.eligibility(actor, learnerId, courseId), (result) => {
      setEligibility(result)
      setIssued(null)
    })
  const issue = () =>
    run(() => api.issue(actor, { learner_id: learnerId, course_id: courseId }), (credential) => {
      setIssued(credential)
      setEligibility(null)
    })

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card title="1. Choose learner and course">
        <div className="space-y-4">
          <div className="flex gap-2">
            <input
              className={inputClass}
              placeholder="Search learners by name, email or ID"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && searchLearners()}
            />
            <Button variant="secondary" onClick={searchLearners} disabled={busy}>
              <Search className="h-4 w-4" aria-hidden="true" /> Search
            </Button>
          </div>
          <Field label="Learner ID">
            <input
              className={inputClass}
              list="learner-options"
              value={learnerId}
              onChange={(event) => setLearnerId(event.target.value.trim())}
              placeholder="LRN-0000001"
            />
            <datalist id="learner-options">
              {(learners ?? []).map((l) => (
                <option key={l.learner_id} value={l.learner_id}>
                  {l.given_name} {l.family_name} · {l.email}
                </option>
              ))}
            </datalist>
          </Field>
          {learners && <p className="text-xs text-slate-500">{learners.length} learner(s) found; pick one above.</p>}
          <Field label="Course">
            {courses.status === 'loading' && <Loading />}
            {courses.status === 'error' && <ErrorAlert error={courses.error} />}
            {courses.status === 'ready' && (
              <select className={inputClass} value={courseId} onChange={(event) => setCourseId(event.target.value)}>
                <option value="">Select a course…</option>
                {courses.data.items.map((c) => (
                  <option key={c.course_id} value={c.course_id}>
                    {c.course_id} · {c.course_code} · {c.title}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={checkEligibility} disabled={busy || !learnerId || !courseId}>
              <ListChecks className="h-4 w-4" aria-hidden="true" /> Check eligibility
            </Button>
            <Button onClick={issue} disabled={busy || !learnerId || !courseId}>
              <BadgeCheck className="h-4 w-4" aria-hidden="true" /> Issue credential
            </Button>
          </div>
        </div>
      </Card>
      <Card title="2. Result">
        <div className="space-y-4">
          {busy && <Loading label="Working…" />}
          {error && <ErrorAlert error={error} />}
          {eligibility && <EligibilityChecklist result={eligibility} />}
          {issued && (
            <SuccessAlert>
              <p className="font-medium">
                Credential <code>{issued.credential_id}</code> issued and signed.
              </p>
              <p className="mt-1 break-all text-xs">SHA-256: {issued.payload_hash}</p>
              <p className="text-xs">Attempt {issued.attempt_ref}</p>
              <div className="mt-3">
                <Button onClick={() => navigate({ page: 'credential', credentialId: issued.credential_id })}>
                  View credential
                </Button>
              </div>
            </SuccessAlert>
          )}
          {!busy && !error && !eligibility && !issued && (
            <p className="text-sm text-slate-500">Check eligibility first, then issue. Issuing requires the academic administrator role.</p>
          )}
        </div>
      </Card>
    </div>
  )
}
