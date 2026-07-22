/** apps-config store factory — a fresh store per mount (mirrors chat/store). */
import { configureStore } from '@reduxjs/toolkit';
import { appsConfigReducer } from './slice.ts';

export function createAppsConfigStore() {
  return configureStore({
    reducer: { appsConfig: appsConfigReducer },
  });
}

export type AppsConfigStore = ReturnType<typeof createAppsConfigStore>;
export type AppsConfigRootState = ReturnType<AppsConfigStore['getState']>;
export type AppsConfigDispatch = AppsConfigStore['dispatch'];
