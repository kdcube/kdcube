import { createAsyncThunk, createSlice, PayloadAction } from '@reduxjs/toolkit';
import { settings } from '../../api/settings';
import { deletePaths, exportPaths, fetchList, fetchRegistry, fetchRoots, fetchTenants } from '../../api/apiClient';
import type { StorageState } from '../../api/types';

const initialState: StorageState = {
  ready: false,
  roots: [],
  selectedRootId: 'bundle_storage',
  tenant: '',
  project: '',
  tenants: [],
  path: '',
  entries: [],
  current: null,
  selectedPaths: [],
  registryBundles: [],
  activeManagedFolders: [],
  loading: false,
  deleting: false,
  exporting: false,
  error: null,
  message: null,
};

export const loadRoots = createAsyncThunk('storage/loadRoots', fetchRoots);
export const loadTenants = createAsyncThunk('storage/loadTenants', fetchTenants);

export const loadRegistry = createAsyncThunk('storage/loadRegistry', async (_, { getState }) => {
  const state = (getState() as { storage: StorageState }).storage;
  return fetchRegistry(state.tenant, state.project);
});

export const loadList = createAsyncThunk('storage/loadList', async (_, { getState }) => {
  const state = (getState() as { storage: StorageState }).storage;
  const root = state.roots.find((item) => item.id === state.selectedRootId);
  if (!root) throw new Error('Select a storage root');
  if (root.tenant_project_mode === 'required' && (!state.tenant || !state.project)) {
    throw new Error('Select tenant and project');
  }
  return fetchList({
    rootId: state.selectedRootId,
    tenant: state.tenant,
    project: state.project,
    path: state.path,
  });
});

export const deleteSelected = createAsyncThunk('storage/deleteSelected', async (_, { getState }) => {
  const state = (getState() as { storage: StorageState }).storage;
  return deletePaths({
    rootId: state.selectedRootId,
    tenant: state.tenant,
    project: state.project,
    paths: state.selectedPaths,
  });
});

export const exportSelected = createAsyncThunk('storage/exportSelected', async (_, { getState }) => {
  const state = (getState() as { storage: StorageState }).storage;
  await exportPaths({
    rootId: state.selectedRootId,
    tenant: state.tenant,
    project: state.project,
    paths: state.selectedPaths,
  });
  return { count: state.selectedPaths.length };
});

const storageSlice = createSlice({
  name: 'storage',
  initialState,
  reducers: {
    setRuntimeDefaults(state) {
      state.ready = true;
      state.tenant = settings.getTenant() || state.tenant;
      state.project = settings.getProject() || state.project;
    },
    selectRoot(state, action: PayloadAction<string>) {
      state.selectedRootId = action.payload;
      state.path = '';
      state.entries = [];
      state.current = null;
      state.selectedPaths = [];
      state.error = null;
      state.message = null;
    },
    selectTenant(state, action: PayloadAction<string>) {
      state.tenant = action.payload;
      const tenantEntry = state.tenants.find((item) => item.tenant === action.payload);
      state.project = tenantEntry?.projects[0] || '';
      state.path = '';
      state.selectedPaths = [];
    },
    selectProject(state, action: PayloadAction<string>) {
      state.project = action.payload;
      state.path = '';
      state.selectedPaths = [];
    },
    setPath(state, action: PayloadAction<string>) {
      state.path = action.payload;
      state.selectedPaths = [];
    },
    togglePath(state, action: PayloadAction<string>) {
      const value = action.payload;
      state.selectedPaths = state.selectedPaths.includes(value)
        ? state.selectedPaths.filter((item) => item !== value)
        : [...state.selectedPaths, value];
    },
    clearSelection(state) {
      state.selectedPaths = [];
    },
    setMessage(state, action: PayloadAction<string | null>) {
      state.message = action.payload;
    },
    setError(state, action: PayloadAction<string | null>) {
      state.error = action.payload;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(loadRoots.fulfilled, (state, action) => {
        state.roots = action.payload.roots || [];
        if (!state.roots.some((root) => root.id === state.selectedRootId)) {
          state.selectedRootId = state.roots[0]?.id || '';
        }
      })
      .addCase(loadTenants.fulfilled, (state, action) => {
        state.tenants = action.payload.tenants || [];
        const currentTenant = state.tenants.find((item) => item.tenant === state.tenant);
        if (!currentTenant && state.tenants[0]) {
          state.tenant = state.tenants[0].tenant;
          state.project = state.tenants[0].projects[0] || '';
        } else if (currentTenant && !currentTenant.projects.includes(state.project)) {
          state.project = currentTenant.projects[0] || '';
        }
      })
      .addCase(loadRegistry.fulfilled, (state, action) => {
        state.registryBundles = action.payload.bundles || [];
        state.activeManagedFolders = action.payload.active_managed_folders || [];
      })
      .addCase(loadList.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(loadList.fulfilled, (state, action) => {
        state.loading = false;
        state.entries = action.payload.entries || [];
        state.current = action.payload.current || null;
      })
      .addCase(loadList.rejected, (state, action) => {
        state.loading = false;
        state.entries = [];
        state.current = null;
        state.error = action.error.message || 'Failed to load storage path';
      })
      .addCase(deleteSelected.pending, (state) => {
        state.deleting = true;
        state.error = null;
        state.message = null;
      })
      .addCase(deleteSelected.fulfilled, (state, action) => {
        state.deleting = false;
        state.selectedPaths = [];
        state.message = `Deleted ${action.payload.deleted_count || 0} item(s).`;
      })
      .addCase(deleteSelected.rejected, (state, action) => {
        state.deleting = false;
        state.error = action.error.message || 'Delete failed';
      })
      .addCase(exportSelected.pending, (state) => {
        state.exporting = true;
        state.error = null;
        state.message = null;
      })
      .addCase(exportSelected.fulfilled, (state, action) => {
        state.exporting = false;
        state.message = `Exported ${action.payload.count || 0} item(s).`;
      })
      .addCase(exportSelected.rejected, (state, action) => {
        state.exporting = false;
        state.error = action.error.message || 'Export failed';
      });
  },
});

export const {
  clearSelection,
  selectProject,
  selectRoot,
  selectTenant,
  setError,
  setMessage,
  setPath,
  setRuntimeDefaults,
  togglePath,
} = storageSlice.actions;

export const storageReducer = storageSlice.reducer;
