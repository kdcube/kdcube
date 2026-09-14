import { useCallback, useEffect, useRef } from 'react'

/** A stable function reference that always calls the latest implementation. */
export function useStableCallback<A extends unknown[], R>(
  fn: (...args: A) => R,
): (...args: A) => R {
  const ref = useRef(fn)
  useEffect(() => {
    ref.current = fn
  }, [fn])
  return useCallback((...args: A) => ref.current(...args), [])
}
