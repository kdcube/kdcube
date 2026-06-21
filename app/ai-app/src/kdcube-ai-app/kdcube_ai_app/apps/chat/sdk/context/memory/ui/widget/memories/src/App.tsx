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

const SURFACE_COMMAND_MESSAGE_TYPE = 'kdcube.surface.command';

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

// Single-record mode: a dedicated host window opens the widget with ?record=<mem id>
// (mirrors the task-tracker wizard). It renders ONLY the MemoryDetail for that one
// record, reusing the same component + styles as the full widget — no list chrome.
function recordFromLocation(): string {
  const params = new URLSearchParams(window.location.search);
  return String(params.get('record') || params.get('memory_id') || '').trim();
}

function memRefsFromValue(value: unknown): string[] {
  if (!value || typeof value !== 'object') {
    if (typeof value === 'string' && (value.trim().startsWith('mem:') || value.trim().startsWith('me:'))) return [value.trim()];
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
  ].filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
  const refs = direct.flatMap((item) => {
    const text = item.trim();
    return text.startsWith('mem:') || text.startsWith('me:') ? [text] : [];
  });
  const kind = String(raw.kind || raw.type || '').trim();
  const rawMemoryId = typeof data.memory_id === 'string' ? data.memory_id.trim() : '';
  if (kind.includes('memory') && rawMemoryId) refs.push(`mem:record:${normalizeMemoryRefs(rawMemoryId)[0] || rawMemoryId}`);
  return refs;
}

function memoryIdsFromPayload(value: unknown): string[] {
  if (!value || typeof value !== 'object') return [];
  const raw = value as Record<string, unknown>;
  const rawItems = Array.isArray(raw.contexts)
    ? raw.contexts
    : Array.isArray(raw.cards)
      ? raw.cards
      : [raw.context, raw];
  return normalizeMemoryRefs(rawItems.flatMap(memRefsFromValue));
}

