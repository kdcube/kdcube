import { useAppDispatch, useAppSelector } from '../../app/hooks';
import type { MemoryEntry } from '../../api/types';
import { loadMemories, loadMemoryEvents, nextPage, previousPage, selectMemory } from './memoriesSlice';

function percent(value: number): string {
  return `${Math.round(Math.max(0, Math.min(1, value || 0)) * 100)}%`;
}

function memoryContextPayload(memory: MemoryEntry) {
  const ref = `mem:${memory.id}`;
  return {
    id: ref,
    kind: 'memory',
    label: memory.memory,
    summary: memory.context || undefined,
    ref,
    logical_path: ref,
    mime: 'application/json',
    data: {
      memory_id: memory.id,
      bundle_id: memory.bundle_id,
      kind: memory.kind,
      status: memory.status,
      tier: memory.tier,
      pinned: memory.pinned,
      labels: memory.labels,
      keywords: memory.keywords,
    },
  };
}

function setMemoryDragData(dataTransfer: DataTransfer, memory: MemoryEntry): void {
  const payload = memoryContextPayload(memory);
  const message = {
    type: 'task-tracker-context-focus',
    source: 'memories-widget',
    contexts: [payload],
  };
  dataTransfer.effectAllowed = 'copy';
  dataTransfer.setData('application/json', JSON.stringify(message));
  dataTransfer.setData('text/plain', memory.memory);
  dataTransfer.setData('text/uri-list', payload.ref);
  if (window.parent && window.parent !== window) {
    window.parent.postMessage({
      type: 'task-tracker-context-drag-start',
      source: 'memories-widget',
      context: payload,
    }, '*');
  }
}

function clearMemoryDragData(): void {
  if (window.parent && window.parent !== window) {
    window.parent.postMessage({
      type: 'task-tracker-context-drag-end',
      source: 'memories-widget',
    }, '*');
  }
}

export function MemoryList() {
  const dispatch = useAppDispatch();
  const { count, hasMore, loading, memories, page, pageSize, selectedId, viewMode } = useAppSelector((state) => state.memories);
  const compact = viewMode === 'compact';

  if (loading) return <div className="empty-state">Opening notes...</div>;
  if (memories.length === 0) return <div className="empty-state">{compact ? 'No matching memories.' : 'No memory notes yet.'}</div>;

  if (compact) {
    return (
      <section className="memory-list compact-memory-list" aria-label="Memories">
        {memories.slice(0, 2).map((memory) => (
          <button
            key={memory.id}
            className={`memory-row compact-memory-row tone-${memory.status || 'active'} ${memory.id === selectedId ? 'selected' : ''}`}
            draggable
            onDragStart={(event) => setMemoryDragData(event.dataTransfer, memory)}
            onDragEnd={clearMemoryDragData}
            onClick={() => {
              dispatch(selectMemory(memory.id));
              void dispatch(loadMemoryEvents(memory.id));
            }}
            title="Drag to canvas or click to open memory detail"
          >
            <div className="memory-row-main">
              <span className="memory-title">{memory.memory}</span>
              <span className="memory-bundle">{memory.pinned ? 'pinned' : memory.kind || 'memory'}</span>
            </div>
            {memory.context ? <span className="memory-context">{memory.context}</span> : null}
            <div className="term-row compact-terms">
              {[...memory.labels, ...memory.keywords].slice(0, 4).map((term) => (
                <span key={`${memory.id}-term-${term}`}>{term}</span>
              ))}
            </div>
          </button>
        ))}
      </section>
    );
  }

  const sorted = [...memories].sort((left, right) => (
    left.tier - right.tier
    || Number(right.pinned) - Number(left.pinned)
    || right.salience_score - left.salience_score
    || String(right.updated_at).localeCompare(String(left.updated_at))
  ));
  const grouped = sorted.reduce<Record<string, typeof sorted>>((acc, memory) => {
    const key = String(memory.tier || 3);
    acc[key] = acc[key] || [];
    acc[key].push(memory);
    return acc;
  }, {});

  return (
    <section className="memory-list" aria-label="Memories">
      {Object.entries(grouped).map(([tier, tierMemories]) => (
        <div className="tier-group" key={tier}>
          <h3>Tier {tier}</h3>
          {tierMemories.map((memory) => (
            <button
              key={memory.id}
              className={`memory-row tone-${memory.status || 'active'} ${memory.id === selectedId ? 'selected' : ''}`}
              draggable
              onDragStart={(event) => setMemoryDragData(event.dataTransfer, memory)}
              onDragEnd={clearMemoryDragData}
              onClick={() => {
                dispatch(selectMemory(memory.id));
                void dispatch(loadMemoryEvents(memory.id));
              }}
              title="Drag to canvas or click to open memory detail"
            >
              <div className="memory-row-main">
                <span className="memory-title">{memory.memory}</span>
                <span className="memory-bundle">{memory.pinned ? 'pinned' : memory.kind || memory.bundle_id || 'global'}</span>
              </div>
              <div className="term-row compact-terms">
                {memory.labels.map((label) => <span key={`${memory.id}-label-${label}`}>{label}</span>)}
                {memory.keywords.map((keyword) => <span key={`${memory.id}-keyword-${keyword}`}>{keyword}</span>)}
              </div>
              <div className="memory-meta">
                <span>Tier {memory.tier}</span>
                {memory.pinned ? <span>Pinned</span> : null}
                <span>Salience {percent(memory.salience_score)}</span>
                <span>{memory.evidence_count} events</span>
              </div>
            </button>
          ))}
        </div>
      ))}
      <div className="pager">
        <button
          type="button"
          className="secondary-button"
          disabled={page === 0}
          onClick={() => {
            dispatch(previousPage());
            void dispatch(loadMemories());
          }}
        >
          Previous
        </button>
        <span>{count > 0 ? `${page * pageSize + 1}-${Math.min(count, page * pageSize + memories.length)} of ${count}` : '0'}</span>
        <button
          type="button"
          className="secondary-button"
          disabled={!hasMore}
          onClick={() => {
            dispatch(nextPage());
            void dispatch(loadMemories());
          }}
        >
          Next
        </button>
      </div>
    </section>
  );
}
