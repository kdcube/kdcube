/**
 * Typed Redux hooks + `useStableCallback`. Ported from the in-tree widget
 * (src/app/hooks.ts); the store types now come from the engine package
 * (`@kdcube/components-core/chat`) since the UI binds to the engine's RTK store
 * provided by <ChatStoreProvider>.
 */
import { useDispatch, useSelector } from 'react-redux'
import type { TypedUseSelectorHook } from 'react-redux'
import type { AppDispatch, RootState } from '@kdcube/components-core/chat'
export { useStableCallback } from './useStableCallback.ts'

export const useAppDispatch: () => AppDispatch = useDispatch
export const useAppSelector: TypedUseSelectorHook<RootState> = useSelector
