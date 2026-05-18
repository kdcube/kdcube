import { createAsyncThunk, createSlice, PayloadAction } from '@reduxjs/toolkit';
import { callOperation } from '../../api/client';
import type {
  MemoriesPayload,
  MemoryDeleteSearchPayload,
  MemoryDraft,
  MemoryEntry,
  MemoryEvent,
  MemoryEventsPayload,
  MemoryExportPayload,
  MemoryMutationPayload,
  MemoryPreferences,
  MemoryPreferencesPayload,
  ReconciliationAnalysis,
  ReconciliationApplyPayload,
  ReconciliationAnalyzePayload,
  ReconciliationExportPayload,
  ReconciliationJob,
  ReconciliationJobsPayload,
  ReconciliationRunPayload,
  ReconcilerAgentType,
  MemorySnapshot,
  SnapshotCreatePayload,
  SnapshotDeletePayload,
  SnapshotExportPayload,
  SnapshotsPayload,
  ScopeFilter,
} from '../../api/types';

interface MemoriesState {
  scopeFilter: ScopeFilter;
  query: string;
  labelsFilter: string;
  keywordsFilter: string;
  status: string;
  page: number;
  pageSize: number;
  count: number;
  hasMore: boolean;
  memories: MemoryEntry[];
  selectedId: string;
  selectedEvents: MemoryEvent[];
  currentBundleId: string;
  allowAllUserMemories: boolean;
  allowWrite: boolean;
  allowReconciliation: boolean;
  allowSnapshots: boolean;
  memoryPreferences: MemoryPreferences;
  memoryUseEnabled: boolean;
  loading: boolean;
  eventsLoading: boolean;
  saving: boolean;
  reconciliationLoading: boolean;
  reconciliationRunning: boolean;
  reconciliationJobsLoading: boolean;
  snapshotLoading: boolean;
  error: string;
  mutationError: string;
  reconciliationError: string;
  reconciliationAnalysis?: ReconciliationAnalysis;
  reconciliationJobs: ReconciliationJob[];
  selectedReconciliationJobId: string;
  reconciliationJobPage: number;
  reconciliationJobPageSize: number;
  reconciliationJobsCount: number;
  reconciliationJobsHasMore: boolean;
  reconcilerAgentType: ReconcilerAgentType;
  reconciliationExport: string;
  snapshots: MemorySnapshot[];
  selectedSnapshotId: string;
  snapshotPage: number;
  snapshotPageSize: number;
  snapshotsCount: number;
  snapshotsHasMore: boolean;
  snapshotExport: string;
}

const initialState: MemoriesState = {
  scopeFilter: 'current_bundle',
  query: '',
  labelsFilter: '',
  keywordsFilter: '',
  status: 'active',
  page: 0,
  pageSize: 30,
  count: 0,
  hasMore: false,
  memories: [],
  selectedId: '',
  selectedEvents: [],
  currentBundleId: '',
  allowAllUserMemories: true,
  allowWrite: false,
  allowReconciliation: false,
  allowSnapshots: false,
  memoryPreferences: { memory_enabled: true },
  memoryUseEnabled: true,
  loading: false,
  eventsLoading: false,
  saving: false,
  reconciliationLoading: false,
  reconciliationRunning: false,
  reconciliationJobsLoading: false,
  snapshotLoading: false,
  error: '',
  mutationError: '',
  reconciliationError: '',
  reconciliationAnalysis: undefined,
  reconciliationJobs: [],
  selectedReconciliationJobId: '',
  reconciliationJobPage: 0,
  reconciliationJobPageSize: 4,
  reconciliationJobsCount: 0,
  reconciliationJobsHasMore: false,
  reconcilerAgentType: 'regular',
  reconciliationExport: '',
  snapshots: [],
  selectedSnapshotId: '',
  snapshotPage: 0,
  snapshotPageSize: 4,
  snapshotsCount: 0,
  snapshotsHasMore: false,
  snapshotExport: '',
};

