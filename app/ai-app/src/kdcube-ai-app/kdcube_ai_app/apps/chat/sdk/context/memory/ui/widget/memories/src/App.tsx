import { useEffect, useMemo, useState, type DragEvent } from 'react';
import { AppShell } from './components/AppShell';
import { useAppDispatch, useAppSelector } from './app/hooks';
import { settings } from './api/settings';
import { MemoryDetail } from './features/memories/MemoryDetail';
import { MemoryEditor } from './features/memories/MemoryEditor';
import { MemoryFilters } from './features/memories/MemoryFilters';
import { MemoryList } from './features/memories/MemoryList';
import { ReconciliationPanel } from './features/memories/ReconciliationPanel';
import {
  clearTransientErrors,
  focusMemories,
  loadMemories,
  loadMemoryEvents,
  normalizeMemoryRefs,
  setViewMode,
  updateMemoryPreferences,
} from './features/memories/memoriesSlice';

function compactModeFromLocation(): boolean {
  const params = new URLSearchParams(window.location.search);
  const view = String(params.get('view') || params.get('mode') || '').trim().toLowerCase();
  const compact = String(params.get('compact') || '').trim().toLowerCase();
  return view === 'compact' || compact === '1' || compact === 'true' || compact === 'yes';
}

function hostControlsFromLocation(): boolean {
  const params = new URLSearchParams(window.location.search);
  const value = String(params.get('host_controls') || params.get('hostControls') || '').trim().toLowerCase();
  return value === '1' || value === 'true' || value === 'yes';
}

function memRefsFromValue(value: unknown): string[] {
  if (!value || typeof value !== 'object') {
    if (typeof value === 'string' && value.trim().startsWith('mem:')) return [value.trim()];
    return [];
  }
  const raw = value as Record<string, unknown>;
  const data = raw.data && typeof raw.data === 'object' ? raw.data as Record<string, unknown> : {};
  const direct = [
    raw.object_ref,
    raw.ref,
    raw.logical_path,
    raw.logicalPath,
    raw.id,
    data.object_ref,
    data.memory_id,
  ].filter((item): item is string => typeof item === 'string' && item.trim());
  const refs = direct.flatMap((item) => item.trim().startsWith('mem:') ? [item.trim()] : []);
  const kind = String(raw.kind || raw.type || '').trim();
  const rawMemoryId = typeof data.memory_id === 'string' ? data.memory_id.trim() : '';
  if (kind.includes('memory') && rawMemoryId) refs.push(`mem:${normalizeMemoryRefs(rawMemoryId)[0] || rawMemoryId}`);
  return refs;
}

function memoryIdsFromPayload(value: unknown): string[] {
  if (!value || typeof value !== 'object') return [];
  const raw = value as Record<string, unknown>;
  const rawItems = Array.isArray(raw.contexts)
    ? raw.contexts
    : Array.isArray(raw.items)
      ? raw.items
      : Array.isArray(raw.cards)
        ? raw.cards
        : [raw.context, raw];
  return normalizeMemoryRefs(rawItems.flatMap(memRefsFromValue));
}

function memoryIdsFromDataTransfer(dataTransfer: DataTransfer): string[] {
  const candidates: string[] = [];
  const jsonTypes = ['application/vnd.kdcube.context+json', 'application/json'];
  jsonTypes.forEach((mimeType) => {
    const raw = dataTransfer.getData(mimeType);
    if (!raw) return;
    try {
      candidates.push(...memoryIdsFromPayload(JSON.parse(raw)));
    } catch {
      // Ignore non-JSON drag payloads for this MIME type.
    }
  });
  candidates.push(...normalizeMemoryRefs(dataTransfer.getData('text/uri-list')));
  return normalizeMemoryRefs(candidates);
}

