/**
 * Redux Toolkit store for the versatile main UI.
 *
 * One slice for now (`chat`). Future waves can split conversations /
 * banners / composer into their own slices without changing the public
 * `useAppDispatch` / `useAppSelector` surface.
 *
 * `serializableCheck` is configured to ignore `composerFiles` (File
 * objects) and pending `chat.delta` payloads which can carry non-POJOs
 * during streaming.
 */

import { configureStore } from '@reduxjs/toolkit'
import { chatReducer } from '../features/chat/chatSlice.ts'

export const store = configureStore({
  reducer: {
    chat: chatReducer,
  },
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware({
      serializableCheck: {
        ignoredPaths: ['chat.composerFiles'],
        ignoredActions: [
          'chat/setComposerFiles',
          'chat/addComposerFiles',
        ],
      },
    }),
})

export type RootState = ReturnType<typeof store.getState>
export type AppDispatch = typeof store.dispatch
