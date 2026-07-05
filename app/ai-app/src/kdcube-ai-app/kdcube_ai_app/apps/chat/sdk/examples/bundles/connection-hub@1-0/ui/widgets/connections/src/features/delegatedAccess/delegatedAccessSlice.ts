import { createAsyncThunk, createSlice, type PayloadAction } from '@reduxjs/toolkit';
import { getOp, postOp } from '../../api/client';
import type {
  DelegatedAccessCreateResult,
  DelegatedAccessGrantOption,
  DelegatedAccessListResult,
  DelegatedAccessRecord,
  DelegatedAccessResourceOption,
  DelegatedAccessRevokeResult,
} from '../../api/types';

export interface DelegatedAccessState {
  platformUserId: string;
  items: DelegatedAccessRecord[];
  grantOptions: DelegatedAccessGrantOption[];
  resources: DelegatedAccessResourceOption[];
  issuedToken: string;
  issuedHeader: string;
  issuedAccess?: DelegatedAccessRecord;
  loading: boolean;
  busy: boolean;
  error: string;
}

const initialState: DelegatedAccessState = {
  platformUserId: '',
  items: [],
  grantOptions: [],
  resources: [],
  issuedToken: '',
  issuedHeader: '',
  loading: true,
  busy: false,
  error: '',
};

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function resultError(result: { error?: string; message?: string } | null | undefined, fallback: string): string {
  return result?.message || result?.error || fallback;
}

export const loadDelegatedAccess = createAsyncThunk<DelegatedAccessListResult, void, { rejectValue: string }>(
  'delegatedAccess/load',
  async (_arg, { rejectWithValue }) => {
    try {
      const res = await getOp<DelegatedAccessListResult>('delegated_access_list');
      if (res?.ok === false) return rejectWithValue(resultError(res, 'Failed to load delegated access'));
      return res || {};
    } catch (e) {
      return rejectWithValue(message(e));
    }
  },
);

export interface CreateDelegatedAccessArgs {
  label: string;
  resourceGrants: Record<string, string[]>;
  operations?: string[];
  ttlSeconds?: number;
}

export const createDelegatedAccess = createAsyncThunk<
  DelegatedAccessCreateResult,
  CreateDelegatedAccessArgs,
  { rejectValue: string }
>(
  'delegatedAccess/create',
  async ({ label, resourceGrants, operations, ttlSeconds }, { rejectWithValue }) => {
    try {
      const res = await postOp<DelegatedAccessCreateResult>('delegated_access_create', {
        label,
        resource_grants: resourceGrants || {},
        operations: operations || [],
        ttl_seconds: ttlSeconds || undefined,
      });
      if (res?.ok === false) return rejectWithValue(resultError(res, 'Failed to create delegated access'));
      return res || {};
    } catch (e) {
      return rejectWithValue(message(e));
    }
  },
);

export const revokeDelegatedAccess = createAsyncThunk<
  DelegatedAccessRevokeResult,
  { accessId: string },
  { rejectValue: string }
>(
  'delegatedAccess/revoke',
  async ({ accessId }, { rejectWithValue }) => {
    try {
      const res = await postOp<DelegatedAccessRevokeResult>('delegated_access_revoke', { access_id: accessId });
      if (res?.ok === false) return rejectWithValue(resultError(res, 'Failed to revoke delegated access'));
      return res || {};
    } catch (e) {
      return rejectWithValue(message(e));
    }
  },
);

const delegatedAccessSlice = createSlice({
  name: 'delegatedAccess',
  initialState,
  reducers: {
    clearDelegatedAccessError(state) {
      state.error = '';
    },
    clearIssuedDelegatedAccess(state) {
      state.issuedToken = '';
      state.issuedHeader = '';
      state.issuedAccess = undefined;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(loadDelegatedAccess.fulfilled, (state, action: PayloadAction<DelegatedAccessListResult>) => {
        state.loading = false;
        state.platformUserId = action.payload.platform_user_id || '';
        state.items = action.payload.items || [];
        state.grantOptions = action.payload.grant_options || [];
        state.resources = action.payload.resources || [];
      })
      .addCase(loadDelegatedAccess.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload ?? 'Failed to load delegated access';
      });

    builder
      .addCase(createDelegatedAccess.pending, (state) => {
        state.busy = true;
        state.error = '';
        state.issuedToken = '';
        state.issuedHeader = '';
        state.issuedAccess = undefined;
      })
      .addCase(createDelegatedAccess.fulfilled, (state, action: PayloadAction<DelegatedAccessCreateResult>) => {
        state.busy = false;
        state.issuedToken = action.payload.access_token || '';
        state.issuedHeader = action.payload.authorization_header || '';
        state.issuedAccess = action.payload.access;
        if (action.payload.access) {
          state.items = [action.payload.access, ...state.items.filter((item) => item.access_id !== action.payload.access?.access_id)];
        }
      })
      .addCase(createDelegatedAccess.rejected, (state, action) => {
        state.busy = false;
        state.error = action.payload ?? 'Failed to create delegated access';
      })
      .addCase(revokeDelegatedAccess.pending, (state) => {
        state.busy = true;
        state.error = '';
      })
      .addCase(revokeDelegatedAccess.fulfilled, (state, action) => {
        state.busy = false;
        const id = action.meta.arg.accessId;
        state.items = state.items.filter((item) => item.access_id !== id);
        if (state.issuedAccess?.access_id === id) {
          state.issuedToken = '';
          state.issuedHeader = '';
          state.issuedAccess = undefined;
        }
      })
      .addCase(revokeDelegatedAccess.rejected, (state, action) => {
        state.busy = false;
        state.error = action.payload ?? 'Failed to revoke delegated access';
      });
  },
});

export const { clearDelegatedAccessError, clearIssuedDelegatedAccess } = delegatedAccessSlice.actions;
export default delegatedAccessSlice.reducer;
