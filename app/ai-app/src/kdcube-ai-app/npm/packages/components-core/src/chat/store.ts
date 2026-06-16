/**
 * Redux Toolkit store factory for one chat engine.
 *
 * Unlike the widget's module-singleton `app/store.ts`, this is a FACTORY:
 * `createChatStore()` returns a fresh store per engine, so a host can run more
 * than one chat on a page. `serializableCheck` ignores `composerFiles` (File
 * objects) and the file-setting actions, exactly as the widget did.
 */
import { configureStore } from '@reduxjs/toolkit'
import { chatReducer } from './slice.ts'

export function createChatStore() {
  return configureStore({
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
}

export type ChatStore = ReturnType<typeof createChatStore>
export type RootState = ReturnType<ChatStore['getState']>
export type AppDispatch = ChatStore['dispatch']
