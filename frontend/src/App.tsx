import { ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { ActorContext, loadActor, saveActor } from './actor'
import type { Actor } from './api/types'
import { RoleSwitcher } from './components/RoleSwitcher'
import { PAGES, type NavState } from './navigation'
import { Anomalies } from './pages/Anomalies'
import { CredentialDetail } from './pages/CredentialDetail'
import { Dashboard } from './pages/Dashboard'
import { IssueCredential } from './pages/IssueCredential'
import { Verify } from './pages/Verify'

const storage = typeof window !== 'undefined' ? window.localStorage : undefined

function App() {
  const [actor, setActorState] = useState<Actor>(() => loadActor(storage))
  const [nav, setNav] = useState<NavState>({ page: 'dashboard' })
  const [navKey, setNavKey] = useState(0)

  const setActor = (next: Actor) => {
    setActorState(next)
    saveActor(storage, next)
  }
  const navigate = (next: NavState) => {
    setNav(next)
    setNavKey((key) => key + 1) // fresh page state for every navigation
  }
  // Pages remount when the actor or the navigation target changes.
  const pageKey = `${actor.role}:${actor.actorId}:${navKey}`

  return (
    <ActorContext.Provider value={{ actor, setActor }}>
      <div className="min-h-screen bg-slate-50 text-slate-900">
        <header className="border-b border-slate-200 bg-white">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3">
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-900 text-white">
                <ShieldCheck className="h-5 w-5" aria-hidden="true" />
              </span>
              <div>
                <h1 className="text-base font-semibold leading-tight">CredSure</h1>
                <p className="text-xs text-slate-500">Digital Credential Lifecycle Management Platform</p>
              </div>
            </div>
            <RoleSwitcher actor={actor} onChange={setActor} />
          </div>
          <nav className="mx-auto flex max-w-7xl gap-1 overflow-x-auto px-4" aria-label="Main">
            {PAGES.map((page) => (
              <button
                key={page.id}
                onClick={() => navigate({ page: page.id })}
                aria-current={nav.page === page.id ? 'page' : undefined}
                className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium ${
                  nav.page === page.id
                    ? 'border-slate-900 text-slate-900'
                    : 'border-transparent text-slate-500 hover:text-slate-800'
                }`}
              >
                {page.label}
              </button>
            ))}
          </nav>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6" key={pageKey}>
          {nav.page === 'dashboard' && <Dashboard navigate={navigate} />}
          {nav.page === 'issue' && <IssueCredential navigate={navigate} />}
          {nav.page === 'credential' && <CredentialDetail initialId={nav.credentialId} navigate={navigate} />}
          {nav.page === 'verify' && <Verify prefill={nav.verify} />}
          {nav.page === 'anomalies' && <Anomalies />}
        </main>
      </div>
    </ActorContext.Provider>
  )
}

export default App
