import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, actorHeaders, api, setApiBase, toApiError } from './client'
import type { Actor } from './types'

const AA: Actor = { actorId: 'AA-001', role: 'academic_admin' }
const PUBLIC: Actor = { actorId: 'anonymous', role: 'external_verifier' }

function mockFetch(status: number, body: unknown) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
  setApiBase('/api/v1')
})

describe('actor headers', () => {
  it('sends the simulated role headers for platform roles', () => {
    expect(actorHeaders(AA)).toEqual({ 'X-Actor-Id': 'AA-001', 'X-Actor-Role': 'academic_admin' })
  })
  it('sends no headers for the public external verifier', () => {
    expect(actorHeaders(PUBLIC)).toEqual({})
  })
})

describe('requests', () => {
  it('calls the documented endpoint with query and headers', async () => {
    const fetchMock = mockFetch(200, { eligible: true })
    await api.eligibility(AA, 'LRN-0000001', 'CRS-00001')
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/v1/eligibility?learner_id=LRN-0000001&course_id=CRS-00001')
    expect((init.headers as Record<string, string>)['X-Actor-Role']).toBe('academic_admin')
    expect(init.method).toBe('GET')
  })

  it('posts JSON bodies', async () => {
    const fetchMock = mockFetch(201, { credential_id: 'CRED-7K2M9QXR4T1B' })
    setApiBase('http://127.0.0.1:5173/api/v1/')
    const issued = await api.issue(AA, { learner_id: 'LRN-0000001', course_id: 'CRS-00001' })
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://127.0.0.1:5173/api/v1/credentials')
    expect(init.body).toBe('{"learner_id":"LRN-0000001","course_id":"CRS-00001"}')
    expect(issued.credential_id).toBe('CRED-7K2M9QXR4T1B')
  })

  it('verifies publicly without actor headers', async () => {
    const fetchMock = mockFetch(200, { result: 'valid' })
    await api.verify(PUBLIC, { credential_id: 'CRED-X', verifier: { name: 'V' } })
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(init.headers).not.toHaveProperty('X-Actor-Id')
  })

  it('turns the error envelope into an ApiError with reasons', async () => {
    mockFetch(422, {
      error: { code: 'not_eligible', message: 'Learner is not eligible.', details: { reasons: ['score_below_minimum'], attempt_ref: 'ATT-1' } },
    })
    const error = await api.issue(AA, { learner_id: 'L', course_id: 'C' }).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(422)
    expect((error as ApiError).code).toBe('not_eligible')
    expect((error as ApiError).reasons).toEqual(['score_below_minimum'])
  })

  it('reports network failures as network_error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
    const error = await api.metrics(AA).catch((e: unknown) => e)
    expect((error as ApiError).code).toBe('network_error')
    expect(toApiError(new Error('x')).status).toBe(0)
  })
})