function terms(value: string): string[] {
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function upsertMemory(state: MemoriesState, memory?: MemoryEntry, incrementIfNew = false) {
  if (!memory) return;
  const index = state.memories.findIndex((item) => item.id === memory.id);
  if (index >= 0) state.memories[index] = memory;
  else {
    state.memories.unshift(memory);
    if (incrementIfNew) state.count += 1;
  }
  state.selectedId = memory.id;
}

export const loadMemories = createAsyncThunk<MemoriesPayload, void, { state: { memories: MemoriesState } }>(
  'memories/load',
  async (_arg, thunkApi) => {
    const state = thunkApi.getState().memories;
    return callOperation<MemoriesPayload>('memories_widget_data', {
      scope_filter: state.scopeFilter,
      query: state.query,
      labels: terms(state.labelsFilter),
      keywords: terms(state.keywordsFilter),
      status: state.status,
      limit: state.pageSize,
      offset: state.page * state.pageSize,
    });
  },
);

export const createMemory = createAsyncThunk<MemoryMutationPayload, MemoryDraft>(
  'memories/create',
  async (draft) => callOperation<MemoryMutationPayload>('memories_widget_create', {
    memory: draft.memory,
    context: draft.context,
    kind: draft.kind,
    labels: terms(draft.labels),
    keywords: terms(draft.keywords),
    importance: draft.importance,
    pinned: draft.pinned,
  }),
);

export const updateMemory = createAsyncThunk<
  MemoryMutationPayload,
  { id: string; draft: MemoryDraft },
  { state: { memories: MemoriesState } }
>(
  'memories/update',
  async ({ id, draft }, thunkApi) => {
    const state = thunkApi.getState().memories;
    return callOperation<MemoryMutationPayload>('memories_widget_update', {
      memory_id: id,
      memory: draft.memory,
      context: draft.context,
      kind: draft.kind,
      status: draft.status,
      labels: terms(draft.labels),
      keywords: terms(draft.keywords),
      importance: draft.importance,
      pinned: draft.pinned,
      scope_filter: state.scopeFilter,
    });
  },
);

export const confirmMemory = createAsyncThunk<MemoryMutationPayload, string>(
  'memories/confirm',
  async (memoryId) => callOperation<MemoryMutationPayload>('memories_widget_confirm', {
    memory_id: memoryId,
    note: 'confirmed by user',
  }),
);

export const retireMemory = createAsyncThunk<MemoryMutationPayload, string>(
  'memories/retire',
  async (memoryId) => callOperation<MemoryMutationPayload>('memories_widget_delete', {
    memory_id: memoryId,
  }),
);

export const updateMemoryPreferences = createAsyncThunk<
  MemoryPreferencesPayload,
  { memoryEnabled: boolean }
>(
  'memories/preferencesUpdate',
  async ({ memoryEnabled }) => callOperation<MemoryPreferencesPayload>('memories_widget_preferences_update', {
    memory_enabled: memoryEnabled,
  }),
);

export const pinMemory = createAsyncThunk<
  MemoryMutationPayload,
  { id: string; pinned: boolean },
  { state: { memories: MemoriesState } }
>(
  'memories/pin',
  async ({ id, pinned }, thunkApi) => {
    const state = thunkApi.getState().memories;
    return callOperation<MemoryMutationPayload>('memories_widget_pin', {
      memory_id: id,
      pinned,
      scope_filter: state.scopeFilter,
    });
  },
);

export const loadMemoryEvents = createAsyncThunk<MemoryEventsPayload, string, { state: { memories: MemoriesState } }>(
  'memories/loadEvents',
  async (memoryId, thunkApi) => {
    const state = thunkApi.getState().memories;
    return callOperation<MemoryEventsPayload>('memories_widget_events', {
      memory_id: memoryId,
      scope_filter: state.scopeFilter,
      limit: 25,
    });
  },
);

function currentFilterPayload(state: MemoriesState, all = false) {
  return {
    scope_filter: state.scopeFilter,
    query: all ? '' : state.query,
    labels: all ? [] : terms(state.labelsFilter),
    keywords: all ? [] : terms(state.keywordsFilter),
    status: all ? 'any' : state.status,
    limit: 5000,
  };
}

export const exportMemories = createAsyncThunk<
  MemoryExportPayload,
  { format: 'json' | 'markdown' | 'csv'; all?: boolean },
  { state: { memories: MemoriesState } }
>(
  'memories/export',
  async ({ format, all = false }, thunkApi) => {
    const state = thunkApi.getState().memories;
    return callOperation<MemoryExportPayload>('memories_widget_export', {
      ...currentFilterPayload(state, all),
      format,
    });
  },
);

export const deleteMemoriesBySearch = createAsyncThunk<
  MemoryDeleteSearchPayload,
  { all?: boolean } | void,
  { state: { memories: MemoriesState } }
>(
  'memories/deleteSearch',
  async (arg, thunkApi) => {
    const state = thunkApi.getState().memories;
    return callOperation<MemoryDeleteSearchPayload>('memories_widget_delete_search', {
      ...currentFilterPayload(state, Boolean(arg && 'all' in arg && arg.all)),
      confirm: true,
    });
  },
);

export const analyzeReconciliation = createAsyncThunk<
  ReconciliationAnalyzePayload,
  void,
  { state: { memories: MemoriesState } }
>(
  'memories/reconcileAnalyze',
  async (_arg, thunkApi) => {
    const state = thunkApi.getState().memories;
    return callOperation<ReconciliationAnalyzePayload>('memories_widget_reconcile_analyze', {
      scope_filter: state.scopeFilter,
      limit: state.pageSize,
    });
  },
);

export const loadReconciliationJobs = createAsyncThunk<
  ReconciliationJobsPayload,
  { page?: number } | void,
  { state: { memories: MemoriesState } }
>(
  'memories/reconcileJobs',
  async (arg, thunkApi) => {
    const state = thunkApi.getState().memories;
    const page = Math.max(0, Number(arg?.page ?? state.reconciliationJobPage) || 0);
    return callOperation<ReconciliationJobsPayload>('memories_widget_reconcile_jobs', {
      scope_filter: state.scopeFilter,
      limit: state.reconciliationJobPageSize,
      offset: page * state.reconciliationJobPageSize,
    });
  },
);

export const runReconciliation = createAsyncThunk<
  ReconciliationRunPayload,
  void,
  { state: { memories: MemoriesState } }
>(
  'memories/reconcileRun',
  async (_arg, thunkApi) => {
    const state = thunkApi.getState().memories;
    return callOperation<ReconciliationRunPayload>('memories_widget_reconcile_run', {
      scope_filter: state.scopeFilter,
      limit: state.pageSize,
      reason: 'manual widget reconciliation dry run',
      agent_type: state.reconcilerAgentType,
    });
  },
);

export const exportReconciliation = createAsyncThunk<
  ReconciliationExportPayload,
  { jobId: string; artifact?: string }
>(
  'memories/reconcileExport',
  async ({ jobId, artifact = 'proposal_md' }) => callOperation<ReconciliationExportPayload>('memories_widget_reconcile_export', {
    job_id: jobId,
    artifact,
  }),
);

export const applyReconciliation = createAsyncThunk<
  ReconciliationApplyPayload,
  { jobId: string }
>(
  'memories/reconcileApply',
  async ({ jobId }) => callOperation<ReconciliationApplyPayload>('memories_widget_reconcile_apply', {
    job_id: jobId,
    confirm: true,
  }),
);

export const loadSnapshots = createAsyncThunk<
  SnapshotsPayload,
  { page?: number } | void,
  { state: { memories: MemoriesState } }
>(
  'memories/snapshots',
  async (arg, thunkApi) => {
    const state = thunkApi.getState().memories;
    const page = Math.max(0, Number(arg?.page ?? state.snapshotPage) || 0);
    return callOperation<SnapshotsPayload>('memories_widget_snapshots', {
      scope_filter: state.scopeFilter,
      limit: state.snapshotPageSize,
      offset: page * state.snapshotPageSize,
    });
  },
);

export const createSnapshot = createAsyncThunk<
  SnapshotCreatePayload,
  void,
  { state: { memories: MemoriesState } }
>(
  'memories/snapshotCreate',
  async (_arg, thunkApi) => {
    const state = thunkApi.getState().memories;
    return callOperation<SnapshotCreatePayload>('memories_widget_snapshot_create', {
      scope_filter: state.scopeFilter,
      limit: 1000,
      reason: 'manual memory snapshot',
    });
  },
);

export const exportSnapshot = createAsyncThunk<
  SnapshotExportPayload,
  { snapshotId: string; artifact?: string }
>(
  'memories/snapshotExport',
  async ({ snapshotId, artifact = 'memories_md' }) => callOperation<SnapshotExportPayload>('memories_widget_snapshot_export', {
    snapshot_id: snapshotId,
    artifact,
  }),
);

export const deleteSnapshot = createAsyncThunk<SnapshotDeletePayload, { snapshotId: string }>(
  'memories/snapshotDelete',
  async ({ snapshotId }) => callOperation<SnapshotDeletePayload>('memories_widget_snapshot_delete', {
    snapshot_id: snapshotId,
    confirm: true,
  }),
);

const memoriesSlice = createSlice({
  name: 'memories',
  initialState,
  reducers: {
    setScopeFilter(state, action: PayloadAction<ScopeFilter>) {
      state.scopeFilter = action.payload;
      state.page = 0;
      state.selectedId = '';
      state.selectedEvents = [];
    },
    setQuery(state, action: PayloadAction<string>) {
      state.query = action.payload;
      state.page = 0;
    },
    setLabelsFilter(state, action: PayloadAction<string>) {
      state.labelsFilter = action.payload;
      state.page = 0;
    },
    setKeywordsFilter(state, action: PayloadAction<string>) {
      state.keywordsFilter = action.payload;
      state.page = 0;
    },
    setStatus(state, action: PayloadAction<string>) {
      state.status = action.payload;
      state.page = 0;
    },
    nextPage(state) {
      if (state.hasMore) {
        state.page += 1;
        state.selectedId = '';
        state.selectedEvents = [];
      }
    },
    previousPage(state) {
      state.page = Math.max(0, state.page - 1);
      state.selectedId = '';
      state.selectedEvents = [];
    },
    selectMemory(state, action: PayloadAction<string>) {
      state.selectedId = action.payload;
      state.selectedEvents = [];
    },
    selectReconciliationJob(state, action: PayloadAction<string>) {
      state.selectedReconciliationJobId = action.payload;
      state.reconciliationExport = '';
    },
    setReconcilerAgentType(state, action: PayloadAction<ReconcilerAgentType>) {
      state.reconcilerAgentType = action.payload;
    },
    selectSnapshot(state, action: PayloadAction<string>) {
      state.selectedSnapshotId = action.payload;
      state.snapshotExport = '';
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(loadMemories.pending, (state) => {
        state.loading = true;
        state.error = '';
      })
      .addCase(loadMemories.fulfilled, (state, action) => {
        state.loading = false;
        if (!action.payload.ok) {
          state.error = action.payload.error || 'Unable to load memories.';
          state.memories = [];
          state.count = 0;
          return;
        }
        state.memories = action.payload.memories || [];
        state.count = Number(action.payload.count || 0);
        state.currentBundleId = action.payload.scope?.bundle_id || state.currentBundleId;
        state.allowAllUserMemories = action.payload.capabilities?.allow_all_user_memories !== false;
        state.allowWrite = action.payload.capabilities?.allow_write === true;
        state.allowReconciliation = action.payload.capabilities?.allow_reconciliation === true;
        state.allowSnapshots = action.payload.capabilities?.allow_snapshots === true;
        state.memoryPreferences = action.payload.preferences || state.memoryPreferences;
        state.memoryUseEnabled = action.payload.preferences?.memory_enabled !== false;
        state.hasMore = action.payload.has_more === true;
        if (!state.allowAllUserMemories && state.scopeFilter === 'all_user_memories') {
          state.scopeFilter = 'current_bundle';
        }
        if (!state.selectedId && state.memories.length > 0) state.selectedId = state.memories[0].id;
        if (state.selectedId && !state.memories.some((memory) => memory.id === state.selectedId)) {
          state.selectedId = state.memories[0]?.id || '';
        }
      })
      .addCase(loadMemories.rejected, (state, action) => {
        state.loading = false;
        state.error = action.error.message || 'Unable to load memories.';
      })
      .addCase(loadMemoryEvents.pending, (state) => {
        state.eventsLoading = true;
      })
      .addCase(loadMemoryEvents.fulfilled, (state, action) => {
        state.eventsLoading = false;
        state.selectedEvents = action.payload.ok ? action.payload.events || [] : [];
      })
      .addCase(loadMemoryEvents.rejected, (state) => {
        state.eventsLoading = false;
        state.selectedEvents = [];
      })
      .addCase(createMemory.pending, (state) => {
        state.saving = true;
        state.mutationError = '';
      })
      .addCase(updateMemory.pending, (state) => {
        state.saving = true;
        state.mutationError = '';
      })
      .addCase(confirmMemory.pending, (state) => {
        state.saving = true;
        state.mutationError = '';
      })
      .addCase(retireMemory.pending, (state) => {
        state.saving = true;
        state.mutationError = '';
      })
      .addCase(pinMemory.pending, (state) => {
        state.saving = true;
        state.mutationError = '';
      })
      .addCase(analyzeReconciliation.pending, (state) => {
        state.reconciliationLoading = true;
        state.reconciliationError = '';
      })
      .addCase(loadReconciliationJobs.pending, (state) => {
        state.reconciliationJobsLoading = true;
        state.reconciliationError = '';
      })
      .addCase(runReconciliation.pending, (state) => {
        state.reconciliationRunning = true;
        state.reconciliationError = '';
      })
      .addCase(exportReconciliation.pending, (state) => {
        state.reconciliationLoading = true;
        state.reconciliationError = '';
      })
      .addCase(applyReconciliation.pending, (state) => {
        state.reconciliationLoading = true;
        state.reconciliationError = '';
      })
      .addCase(loadSnapshots.pending, (state) => {
        state.snapshotLoading = true;
        state.reconciliationError = '';
      })
      .addCase(createSnapshot.pending, (state) => {
        state.snapshotLoading = true;
        state.reconciliationError = '';
      })
      .addCase(exportSnapshot.pending, (state) => {
        state.snapshotLoading = true;
        state.reconciliationError = '';
      })
      .addCase(deleteSnapshot.pending, (state) => {
        state.snapshotLoading = true;
        state.reconciliationError = '';
      })
      .addCase(createMemory.fulfilled, (state, action) => {
        state.saving = false;
        if (!action.payload.ok) state.mutationError = action.payload.message || action.payload.error || 'Unable to save memory.';
        else upsertMemory(state, action.payload.memory, true);
      })
      .addCase(updateMemory.fulfilled, (state, action) => {
        state.saving = false;
        if (!action.payload.ok) state.mutationError = action.payload.message || action.payload.error || 'Unable to save memory.';
        else upsertMemory(state, action.payload.memory);
      })
      .addCase(confirmMemory.fulfilled, (state, action) => {
        state.saving = false;
        if (!action.payload.ok) state.mutationError = action.payload.message || action.payload.error || 'Unable to save memory.';
        else upsertMemory(state, action.payload.memory);
      })
      .addCase(retireMemory.fulfilled, (state, action) => {
        state.saving = false;
        if (!action.payload.ok) state.mutationError = action.payload.message || action.payload.error || 'Unable to save memory.';
        else {
          const deletedId = action.payload.memory_id || action.payload.memory?.id || state.selectedId;
          state.memories = state.memories.filter((memory) => memory.id !== deletedId);
          state.count = Math.max(0, state.count - 1);
          state.selectedId = state.memories[0]?.id || '';
          state.selectedEvents = [];
        }
      })
      .addCase(updateMemoryPreferences.fulfilled, (state, action) => {
        if (!action.payload.ok) {
          state.mutationError = action.payload.message || action.payload.error || 'Unable to update memory preferences.';
          return;
        }
        state.memoryPreferences = action.payload.preferences || state.memoryPreferences;
        state.memoryUseEnabled = action.payload.preferences?.memory_enabled !== false;
      })
      .addCase(pinMemory.fulfilled, (state, action) => {
        state.saving = false;
        if (!action.payload.ok) state.mutationError = action.payload.message || action.payload.error || 'Unable to save memory.';
        else upsertMemory(state, action.payload.memory);
      })
      .addCase(analyzeReconciliation.fulfilled, (state, action) => {
        state.reconciliationLoading = false;
        if (!action.payload.ok) state.reconciliationError = action.payload.message || action.payload.error || 'Unable to analyze memories.';
        else state.reconciliationAnalysis = action.payload.analysis;
      })
      .addCase(loadReconciliationJobs.fulfilled, (state, action) => {
        state.reconciliationJobsLoading = false;
        if (!action.payload.ok) {
          state.reconciliationError = action.payload.message || action.payload.error || 'Unable to load reconciliation jobs.';
          return;
        }
        const offset = Math.max(0, Number(action.payload.offset || 0));
        state.reconciliationJobPage = Math.floor(offset / state.reconciliationJobPageSize);
        state.reconciliationJobsCount = Number(action.payload.count || 0);
        state.reconciliationJobsHasMore = action.payload.has_more === true;
        state.reconciliationJobs = action.payload.jobs || [];
        if (!state.reconciliationJobs.some((job) => job.job_id === state.selectedReconciliationJobId)) {
          state.selectedReconciliationJobId = '';
          state.reconciliationExport = '';
        }
        if (!state.selectedReconciliationJobId && state.reconciliationJobs.length > 0) {
          state.selectedReconciliationJobId = state.reconciliationJobs[0].job_id;
        }
      })
      .addCase(runReconciliation.fulfilled, (state, action) => {
        state.reconciliationRunning = false;
        if (!action.payload.ok || !action.payload.job) {
          state.reconciliationError = action.payload.message || action.payload.error || 'Unable to run reconciliation.';
          return;
        }
        state.reconciliationJobs = [
          action.payload.job,
          ...state.reconciliationJobs.filter((job) => job.job_id !== action.payload.job?.job_id),
        ];
        state.reconciliationJobPage = 0;
        state.selectedReconciliationJobId = action.payload.job.job_id;
      })
      .addCase(exportReconciliation.fulfilled, (state, action) => {
        state.reconciliationLoading = false;
        if (!action.payload.ok) state.reconciliationError = action.payload.message || action.payload.error || 'Unable to export reconciliation report.';
        else state.reconciliationExport = action.payload.content || '';
      })
      .addCase(applyReconciliation.fulfilled, (state, action) => {
        state.reconciliationLoading = false;
        if (!action.payload.ok || !action.payload.job) {
          state.reconciliationError = action.payload.message || action.payload.error || 'Unable to apply reconciliation proposal.';
          return;
        }
        state.reconciliationJobs = [
          action.payload.job,
          ...state.reconciliationJobs.filter((job) => job.job_id !== action.payload.job?.job_id),
        ];
        state.reconciliationJobPage = 0;
        state.selectedReconciliationJobId = action.payload.job.job_id;
        if (action.payload.safety_snapshot?.snapshot_id) {
          state.snapshots = [
            action.payload.safety_snapshot,
            ...state.snapshots.filter((snapshot) => snapshot.snapshot_id !== action.payload.safety_snapshot?.snapshot_id),
          ];
          state.snapshotPage = 0;
          state.selectedSnapshotId = action.payload.safety_snapshot.snapshot_id;
        }
      })
      .addCase(loadSnapshots.fulfilled, (state, action) => {
        state.snapshotLoading = false;
        if (!action.payload.ok) {
          state.reconciliationError = action.payload.message || action.payload.error || 'Unable to load snapshots.';
          return;
        }
        const offset = Math.max(0, Number(action.payload.offset || 0));
        state.snapshotPage = Math.floor(offset / state.snapshotPageSize);
        state.snapshotsCount = Number(action.payload.count || 0);
        state.snapshotsHasMore = action.payload.has_more === true;
        state.snapshots = action.payload.snapshots || [];
        if (!state.snapshots.some((snapshot) => snapshot.snapshot_id === state.selectedSnapshotId)) {
          state.selectedSnapshotId = '';
          state.snapshotExport = '';
        }
        if (!state.selectedSnapshotId && state.snapshots.length > 0) {
          state.selectedSnapshotId = state.snapshots[0].snapshot_id;
        }
      })
      .addCase(createSnapshot.fulfilled, (state, action) => {
        state.snapshotLoading = false;
        if (!action.payload.ok || !action.payload.snapshot) {
          state.reconciliationError = action.payload.message || action.payload.error || 'Unable to create snapshot.';
          return;
        }
        state.snapshots = [
          action.payload.snapshot,
          ...state.snapshots.filter((snapshot) => snapshot.snapshot_id !== action.payload.snapshot?.snapshot_id),
        ];
        state.snapshotPage = 0;
        state.selectedSnapshotId = action.payload.snapshot.snapshot_id;
      })
      .addCase(exportSnapshot.fulfilled, (state, action) => {
        state.snapshotLoading = false;
        if (!action.payload.ok) state.reconciliationError = action.payload.message || action.payload.error || 'Unable to export snapshot.';
        else state.snapshotExport = action.payload.content || '';
      })
      .addCase(deleteSnapshot.fulfilled, (state, action) => {
        state.snapshotLoading = false;
        if (!action.payload.ok || !action.payload.snapshot_id) {
          state.reconciliationError = action.payload.message || action.payload.error || 'Unable to delete snapshot.';
          return;
        }
        state.snapshots = state.snapshots.filter((snapshot) => snapshot.snapshot_id !== action.payload.snapshot_id);
        if (state.selectedSnapshotId === action.payload.snapshot_id) {
          state.selectedSnapshotId = state.snapshots[0]?.snapshot_id || '';
          state.snapshotExport = '';
        }
      })
      .addCase(createMemory.rejected, (state, action) => {
        state.saving = false;
        state.mutationError = action.error.message || 'Unable to save memory.';
      })
      .addCase(updateMemory.rejected, (state, action) => {
        state.saving = false;
        state.mutationError = action.error.message || 'Unable to save memory.';
      })
      .addCase(confirmMemory.rejected, (state, action) => {
        state.saving = false;
        state.mutationError = action.error.message || 'Unable to save memory.';
      })
      .addCase(retireMemory.rejected, (state, action) => {
        state.saving = false;
        state.mutationError = action.error.message || 'Unable to save memory.';
      })
      .addCase(updateMemoryPreferences.rejected, (state, action) => {
        state.mutationError = action.error.message || 'Unable to update memory preferences.';
      })
      .addCase(pinMemory.rejected, (state, action) => {
        state.saving = false;
        state.mutationError = action.error.message || 'Unable to save memory.';
      })
      .addCase(analyzeReconciliation.rejected, (state, action) => {
        state.reconciliationLoading = false;
        state.reconciliationError = action.error.message || 'Unable to analyze memories.';
      })
      .addCase(loadReconciliationJobs.rejected, (state, action) => {
        state.reconciliationJobsLoading = false;
        state.reconciliationError = action.error.message || 'Unable to load reconciliation jobs.';
      })
      .addCase(runReconciliation.rejected, (state, action) => {
        state.reconciliationRunning = false;
        state.reconciliationError = action.error.message || 'Unable to run reconciliation.';
      })
      .addCase(exportReconciliation.rejected, (state, action) => {
        state.reconciliationLoading = false;
        state.reconciliationError = action.error.message || 'Unable to export reconciliation report.';
      })
      .addCase(applyReconciliation.rejected, (state, action) => {
        state.reconciliationLoading = false;
        state.reconciliationError = action.error.message || 'Unable to apply reconciliation proposal.';
      })
      .addCase(loadSnapshots.rejected, (state, action) => {
        state.snapshotLoading = false;
        state.reconciliationError = action.error.message || 'Unable to load snapshots.';
      })
      .addCase(createSnapshot.rejected, (state, action) => {
        state.snapshotLoading = false;
        state.reconciliationError = action.error.message || 'Unable to create snapshot.';
      })
      .addCase(exportSnapshot.rejected, (state, action) => {
        state.snapshotLoading = false;
        state.reconciliationError = action.error.message || 'Unable to export snapshot.';
      })
      .addCase(deleteSnapshot.rejected, (state, action) => {
        state.snapshotLoading = false;
        state.reconciliationError = action.error.message || 'Unable to delete snapshot.';
      });
  },
});

export const {
  nextPage,
  previousPage,
  selectMemory,
  selectReconciliationJob,
  selectSnapshot,
  setReconcilerAgentType,
  setKeywordsFilter,
  setLabelsFilter,
  setQuery,
  setScopeFilter,
  setStatus,
} = memoriesSlice.actions;
export default memoriesSlice.reducer;