function isMemorySurfaceCommand(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false;
  const raw = value as Record<string, unknown>;
  if (raw.type !== SURFACE_COMMAND_MESSAGE_TYPE) return false;
  const target = typeof raw.target_surface === 'string' ? raw.target_surface.trim().toLowerCase() : '';
  return !target || target === 'sdk.memory.viewer' || target === 'sdk.memory.list' || target.includes('memory');
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
  const singleRecord = useMemo(() => recordFromLocation(), []);
  // Single-record window: ?single=1 (the dedicated host window opens with this;
  // the record arrives via the host 'open' command, like the task wizard) OR a
  // record id baked straight into ?record=<id>.
  const singleMode = useMemo(() => {
    if (recordFromLocation()) return true;
    const params = new URLSearchParams(window.location.search);
    const value = String(params.get('single') || '').trim().toLowerCase();
    return value === '1' || value === 'true' || value === 'yes';
  }, []);
  const [compact, setCompact] = useState(initialCompact);
  const [editorMode, setEditorMode] = useState<'create' | 'edit' | ''>('');
  const [maintenanceOpen, setMaintenanceOpen] = useState(false);
  const [dropActive, setDropActive] = useState(false);
  const selectedMemory = memories.find((memory) => memory.id === selectedId);
  // In expanded view we always show the side-panel when a focus filter
  // is active — the right column then carries MemoryDetail for the
  // dropped/opened memory so the bottom half of the widget is filled
  // with useful content instead of an empty grid below a single row.
  const hasFocus = focusedMemoryIds.length > 0;
  const showSidePanel = !compact && (editorMode === 'create' || editorMode === 'edit' || Boolean(selectedMemory) || hasFocus);

  function focusMemoryIdsKeepView(memoryIds: string[]) {
    // Used by the drop handler. The widget's current view (compact or full)
    // is preserved — drop is a focus/filter gesture, not a view switch. The
    // user can resize the pane separately if they want more room.
    const ids = normalizeMemoryRefs(memoryIds);
    if (!ids.length) return;
    setEditorMode('');
    dispatch(focusMemories(ids));
    void dispatch(loadMemories()).then(() => {
      void dispatch(loadMemoryEvents(ids[0]));
    });
  }

  function focusAndExpandMemoryIds(memoryIds: string[]) {
    // Used by the host 'open' message — an explicit request to open this
    // memory in the full editor layout.
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

  // Single-record mode boot: focus + load the one record named in ?record=<id>,
  // reusing the same path the host 'open' command uses.
  useEffect(() => {
    if (singleRecord) focusAndExpandMemoryIds([singleRecord]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [singleRecord]);

  useEffect(() => {
    document.documentElement.dataset.memoryView = compact ? 'compact' : 'full';
    return () => {
      delete document.documentElement.dataset.memoryView;
    };
  }, [compact]);

  // When the host iframe is resized, Chrome leaves the iframe's wheel
  // hit-test region stale, so touchpad scroll stops working until a REAL
  // scroll happens (the user found dragging the scrollbar wakes it).
  // We reproduce that real scroll: move the scroll container down 1px in
  // one frame and back in the NEXT frame. Doing both in the same frame
  // coalesces to a no-op (no scroll event, no compositor wake) — the
  // cross-frame split is what makes it count. Triggered by the native
  // window-resize event and by an explicit host wake-scroll command.
  useEffect(() => {
    const scroller = () =>
      document.querySelector('.expanded-shell, .compact-shell') as HTMLElement | null;
    const jiggle = () => {
      const el = scroller();
      if (!el || el.scrollHeight <= el.clientHeight) return;
      const top = el.scrollTop;
      const bumped = top === 0 ? 1 : top - 1;
      el.scrollTop = bumped;
      window.requestAnimationFrame(() => {
        const back = scroller();
        if (back) back.scrollTop = top;
      });
    };
    const wakeBurst = () => {
      jiggle();
      window.setTimeout(jiggle, 90);
      window.setTimeout(jiggle, 260);
    };
    const onHostWake = (event: MessageEvent) => {
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      if (isMemorySurfaceCommand(data) && data.action === 'wake-scroll') {
        wakeBurst();
      }
    };
    window.addEventListener('resize', wakeBurst);
    window.addEventListener('message', onHostWake);
    return () => {
      window.removeEventListener('resize', wakeBurst);
      window.removeEventListener('message', onHostWake);
    };
  }, []);

  useEffect(() => {
    function onHostMessage(event: MessageEvent) {
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      if (data.type === 'kdcube-set-view') {
        if (data.view === 'expanded') setCompact(false);
        if (data.view === 'compact') setCompact(true);
        return;
      }
      const isSurfaceCommand = isMemorySurfaceCommand(data);
      if (!isSurfaceCommand) return;
      if (data.action === 'create') {
        setCompact(false);
        setEditorMode('create');
        return;
      }
      if (data.action === 'open') {
        const memoryIds = isSurfaceCommand
          ? memoryIdsFromPayload(data)
          : normalizeMemoryRefs([
              data.memory_id,
              data.object_ref,
              ...(Array.isArray(data.memory_ids) ? data.memory_ids : []),
              ...(Array.isArray(data.object_refs) ? data.object_refs : []),
            ]);
        focusAndExpandMemoryIds(memoryIds);
        return;
      }
      // Focus a set of records in the list WITHOUT forcing the expanded view —
      // used when several memories are dropped at once (keep the current form).
      if (data.action === 'focus') {
        const memoryIds = isSurfaceCommand
          ? memoryIdsFromPayload(data)
          : normalizeMemoryRefs([
              data.memory_id,
              data.object_ref,
              ...(Array.isArray(data.memory_ids) ? data.memory_ids : []),
              ...(Array.isArray(data.object_refs) ? data.object_refs : []),
            ]);
        focusMemoryIdsKeepView(memoryIds);
        return;
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
    focusMemoryIdsKeepView(memoryIds);
  }

  // Dedicated single-record window: just the detail (or its editor), reusing the
  // same component + styles as the full widget. No list / filters / shell chrome.
  if (singleMode) {
    return (
      <div className="memory-single">
        {error ? (
          <div className="error-box dismissible-error">
            <span>{error}</span>
            <button type="button" onClick={() => dispatch(clearTransientErrors())}>Dismiss</button>
          </div>
        ) : null}
        {editorMode === 'edit' && selectedMemory ? (
          <MemoryEditor mode="edit" memory={selectedMemory} onClose={() => setEditorMode('')} />
        ) : (
          <MemoryDetail onEdit={() => setEditorMode('edit')} single />
        )}
      </div>
    );
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
      maintenanceOpen={maintenanceOpen}
      onToggleMaintenance={() => setMaintenanceOpen((open) => !open)}
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
      <MemoryFilters maintenanceOpen={maintenanceOpen} />
      {compact || !maintenanceOpen ? null : <ReconciliationPanel />}
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
