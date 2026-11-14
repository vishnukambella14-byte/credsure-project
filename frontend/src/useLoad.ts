import { useEffect, useState } from 'react'
import { ApiError, toApiError } from './api/client'

export type LoadState<T> =
  | { status: 'loading' }
  | { status: 'ready'; data: T }
  | { status: 'error'; error: ApiError }

/**
 * Load data once per `loader` identity (wrap the loader in useCallback). State is only set in
 * promise callbacks, never synchronously inside the effect. Pages remount when the actor changes.
 */
export function useLoad<T>(loader: () => Promise<T>): LoadState<T> {
  const [state, setState] = useState<LoadState<T>>({ status: 'loading' })
  useEffect(() => {
    let active = true
    loader().then(
      (data) => active && setState({ status: 'ready', data }),
      (error: unknown) => active && setState({ status: 'error', error: toApiError(error) }),
    )
    return () => {
      active = false
    }
  }, [loader])
  return state
}
