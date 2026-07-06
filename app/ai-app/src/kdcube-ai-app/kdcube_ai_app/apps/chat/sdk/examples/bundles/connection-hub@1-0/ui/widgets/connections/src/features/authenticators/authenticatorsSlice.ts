import { createAsyncThunk, createSlice, type PayloadAction } from '@reduxjs/toolkit';
import { getOp, postOp } from '../../api/client';
import type {
  AuthenticatorMutationResult,
  AuthenticatorRow,
  AuthenticatorsListResult,
  SupportedAuthenticatorProvider,
} from '../../api/types';

export interface AuthenticatorsState {
  items: AuthenticatorRow[];
  supportedProviders: SupportedAuthenticatorProvider[];
  loading: boolean;
  busy: boolean;
  error: string;
  // Operator surface: false when the backend answered platform_admin_required.
  allowed: boolean;
}

const initialState: AuthenticatorsState = {
  items: [],
  supportedProviders: [],
  loading: true,
  busy: false,
  error: '',
  allowed: true,
};

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export const loadAuthenticators = createAsyncThunk<AuthenticatorsListResult, void, { rejectValue: string }>(
  'authenticators/load',
  async (_arg, { rejectWithValue }) => {
    try {
      return await getOp<AuthenticatorsListResult>('authenticators_list');
    } catch (e) {
      return rejectWithValue(message(e));
    }
  },
);

export interface UpsertAuthenticatorArgs {
  authenticatorId: string;
  provider: string;
  authorityId?: string;
  label?: string;
  enabled?: boolean;
  roleProviding?: boolean;
  subjectNamespace?: string;
  secretRef?: string;
  selector?: Record<string, unknown>;
  verifier?: Record<string, unknown>;
  properties?: Record<string, unknown>;
}

export const upsertAuthenticator = createAsyncThunk<
  AuthenticatorMutationResult,
  UpsertAuthenticatorArgs,
  { rejectValue: string }
>(
  'authenticators/upsert',
  async (args, { rejectWithValue }) => {
    try {
      const res = await postOp<AuthenticatorMutationResult>('authenticators_upsert', {
        authenticator_id: args.authenticatorId,
        provider: args.provider,
        authority_id: args.authorityId || '',
        label: args.label || '',
        enabled: args.enabled !== false,
        role_providing: args.roleProviding === true,
        subject_namespace: args.subjectNamespace || '',
        secret_ref: args.secretRef || '',
        selector: args.selector || {},
        verifier: args.verifier || {},
        properties: args.properties || {},
      });
      if (res && res.ok === false) {
        return rejectWithValue(res.message || res.error || 'Authenticator save failed');
      }
      return res;
    } catch (e) {
      return rejectWithValue(message(e));
    }
  },
);

export const removeAuthenticator = createAsyncThunk<
  AuthenticatorMutationResult,
  string,
  { rejectValue: string }
>(
  'authenticators/remove',
  async (authenticatorId, { rejectWithValue }) => {
    try {
      const res = await postOp<AuthenticatorMutationResult>('authenticators_remove', {
        authenticator_id: authenticatorId,
      });
      if (res && res.ok === false) {
        return rejectWithValue(res.message || res.error || 'Authenticator remove failed');
      }
      return res;
    } catch (e) {
      return rejectWithValue(message(e));
    }
  },
);

const authenticatorsSlice = createSlice({
  name: 'authenticators',
  initialState,
  reducers: {
    clearAuthenticatorsError(state) {
      state.error = '';
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(loadAuthenticators.fulfilled, (state, action: PayloadAction<AuthenticatorsListResult>) => {
        state.loading = false;
        if (action.payload.ok === false && action.payload.error === 'platform_admin_required') {
          state.allowed = false;
          state.items = [];
          state.supportedProviders = [];
          return;
        }
        state.allowed = true;
        state.items = Array.isArray(action.payload.items) ? action.payload.items : [];
        state.supportedProviders = Array.isArray(action.payload.supported_providers)
          ? action.payload.supported_providers
          : [];
      })
      .addCase(loadAuthenticators.rejected, (state, action) => {
        state.loading = false;
        state.error = action.payload ?? 'Failed to load authenticators';
      });

    [upsertAuthenticator, removeAuthenticator].forEach((thunk) => {
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
          state.error = (action.payload as string) ?? 'Authenticator operation failed';
        });
    });
  },
});

export const { clearAuthenticatorsError } = authenticatorsSlice.actions;
export default authenticatorsSlice.reducer;
