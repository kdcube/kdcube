/** apps-config slice — pure reducers only; orchestration lives in ../controller. */
import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import { initialState } from './state.ts';
import type { AppScope, AppSummary, AppConfigView, AgentCapabilities } from '../model/index.ts';

const slice = createSlice({
  name: 'appsConfig',
  initialState,
  reducers: {
    setScope(state, action: PayloadAction<AppScope>) {
      state.scope = action.payload;
    },

    appsLoading(state) {
      state.apps.status = 'loading';
      state.apps.error = null;
    },
    appsLoaded(state, action: PayloadAction<AppSummary[]>) {
      state.apps.status = 'ready';
      state.apps.data = action.payload;
    },
    appsError(state, action: PayloadAction<string>) {
      state.apps.status = 'error';
      state.apps.error = action.payload;
    },

    selectApp(state, action: PayloadAction<string | null>) {
      state.selectedAppId = action.payload;
      // a new app resets its dependent slots
      state.appConfig = { status: 'idle', error: null, data: null };
      state.selectedAgentId = null;
      state.agentCaps = {};
    },
    appConfigLoading(state) {
      state.appConfig.status = 'loading';
      state.appConfig.error = null;
    },
    appConfigLoaded(state, action: PayloadAction<AppConfigView>) {
      state.appConfig.status = 'ready';
      state.appConfig.data = action.payload;
    },
    appConfigError(state, action: PayloadAction<string>) {
      state.appConfig.status = 'error';
      state.appConfig.error = action.payload;
    },

    selectAgent(state, action: PayloadAction<string | null>) {
      state.selectedAgentId = action.payload;
    },
    agentCapsLoading(state, action: PayloadAction<string>) {
      state.agentCaps[action.payload] = { status: 'loading', error: null, data: null };
    },
    agentCapsLoaded(state, action: PayloadAction<{ agentId: string; caps: AgentCapabilities }>) {
      state.agentCaps[action.payload.agentId] = {
        status: 'ready',
        error: null,
        data: action.payload.caps,
      };
    },
    agentCapsError(state, action: PayloadAction<{ agentId: string; error: string }>) {
      state.agentCaps[action.payload.agentId] = {
        status: 'error',
        error: action.payload.error,
        data: null,
      };
    },
  },
});

export const appsConfigActions = slice.actions;
export const appsConfigReducer = slice.reducer;
