import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { callOperation } from '../../api/client';
import type {
  MemorySnapshot,
  SnapshotExportPayload,
  SnapshotRestoreApplyPayload,
  SnapshotRestorePreviewPayload,
  ReconcilerAgentType,
} from '../../api/types';
import {
  analyzeReconciliation,
  applyReconciliation,
  createSnapshot,
  deleteSnapshot,
  exportSnapshot,
  exportReconciliation,
  loadMemories,
  loadReconciliationJobs,
  loadSnapshots,
  runReconciliation,
  selectReconciliationJob,
  selectSnapshot,
  setReconcilerAgentType,
} from './memoriesSlice';

function formatDate(value?: string): string {
  if (!value) return '';
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value));
  } catch {
    return value;
  }
}

function downloadText(filename: string, content: string, mime = 'text/plain') {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

async function downloadSnapshotArtifact(snapshot: MemorySnapshot, artifact: string, suffix: string) {
  const payload = await callOperation<SnapshotExportPayload>('memories_widget_snapshot_export', {
    snapshot_id: snapshot.snapshot_id,
    artifact,
  });
  if (!payload.ok || !payload.content) {
    throw new Error(payload.message || payload.error || 'Unable to export snapshot.');
  }
  downloadText(`${snapshot.snapshot_id}.${suffix}`, payload.content, payload.mime || 'text/plain');
}

export function ReconciliationPanel() {
  const dispatch = useAppDispatch();
  const [downloadError, setDownloadError] = useState('');
  const [downloadInProgress, setDownloadInProgress] = useState(false);
  const [restoreError, setRestoreError] = useState('');
  const [restoreBusy, setRestoreBusy] = useState(false);
  const [restorePreview, setRestorePreview] = useState<SnapshotRestorePreviewPayload | null>(null);
  const [restoreResult, setRestoreResult] = useState<SnapshotRestoreApplyPayload | null>(null);
  const {
    allowReconciliation,
    allowSnapshots,
    reconciliationAnalysis,
    reconciliationError,
    reconciliationExport,
    reconciliationJobs,
    reconciliationJobPage,
    reconciliationJobsCount,
    reconciliationJobsHasMore,
    reconciliationJobsLoading,
    reconciliationLoading,
    reconciliationRunning,
    reconcilerAgentType,
    memoryUseEnabled,
    selectedSnapshotId,
    selectedReconciliationJobId,
    snapshotExport,
    snapshotLoading,
    snapshotPage,
    snapshotsCount,
    snapshotsHasMore,
    snapshots,
    scopeFilter,
  } = useAppSelector((state) => state.memories);

  useEffect(() => {
    if (allowReconciliation) void dispatch(loadReconciliationJobs());
    if (allowSnapshots) void dispatch(loadSnapshots());
  }, [allowReconciliation, allowSnapshots, dispatch]);

  if (!allowReconciliation && !allowSnapshots) return null;

  const selectedJob = reconciliationJobs.find((job) => job.job_id === selectedReconciliationJobId);
  const selectedSnapshot = snapshots.find((snapshot) => snapshot.snapshot_id === selectedSnapshotId);
  const busy = reconciliationLoading || reconciliationRunning || reconciliationJobsLoading || snapshotLoading || restoreBusy;
  const mutationDisabled = busy || !memoryUseEnabled;
  const downloading = downloadInProgress;

  async function previewRestore(snapshot: MemorySnapshot) {
    setRestoreError('');
    setRestoreResult(null);
    setRestoreBusy(true);
    try {
      const payload = await callOperation<SnapshotRestorePreviewPayload>('memories_widget_snapshot_restore_preview', {
        snapshot_id: snapshot.snapshot_id,
        scope_filter: scopeFilter,
        retire_extra: true,
      });
      if (!payload.ok) throw new Error(payload.message || payload.error || 'Unable to preview restore.');
      setRestorePreview(payload);
    } catch (error) {
      setRestoreError(error instanceof Error ? error.message : String(error));
    } finally {
      setRestoreBusy(false);
    }
  }

  async function applyRestore(snapshot: MemorySnapshot) {
    setRestoreError('');
    setRestoreBusy(true);
    try {
      const payload = await callOperation<SnapshotRestoreApplyPayload>('memories_widget_snapshot_restore_apply', {
        snapshot_id: snapshot.snapshot_id,
        scope_filter: scopeFilter,
        retire_extra: true,
        confirm: true,
      });
      if (!payload.ok) throw new Error(payload.message || payload.error || 'Unable to restore snapshot.');
      setRestoreResult(payload);
      setRestorePreview(payload.post_restore_preview || null);
      dispatch(loadMemories());
      dispatch(loadSnapshots({ page: 0 }));
    } catch (error) {
      setRestoreError(error instanceof Error ? error.message : String(error));
    } finally {
      setRestoreBusy(false);
    }
  }

  return (
    <section className="reconcile-panel" aria-label="Memory reconciliation">
      <div className="reconcile-head">
        <div>
          <span className="eyebrow">Maintenance</span>
          <h2>Reconciliation</h2>
          <p>Analyze and export a dry-run proposal before any memory changes are applied.</p>
        </div>
        <div className="reconcile-actions">
          {allowReconciliation ? (
            <label className="reconcile-agent-select">
              <span>Agent</span>
              <select
                value={reconcilerAgentType}
                disabled={mutationDisabled}
                onChange={(event) => dispatch(setReconcilerAgentType(event.target.value as ReconcilerAgentType))}
              >
                <option value="lite">Lite</option>
                <option value="regular">Regular</option>
                <option value="strong">Strong</option>
              </select>
            </label>
          ) : null}
          {allowSnapshots ? (
            <button
              type="button"
              className="secondary-button"
              disabled={mutationDisabled}
              onClick={() => void dispatch(createSnapshot()).then(() => dispatch(loadSnapshots({ page: 0 })))}
            >
              Snapshot
            </button>
          ) : null}
          {allowReconciliation ? (
            <>
              <button
                type="button"
                className="secondary-button"
                disabled={mutationDisabled}
                onClick={() => void dispatch(analyzeReconciliation())}
              >
                Analyze
              </button>
              <button
                type="button"
                className="primary-button"
                disabled={mutationDisabled}
                onClick={() => void dispatch(runReconciliation()).then(() => {
                  dispatch(loadReconciliationJobs({ page: 0 }));
                  dispatch(loadSnapshots({ page: 0 }));
                })}
              >
                Dry Run
              </button>
            </>
          ) : null}
        </div>
      </div>

      {reconciliationError ? <div className="error-box compact-error">{reconciliationError}</div> : null}
      {downloadError ? <div className="error-box compact-error">{downloadError}</div> : null}
      {restoreError ? <div className="error-box compact-error">{restoreError}</div> : null}

      {reconciliationAnalysis ? (
        <div className="reconcile-stats">
          <div>
            <strong>{reconciliationAnalysis.total}</strong>
            <span>candidates</span>
          </div>
          <div>
            <strong>{reconciliationAnalysis.possible_duplicate_groups.length}</strong>
            <span>duplicate signals</span>
          </div>
          <div>
            <strong>{reconciliationAnalysis.contradiction_count}</strong>
            <span>contradictions</span>
          </div>
          <div className={reconciliationAnalysis.needs_reconciliation ? 'needs-work' : ''}>
            <strong>{reconciliationAnalysis.needs_reconciliation ? 'Review' : 'Clean'}</strong>
            <span>{reconciliationAnalysis.reasons[0] || 'no strong signal'}</span>
          </div>
        </div>
      ) : null}

      <div className="reconcile-grid">
        {allowSnapshots ? (
          <div className="reconcile-jobs">
            <div className="reconcile-subhead">
              <h3>Snapshots</h3>
              <button
                type="button"
                className="icon-button"
                title="Refresh snapshots"
                disabled={busy}
                onClick={() => void dispatch(loadSnapshots({ page: snapshotPage }))}
              >
                R
              </button>
            </div>
            {snapshots.length === 0 ? <p className="muted-small">No memory snapshots yet.</p> : null}
            {snapshots.map((snapshot) => (
              <button
                type="button"
                key={snapshot.snapshot_id}
                className={`job-row ${snapshot.snapshot_id === selectedSnapshotId ? 'selected' : ''}`}
                onClick={() => dispatch(selectSnapshot(snapshot.snapshot_id))}
              >
                <span>{snapshot.status}</span>
                <strong>{snapshot.memory_count ?? 0} memories</strong>
                <small>{formatDate(snapshot.updated_at || snapshot.created_at)}</small>
              </button>
            ))}
            {selectedSnapshot ? (
              <div className="snapshot-actions">
                <p className="muted-small">
                  Markdown is a human preview. JSON is the structured aggregate snapshot payload for restore/import workflows.
                </p>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={busy || !selectedSnapshot.artifacts?.memories_md}
                  onClick={() => void dispatch(exportSnapshot({ snapshotId: selectedSnapshot.snapshot_id }))}
                >
                  Preview MD
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={busy || downloading || !selectedSnapshot.artifacts?.memories}
                  onClick={() => {
                    setDownloadError('');
                    setDownloadInProgress(true);
                    void downloadSnapshotArtifact(selectedSnapshot, 'memories', 'memories.json')
                      .catch((error) => setDownloadError(error instanceof Error ? error.message : String(error)))
                      .finally(() => setDownloadInProgress(false));
                  }}
                >
                  Download JSON
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={busy || downloading || !selectedSnapshot.artifacts?.memories_csv}
                  onClick={() => {
                    setDownloadError('');
                    setDownloadInProgress(true);
                    void downloadSnapshotArtifact(selectedSnapshot, 'memories_csv', 'memories.csv')
                      .catch((error) => setDownloadError(error instanceof Error ? error.message : String(error)))
                      .finally(() => setDownloadInProgress(false));
                  }}
                >
                  Download CSV
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={mutationDisabled}
                  onClick={() => void previewRestore(selectedSnapshot)}
                >
                  Restore Preview
                </button>
                <button
                  type="button"
                  className="danger-button"
                  disabled={mutationDisabled || !restorePreview || restorePreview.snapshot_id !== selectedSnapshot.snapshot_id}
                  onClick={() => void applyRestore(selectedSnapshot)}
                >
                  Restore
                </button>
                <button
                  type="button"
                  className="danger-button"
                  disabled={busy}
                  onClick={() => {
                    if (!window.confirm('Delete this memory snapshot? This does not delete current memories.')) return;
                    void dispatch(deleteSnapshot({ snapshotId: selectedSnapshot.snapshot_id }));
                  }}
                >
                Delete
                </button>
              </div>
            ) : null}
            {snapshots.length > 0 ? (
              <div className="pager compact-pager">
                <button
                  type="button"
                  className="secondary-button"
                  disabled={busy || snapshotPage === 0}
                  onClick={() => void dispatch(loadSnapshots({ page: Math.max(0, snapshotPage - 1) }))}
                >
                  Previous
                </button>
                <span>
                  Page {snapshotPage + 1} · {snapshotsCount || snapshots.length} total
                </span>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={busy || !snapshotsHasMore}
                  onClick={() => void dispatch(loadSnapshots({ page: snapshotPage + 1 }))}
                >
                  Next
                </button>
              </div>
            ) : null}
            {restorePreview && selectedSnapshot && restorePreview.snapshot_id === selectedSnapshot.snapshot_id ? (
              <div className="restore-preview">
                <strong>Restore diff</strong>
                <div className="restore-counts">
                  {Object.entries(restorePreview.counts || {}).map(([key, value]) => (
                    <span key={key}>{key.replace(/_/g, ' ')}: {value}</span>
                  ))}
                </div>
                <p className="muted-small">
                  Restore will apply the snapshot aggregate records and retire current active memories in this scope that are not in the snapshot.
                </p>
                {(restorePreview.changes || []).slice(0, 8).map((change) => (
                  <div className="restore-change" key={`${change.action}-${change.memory_id}`}>
                    <span>{change.action.replace(/_/g, ' ')}</span>
                    <small>{change.memory || change.memory_id}</small>
                  </div>
                ))}
                {restorePreview.truncated ? <p className="muted-small">Preview is truncated.</p> : null}
              </div>
            ) : null}
            {restoreResult?.result ? (
              <div className="restore-preview">
                <strong>Last restore</strong>
                <div className="restore-counts">
                  <span>restored: {restoreResult.result.restored ?? 0}</span>
                  <span>updated: {restoreResult.result.updated ?? 0}</span>
                  <span>inserted: {restoreResult.result.inserted ?? 0}</span>
                  <span>retired extra: {restoreResult.result.retired_extra ?? 0}</span>
                  <span>skipped: {restoreResult.result.skipped_count ?? 0}</span>
                </div>
                {restoreResult.safety_snapshot?.snapshot_id ? (
                  <p className="muted-small">Safety snapshot: {restoreResult.safety_snapshot.snapshot_id}</p>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="reconcile-jobs">
          <div className="reconcile-subhead">
            <h3>Jobs</h3>
            <button
              type="button"
              className="icon-button"
              title="Refresh jobs"
              disabled={busy}
              onClick={() => void dispatch(loadReconciliationJobs({ page: reconciliationJobPage }))}
            >
              R
            </button>
          </div>
          {reconciliationJobs.length === 0 ? <p className="muted-small">No reconciliation jobs yet.</p> : null}
          {reconciliationJobs.map((job) => (
            <button
              type="button"
              key={job.job_id}
              className={`job-row ${job.job_id === selectedReconciliationJobId ? 'selected' : ''}`}
              onClick={() => dispatch(selectReconciliationJob(job.job_id))}
            >
              <span>{job.status}</span>
              <strong>{job.proposal_count ?? 0} actions</strong>
              <small>{job.agent_type || 'regular'} agent</small>
              <small>{formatDate(job.updated_at || job.created_at)}</small>
            </button>
          ))}
          {reconciliationJobs.length > 0 ? (
            <div className="pager compact-pager">
              <button
                type="button"
                className="secondary-button"
                disabled={busy || reconciliationJobPage === 0}
                onClick={() => void dispatch(loadReconciliationJobs({ page: Math.max(0, reconciliationJobPage - 1) }))}
              >
                Previous
              </button>
              <span>
                Page {reconciliationJobPage + 1} · {reconciliationJobsCount || reconciliationJobs.length} total
              </span>
              <button
                type="button"
                className="secondary-button"
                disabled={busy || !reconciliationJobsHasMore}
                onClick={() => void dispatch(loadReconciliationJobs({ page: reconciliationJobPage + 1 }))}
              >
                Next
              </button>
            </div>
          ) : null}
        </div>

        <div className="reconcile-export">
          <div className="reconcile-subhead">
            <h3>Export</h3>
            {selectedJob ? (
              <button
                type="button"
                className="secondary-button"
                disabled={busy || !selectedJob.artifacts?.proposal_md}
                onClick={() => void dispatch(exportReconciliation({ jobId: selectedJob.job_id }))}
              >
                Preview
              </button>
            ) : null}
            {selectedJob ? (
              <button
                type="button"
                className="danger-button"
                disabled={mutationDisabled || selectedJob.status !== 'succeeded' || (selectedJob.proposal_count ?? 0) <= 0}
                onClick={() => {
                  if (!window.confirm('Apply this memory reconciliation proposal? A safety snapshot will be created first.')) return;
                  void dispatch(applyReconciliation({ jobId: selectedJob.job_id })).then(() => {
                    dispatch(loadReconciliationJobs({ page: 0 }));
                    dispatch(loadSnapshots({ page: 0 }));
                    dispatch(loadMemories());
                  });
                }}
              >
                Apply
              </button>
            ) : null}
          </div>
          {selectedJob ? (
            <div className="job-summary">
              <span>{selectedJob.job_id}</span>
              <span>{selectedJob.agent_type || 'regular'} agent</span>
              {selectedJob.role_model?.model ? <span>{selectedJob.role_model.model}</span> : null}
              {selectedJob.snapshot_id ? <span>snapshot {selectedJob.snapshot_id}</span> : null}
              <span>{selectedJob.candidate_count ?? 0} candidates</span>
              <span>{selectedJob.warning_count ?? 0} warnings</span>
            </div>
          ) : <p className="muted-small">Select a job to preview the proposal.</p>}
          {snapshotExport ? <p className="muted-small">Snapshot Markdown preview. Use Download JSON for a restorable payload.</p> : null}
          {reconciliationExport ? <pre className="export-preview">{reconciliationExport}</pre> : null}
          {snapshotExport ? <pre className="export-preview">{snapshotExport}</pre> : null}
        </div>
      </div>
    </section>
  );
}
