import { createAsyncThunk, createSlice, type PayloadAction } from '@reduxjs/toolkit';
import { getOp } from '../../api/client';
import type { DelegatedAccessMapResult } from '../../api/types';

// Read-only admin view: the named-services <-> grants mapping resolved from
// live config by the `delegated_access_map` operation. Loaded lazily when the
// tab first activates; `allowed` mirrors the backend's platform-admin gate.

export interface AccessMapState {
  data: DelegatedAccessMapResult | null;
  loading: boolean;
  loaded: boolean;
  error: string;
  allowed: boolean;
}

const initialState: AccessMapState = {
  data: null,
  loading: false,
  loaded: false,
  error: '',
  allowed: true,
};

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export const loadAccessMap = createAsyncThunk<DelegatedAccessMapResult, void, { rejectValue: string }>(
  'accessMap/load',
  async (_arg, { rejectWithValue }) => {
    try {
      return await getOp<DelegatedAccessMapResult>('delegated_access_map');
    } catch (e) {
      return rejectWithValue(message(e));
    }
  },
);

const accessMapSlice = createSlice({
  name: 'accessMap',
  initialState,
  reducers: {
    clearAccessMapError(state) {
      state.error = '';
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(loadAccessMap.pending, (state) => {
        state.loading = true;
        state.error = '';
      })
      .addCase(loadAccessMap.fulfilled, (state, action: PayloadAction<DelegatedAccessMapResult>) => {
        state.loading = false;
        state.loaded = true;
        if (action.payload.ok === false && action.payload.error === 'platform_admin_required') {
          state.allowed = false;
          state.data = null;
          return;
        }
        state.allowed = true;
        state.data = action.payload;
      })
      .addCase(loadAccessMap.rejected, (state, action) => {
        state.loading = false;
        state.loaded = true;
        state.error = action.payload || 'Loading the access map failed';
      });
  },
});

export const { clearAccessMapError } = accessMapSlice.actions;
export default accessMapSlice.reducer;
