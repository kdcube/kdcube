// Provider connections — the registry-driven OAuth accounts served by the
// connections_* ops (Slack, Gmail, …). These are the accounts the connections
// named service resolves tokens from; each provider row carries its claim
// tiers and each account its tier coverage, so the panel can offer tiers at
// connect time and additional tiers on reconnect.

import { createAsyncThunk, createSlice, type PayloadAction } from '@reduxjs/toolkit';
import { getOp, postOp } from '../../api/client';
import type {
  ConnectionsCatalogResult,
  ConnectionsDisconnectResult,
  ConnectionsError,
  ConnectionsProviderRow,
  ConnectionsStartOAuthResult,
} from '../../api/types';

export interface ProviderConnectionsState {
  providers: ConnectionsProviderRow[];
  loading: boolean;
  busy: boolean;
  error: string;
}

const initialState: ProviderConnectionsState = {
  providers: [],
  loading: true,
  busy: false,
  error: '',
};

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function resultError(
  result: { error?: ConnectionsError; message?: string } | null | undefined,
  fallback: string,
): string {
  if (result?.message) return result.message;
  const error = result?.error;
  if (typeof error === 'string' && error) return error;
  if (error && typeof error === 'object' && error.message) return error.message;
  return fallback;
}

export const loadProviderConnections = createAsyncThunk<
  ConnectionsProviderRow[],
  void,
  { rejectValue: string }
>(
  'providerConnections/load',
  async (_arg, { rejectWithValue }) => {
    try {
      const res = await getOp<ConnectionsCatalogResult>('connections_catalog');
      if (res?.ok === false) return rejectWithValue(resultError(res, 'Failed to load provider connections'));
      return Array.isArray(res?.providers) ? res.providers : [];
    } catch (e) {
      return rejectWithValue(message(e));
    }
  },
);

export interface StartProviderOAuthArgs {
  provider: string;
  appId?: string;
  // Claim-tier ids to request; the server resolves them to the exact scope
  // union. Omitted for providers without declared tiers (the client app's
  // configured scopes apply).
  tiers?: string[];
}

// Begins OAuth and returns the start payload; the component opens the
// authorize URL so the window.open stays close to the user gesture.
export const startProviderConnectionsOAuth = createAsyncThunk<
  ConnectionsStartOAuthResult,
  StartProviderOAuthArgs,
  { rejectValue: string }
>(
  'providerConnections/startOAuth',
  async ({ provider, appId, tiers }, { rejectWithValue }) => {
    try {
      const payload: Record<string, unknown> = { provider, return_hint: window.location.href };
      if (appId) payload.app_id = appId;
      if (tiers && tiers.length) payload.tiers = tiers;
      const res = await postOp<ConnectionsStartOAuthResult>('connections_start_oauth', payload);
      if (res?.ok === false) return rejectWithValue(resultError(res, 'Failed to start the provider connection'));
      return res || {};
    } catch (e) {
      return rejectWithValue(message(e));
    }
  },
);

export const disconnectProviderConnection = createAsyncThunk<
  ConnectionsDisconnectResult,
  { provider: string; accountId: string },
  { rejectValue: string }
>(
  'providerConnections/disconnect',
  async ({ provider, accountId }, { rejectWithValue }) => {
    try {
      const res = await postOp<ConnectionsDisconnectResult>('connections_disconnect', {
        provider,
        account_id: accountId,
      });
      if (res?.ok === false) return rejectWithValue(resultError(res, 'Failed to disconnect the account'));
      return res || {};
    } catch (e) {
      return rejectWithValue(message(e));
    }
  },
);

const providerConnectionsSlice = createSlice({
  name: 'providerConnections',
  initialState,
  reducers: {
    clearProviderConnectionsError(state) {
      state.error = '';
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(loadProviderConnections.fulfilled, (state, action: PayloadAction<ConnectionsProviderRow[]>) => {
        state.loading = false;
        state.providers = action.payload;
      })
      .addCase(loadProviderConnections.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload ?? 'Failed to load provider connections';
      });

    // Mutations share busy + error handling.
    [startProviderConnectionsOAuth, disconnectProviderConnection].forEach((thunk) => {
      builder
        .addCase(thunk.pending, (state) => {
          state.busy = true;
          state.error = '';
        })
        .addCase(thunk.fulfilled, (state) => {
          state.busy = false;
        })
        .addCase(thunk.rejected, (state, action) => {
          state.busy = false;
          state.error = (action.payload as string) ?? 'Operation failed';
        });
    });
  },
});

export const { clearProviderConnectionsError } = providerConnectionsSlice.actions;
export default providerConnectionsSlice.reducer;
