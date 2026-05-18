export type ScopeFilter = 'current_bundle' | 'all_user_memories';
export type ReconcilerAgentType = 'lite' | 'regular' | 'strong';

export interface MemoryEntry {
  id: string;
  bundle_id: string;
  memory: string;
  context: string;
  kind: string;
  status: string;
  visibility: string;
  labels: string[];
  keywords: string[];
  tier: number;
  pinned: boolean;
  confidence_score: number;
  importance_score: number;
  salience_score: number;
  confirmation_rate: number;
  evidence_count: number;
  update_count: number;
  confirmation_count: number;
  contradiction_count: number;
  created_at: string;
  updated_at: string;
  last_event_at: string;
  revision: number;
  score?: number;
}

export interface MemoryEvent {
  id: string;
  memory_id: string;
  bundle_id: string;
  event_type: string;
  signal_text: string;
  context: string;
  originator: string;
  confidence: number;
  importance: number;
  labels: string[];
  keywords: string[];
  created_at: string;
}

export interface MemoriesPayload {
  ok: boolean;
  error?: string;
  message?: string;
  capabilities?: {
    allow_all_user_memories?: boolean;
    allow_write?: boolean;
    allow_reconciliation?: boolean;
    allow_snapshots?: boolean;
  };
  preferences?: MemoryPreferences;
  viewer?: {
    user_type?: string;
    is_admin?: boolean;
  };
  scope?: {
    tenant: string;
    project: string;
    user_id: string;
    bundle_id: string;
    filter: string;
  };
  memories: MemoryEntry[];
  count: number;
  limit?: number;
  offset?: number;
  has_more?: boolean;
}

export interface MemoryPreferences {
  memory_enabled: boolean;
  updated_by?: string;
  metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface MemoryPreferencesPayload {
  ok: boolean;
  error?: string;
  message?: string;
  preferences?: MemoryPreferences;
  capabilities?: MemoriesPayload['capabilities'];
  viewer?: MemoriesPayload['viewer'];
}

export interface MemoryEventsPayload {
  ok: boolean;
  error?: string;
  memory?: MemoryEntry;
  events: MemoryEvent[];
  count: number;
}

export interface MemoryMutationPayload {
  ok: boolean;
  error?: string;
  message?: string;
  memory?: MemoryEntry;
  memory_id?: string;
  deleted?: boolean;
}

export interface MemoryExportPayload {
  ok: boolean;
  error?: string;
  message?: string;
  format?: string;
  filename?: string;
  mime?: string;
  content?: string;
  count?: number;
}

export interface MemoryDeleteSearchPayload {
  ok: boolean;
  error?: string;
  message?: string;
  deleted_count?: number;
  deleted_ids?: string[];
  skipped_ids?: string[];
  soft_delete?: boolean;
}

export interface MemoryDraft {
  memory: string;
  context: string;
  kind: string;
  status: string;
  labels: string;
  keywords: string;
  importance: number;
  pinned: boolean;
}

export interface ReconciliationAnalysis {
  total: number;
  status_counts: Record<string, number>;
  tier_counts: Record<string, number>;
  possible_duplicate_groups: Array<{
    memory_ids: string[];
    preview: string[];
  }>;
  contradiction_count: number;
  weak_or_unsupported_count: number;
  low_freshness_count: number;
  needs_reconciliation: boolean;
  reasons: string[];
}

export interface ReconciliationJob {
  job_id: string;
  status: string;
  reason?: string;
  agent_type?: ReconcilerAgentType;
  reconciliation_context?: Record<string, unknown>;
  role_model?: {
    provider?: string;
    model?: string;
  };
  scope_filter?: string;
  candidate_count?: number;
  proposal_count?: number;
  warning_count?: number;
  created_at?: string;
  updated_at?: string;
  error?: string;
  artifacts?: Record<string, { key: string; uri?: string; mime?: string }>;
  snapshot_id?: string;
}

export interface MemorySnapshot {
  snapshot_id: string;
  status: string;
  reason?: string;
  scope_filter?: string;
  memory_count?: number;
  created_at?: string;
  updated_at?: string;
  linked_job_id?: string;
  error?: string;
  artifacts?: Record<string, { key: string; uri?: string; mime?: string }>;
}

export interface SnapshotsPayload {
  ok: boolean;
  error?: string;
  message?: string;
  snapshots: MemorySnapshot[];
  count: number;
  limit?: number;
  offset?: number;
  has_more?: boolean;
}

export interface SnapshotCreatePayload {
  ok: boolean;
  error?: string;
  message?: string;
  snapshot?: MemorySnapshot;
}

export interface SnapshotExportPayload {
  ok: boolean;
  error?: string;
  message?: string;
  snapshot_id?: string;
  artifact?: string;
  key?: string;
  uri?: string;
  mime?: string;
  content?: string;
}

export interface SnapshotDeletePayload {
  ok: boolean;
  error?: string;
  message?: string;
  snapshot_id?: string;
  deleted_artifacts?: number;
}

export interface SnapshotRestoreChange {
  memory_id: string;
  action: string;
  fields?: string[];
  memory?: string;
  status?: string;
  current_status?: string;
  snapshot_status?: string;
}

export interface SnapshotRestorePreviewPayload {
  ok: boolean;
  error?: string;
  message?: string;
  snapshot_id?: string;
  scope_filter?: string;
  retire_extra?: boolean;
  counts?: Record<string, number>;
  change_count?: number;
  changes?: SnapshotRestoreChange[];
  truncated?: boolean;
}

export interface SnapshotRestoreApplyPayload {
  ok: boolean;
  error?: string;
  message?: string;
  snapshot_id?: string;
  result?: {
    restored?: number;
    updated?: number;
    inserted?: number;
    retired_extra?: number;
    skipped_count?: number;
    skipped?: Array<Record<string, unknown>>;
  };
  safety_snapshot?: MemorySnapshot;
  post_restore_preview?: SnapshotRestorePreviewPayload;
}

export interface ReconciliationAnalyzePayload {
  ok: boolean;
  error?: string;
  message?: string;
  scope_filter?: string;
  candidate_count?: number;
  analysis?: ReconciliationAnalysis;
}

export interface ReconciliationJobsPayload {
  ok: boolean;
  error?: string;
  message?: string;
  jobs: ReconciliationJob[];
  count: number;
  limit?: number;
  offset?: number;
  has_more?: boolean;
}

export interface ReconciliationRunPayload {
  ok: boolean;
  accepted?: boolean;
  error?: string;
  message?: string;
  job?: ReconciliationJob;
}

export interface ReconciliationExportPayload {
  ok: boolean;
  error?: string;
  message?: string;
  job_id?: string;
  artifact?: string;
  key?: string;
  uri?: string;
  mime?: string;
  content?: string;
}

export interface ReconciliationApplyPayload {
  ok: boolean;
  error?: string;
  message?: string;
  job?: ReconciliationJob;
  safety_snapshot?: MemorySnapshot;
  apply_result?: {
    applied_count?: number;
    skipped_count?: number;
    safety_snapshot_id?: string;
    results?: Array<Record<string, unknown>>;
  };
}
