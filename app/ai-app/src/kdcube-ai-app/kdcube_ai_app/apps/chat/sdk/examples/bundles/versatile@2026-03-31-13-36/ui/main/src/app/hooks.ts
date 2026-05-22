/**
 * Typed Redux hooks — use these everywhere instead of the untyped
 * `useDispatch` / `useSelector` from `react-redux`.
 *
 * Also exports `useStableCallback`, a small polyfill for the React
 * `useEvent` proposal. It returns a stable function reference that
 * always invokes the latest implementation, so callbacks passed to
 * memoized children don't bust their memoization on every parent
 * re-render. Use for event handlers — not for values that need to be
 * read synchronously during render.
 */

import { useCallback, useEffect, useRef } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import type { TypedUseSelectorHook } from 'react-redux'
import type { AppDispatch, RootState } from './store.ts'

export const useAppDispatch: () => AppDispatch = useDispatch
export const useAppSelector: TypedUseSelectorHook<RootState> = useSelector

export function useStableCallback<A extends unknown[], R>(
  fn: (...args: A) => R,
): (...args: A) => R {
  const ref = useRef(fn)
  useEffect(() => {
    ref.current = fn
  }, [fn])
  return useCallback((...args: A) => ref.current(...args), [])
}
