import { createAsyncThunk, createSlice, type PayloadAction } from '@reduxjs/toolkit';
import { getOp, postOp } from '../../api/client';
import type { DcrAllowlistResult } from '../../api/types';

// Admin editor state for the OAuth dynamic-client-registration redirect
// allowlist (connections.delegated_credentials.oauth.dynamic_client_registration
// .allowed_redirect_uris). DCR runs before any user authenticates, so this list
// is what keeps a registered client's redirect pointed at a known app callback
// or loopback. `allowed` mirrors the backend's platform-admin gate.

export interface DcrAllowlistState {
  uris: string[];
  effective: string[];
  defaults: string[];
  loading: boolean;
  loaded: boolean;
  busy: boolean;
  error: string;
  allowed: boolean;
}

const initialState: DcrAllowlistState = {
  uris: [],
  effective: [],
  defaults: [],
  loading: false,
  loaded: false,
  busy: false,
  error: '',
  allowed: true,
};

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function applyResult(state: DcrAllowlistState, payload: DcrAllowlistResult): void {
  if (payload.ok === false && payload.error === 'platform_admin_required') {
    state.allowed = false;
    return;
  }
  state.allowed = true;
  state.uris = payload.allowed_redirect_uris ?? [];
  state.effective = payload.effective_redirect_uris ?? [];
  state.defaults = payload.defaults ?? [];
}

export const loadDcrAllowlist = createAsyncThunk<DcrAllowlistResult, void, { rejectValue: string }>(
  'dcrAllowlist/load',
  async (_arg, { rejectWithValue }) => {
    try {
      return await getOp<DcrAllowlistResult>('dcr_allowlist_get');
    } catch (e) {
      return rejectWithValue(message(e));
    }
  },
);

export const saveDcrAllowlist = createAsyncThunk<DcrAllowlistResult, string[], { rejectValue: string }>(
  'dcrAllowlist/save',
  async (uris, { rejectWithValue }) => {
    try {
      const res = await postOp<DcrAllowlistResult>('dcr_allowlist_set', { allowed_redirect_uris: uris });
      if (res.ok === false && res.error !== 'platform_admin_required') {
        return rejectWithValue(res.message || res.error || 'Saving the allowlist failed');
      }
      return res;
    } catch (e) {
      return rejectWithValue(message(e));
    }
  },
);

const dcrAllowlistSlice = createSlice({
  name: 'dcrAllowlist',
  initialState,
  reducers: {
    clearDcrAllowlistError(state) {
      state.error = '';
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(loadDcrAllowlist.pending, (state) => {
        state.loading = true;
        state.error = '';
      })
      .addCase(loadDcrAllowlist.fulfilled, (state, action: PayloadAction<DcrAllowlistResult>) => {
        state.loading = false;
        state.loaded = true;
        applyResult(state, action.payload);
      })
      .addCase(loadDcrAllowlist.rejected, (state, action) => {
        state.loading = false;
        state.loaded = true;
        state.error = action.payload || 'Loading the DCR allowlist failed';
      })
      .addCase(saveDcrAllowlist.pending, (state) => {
        state.busy = true;
        state.error = '';
      })
      .addCase(saveDcrAllowlist.fulfilled, (state, action: PayloadAction<DcrAllowlistResult>) => {
        state.busy = false;
        applyResult(state, action.payload);
      })
      .addCase(saveDcrAllowlist.rejected, (state, action) => {
        state.busy = false;
        state.error = action.payload || 'Saving the DCR allowlist failed';
      });
  },
});

export const { clearDcrAllowlistError } = dcrAllowlistSlice.actions;
export default dcrAllowlistSlice.reducer;