export default function App() {
  const dispatch = useAppDispatch();
  const {
    allowWrite,
    count,
    error,
    focusedMemoryIds,
    memories,
    memoryUseEnabled,
    mutationError,
    saving,
    selectedId,
  } = useAppSelector((state) => state.memories);
  const initialCompact = useMemo(() => compactModeFromLocation(), []);
  const hostControls = useMemo(() => hostControlsFromLocation(), []);
  const [compact, setCompact] = useState(initialCompact);
  const [editorMode, setEditorMode] = useState<'create' | 'edit' | ''>('');
  const [maintenanceOpen, setMaintenanceOpen] = useState(false);
  const [dropActive, setDropActive] = useState(false);
  const selectedMemory = memories.find((memory) => memory.id === selectedId);
  const showSidePanel = !compact && (editorMode === 'create' || editorMode === 'edit' || Boolean(selectedMemory));

  function focusMemoryIds(memoryIds: string[]) {
    const ids = normalizeMemoryRefs(memoryIds);
    if (!ids.length) return;
    setCompact(false);
    setEditorMode('');
    dispatch(setViewMode('full'));
    dispatch(focusMemories(ids));
    void dispatch(loadMemories()).then(() => {
      void dispatch(loadMemoryEvents(ids[0]));
    });
  }

  useEffect(() => {
    dispatch(setViewMode(compact ? 'compact' : 'full'));
    void settings.setupParentListener().then(() => dispatch(loadMemories()));
  }, [compact, dispatch]);

  useEffect(() => {
    document.documentElement.dataset.memoryView = compact ? 'compact' : 'full';
    return () => {
      delete document.documentElement.dataset.memoryView;
    };
  }, [compact]);

  useEffect(() => {
    function onHostMessage(event: MessageEvent) {
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      if (data.type === 'kdcube-set-view') {
        if (data.view === 'expanded') setCompact(false);
        if (data.view === 'compact') setCompact(true);
        return;
      }
      if (data.type !== 'kdcube-memory-widget-command' || data.widget !== 'memories') return;
      if (data.action === 'create') {
        setCompact(false);
        setEditorMode('create');
        return;
      }
      if (data.action === 'open') {
        const memoryIds = normalizeMemoryRefs([
          data.memory_id,
          data.object_ref,
          ...(Array.isArray(data.memory_ids) ? data.memory_ids : []),
          ...(Array.isArray(data.object_refs) ? data.object_refs : []),
        ]);
        focusMemoryIds(memoryIds);
      }
    }
    window.addEventListener('message', onHostMessage);
    return () => window.removeEventListener('message', onHostMessage);
  }, [dispatch]);

  useEffect(() => {
    if (!window.parent || window.parent === window) return;
    window.parent.postMessage({
      type: 'kdcube-memory-widget-status',
      widget: 'memories',
      count,
      compact,
      memoryUseEnabled,
    }, '*');
  }, [compact, count, memoryUseEnabled]);

  const requestExpand = () => {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ type: 'kdcube-widget-view', widget: 'memories', view: 'expanded' }, '*');
      return;
    }
    setCompact(false);
  };

  const toggleMemoryUse = () => {
    void dispatch(updateMemoryPreferences({ memoryEnabled: !memoryUseEnabled })).then(() => dispatch(loadMemories()));
  };

  function handleDragOver(event: DragEvent<HTMLElement>) {
    if (!Array.from(event.dataTransfer.types || []).some((type) => (
      type === 'application/vnd.kdcube.context+json' ||
      type === 'application/json' ||
      type === 'text/uri-list'
    ))) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    setDropActive(true);
  }

  function handleDrop(event: DragEvent<HTMLElement>) {
    const memoryIds = memoryIdsFromDataTransfer(event.dataTransfer);
    setDropActive(false);
    if (!memoryIds.length) return;
    event.preventDefault();
    focusMemoryIds(memoryIds);
  }

  return (
    <AppShell
      allowWrite={allowWrite}
      count={count}
      memoryUseEnabled={memoryUseEnabled}
      saving={saving}
      dropActive={dropActive}
      compact={compact}
      hostControls={hostControls}
      onCreate={() => setEditorMode('create')}
      onExpand={compact && !hostControls ? requestExpand : undefined}
      onToggleMemoryUse={toggleMemoryUse}
      onDragLeave={() => setDropActive(false)}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {compact ? (
        <label className="compact-memory-toggle">
          <input
            type="checkbox"
            checked={memoryUseEnabled}
            disabled={saving}
            onChange={toggleMemoryUse}
          />
          <span>Use my memory</span>
        </label>
      ) : null}
      {!memoryUseEnabled ? (
        <div className="warning-box">
          Memory use is off. Existing notes remain visible for review, export, or deletion.
        </div>
      ) : null}
      <MemoryFilters />
      {compact ? null : (
        <>
          <div className="maintenance-toggle-row">
            <button
              type="button"
              className="secondary-button"
              onClick={() => setMaintenanceOpen((open) => !open)}
            >
              {maintenanceOpen ? 'Hide maintenance' : 'Show maintenance'}
            </button>
          </div>
          {maintenanceOpen ? <ReconciliationPanel /> : null}
        </>
      )}
      {error ? (
        <div className="error-box dismissible-error">
          <span>{error}</span>
          <button type="button" onClick={() => dispatch(clearTransientErrors())}>Dismiss</button>
        </div>
      ) : null}
      {mutationError ? (
        <div className="error-box dismissible-error">
          <span>{mutationError}</span>
          <button type="button" onClick={() => dispatch(clearTransientErrors())}>Dismiss</button>
        </div>
      ) : null}
      <div className={`workspace ${compact ? 'compact-workspace' : ''} ${showSidePanel ? 'with-side-panel' : ''}`}>
        <MemoryList />
        {showSidePanel ? <div className="side-panel">
          {editorMode === 'create' ? (
            <MemoryEditor mode="create" onClose={() => setEditorMode('')} />
          ) : null}
          {editorMode === 'edit' && selectedMemory ? (
            <MemoryEditor mode="edit" memory={selectedMemory} onClose={() => setEditorMode('')} />
          ) : null}
          {!editorMode ? <MemoryDetail onEdit={() => setEditorMode('edit')} /> : null}
        </div> : null}
      </div>
    </AppShell>
  );
}
