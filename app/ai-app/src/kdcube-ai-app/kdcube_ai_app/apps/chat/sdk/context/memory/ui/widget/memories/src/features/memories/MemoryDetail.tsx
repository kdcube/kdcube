import { useEffect, useState } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import type { MemoryEntry } from '../../api/types';
import {
  applyEvidence,
  clearTransientErrors,
  confirmMemory,
  deleteEvidence,
  loadMemories,
  loadMemoryEvents,
  pinMemory,
  retireMemory,
} from './memoriesSlice';

// Map an evidence apply/drop failure to a design-system notice. The soft-tip
// (green) variant is for the recoverable "keep one revision" guard; everything
// else is a critical (red) notice.
function evidenceNotice(error: string): { tone: 'success' | 'error'; message: string } | null {
  if (!error) return null;
  if (error === 'memory_requires_at_least_one_evidence') {
    return { tone: 'success', message: 'A memory must keep at least one revision.' };
  }
  if (error === 'revision_conflict') {
    return { tone: 'error', message: 'This memory changed elsewhere — reload to see the latest.' };
  }
  return { tone: 'error', message: error };
}

function formatDate(value: string): string {
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

function uniqueTerms(...groups: string[][]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  groups.flat().forEach((term) => {
    const clean = String(term || '').trim();
    const key = clean.toLowerCase();
    if (!clean || seen.has(key)) return;
    seen.add(key);
    result.push(clean);
  });
  return result;
}

function memoryContextPayload(memory: MemoryEntry) {
  const ref = `mem:record:${memory.id}`;
  return {
    id: ref,
    kind: 'memory',
    label: memory.memory,
    summary: memory.context || undefined,
    ref,
    object_ref: ref,
    logical_path: ref,
    mime: 'application/json',
    namespace: 'mem',
    object_kind: 'memory.record',
    event_source_id: 'memory.context',
    surface: 'memory.widget',
    data: {
      memory_id: memory.id,
      object_ref: ref,
      namespace: 'mem',
      object_kind: 'memory.record',
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

interface MemoryDetailProps {
  onEdit: () => void;
  // When true the detail is already in its own dedicated window — hide the
  // "Open in window" affordance (it would be redundant / recursive).
  single?: boolean;
}

export function MemoryDetail({ onEdit, single = false }: MemoryDetailProps) {
  const dispatch = useAppDispatch();
  const { allowWrite, eventsLoading, evidenceError, memories, saving, selectedEvents, selectedId } = useAppSelector((state) => state.memories);
  const memory = memories.find((item) => item.id === selectedId);
  const terms = memory ? uniqueTerms(memory.labels, memory.keywords) : [];
  const notice = evidenceNotice(evidenceError);
  // The evidence entry pending a Drop confirmation (null = dialog closed).
  const [dropEventId, setDropEventId] = useState<string | null>(null);

  useEffect(() => {
    if (selectedId) void dispatch(loadMemoryEvents(selectedId));
  }, [dispatch, selectedId]);

  // Close any open Drop confirmation when the selected note changes.
  useEffect(() => {
    setDropEventId(null);
  }, [selectedId]);

  if (!memory) return <aside className="memory-detail empty-detail">Select a note.</aside>;

  return (
    <aside className="memory-detail">
      <div className="detail-head">
        <div>
          <span className="eyebrow">{memory.kind || 'memory'}</span>
          <h2>{memory.memory}</h2>
          <p>{memory.context || 'No context or reason recorded.'}</p>
        </div>
        <span className={`status-pill status-${memory.status}`}>{memory.status}</span>
      </div>

      {!single ? (
        <div className="detail-actions detail-actions-open">
          <button
            type="button"
            className="secondary-button"
            title="Open this memory in its own editor window"
            onClick={() => {
              try {
                const context = memoryContextPayload(memory);
                window.parent.postMessage({
                  type: 'kdcube.surface.command',
                  target_surface: 'sdk.memory.viewer',
                  action: 'open',
                  widget: 'memories',
                  memory_id: memory.id,
                  object_ref: context.object_ref,
                  context,
                }, '*');
              } catch {
                /* no host listening — inline-only context */
              }
            }}
          >
            Open in Editor
          </button>
        </div>
      ) : null}

      {allowWrite ? (
        <div className="detail-actions">
          <button type="button" className="secondary-button" onClick={onEdit} disabled={saving}>Edit</button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => void dispatch(confirmMemory(memory.id)).then(() => dispatch(loadMemoryEvents(memory.id)))}
            disabled={saving}
          >
            Confirm
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => void dispatch(pinMemory({ id: memory.id, pinned: !memory.pinned })).then(() => dispatch(loadMemoryEvents(memory.id)))}
            disabled={saving || memory.status !== 'active'}
          >
            {memory.pinned ? 'Unpin' : 'Pin'}
          </button>
          <button
            type="button"
            className="danger-button"
            onClick={() => {
              if (!window.confirm('Delete this memory note and its memory events?')) return;
              void dispatch(retireMemory(memory.id)).finally(() => dispatch(loadMemories()));
            }}
            disabled={saving}
          >
            Delete
          </button>
        </div>
      ) : null}

      <dl className="score-grid">
        <div>
          <dt>Tier</dt>
          <dd>{memory.tier}{memory.pinned ? ' pinned' : ''}</dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>{Math.round(memory.confidence_score * 100)}%</dd>
        </div>
        <div>
          <dt>Importance</dt>
          <dd>{Math.round(memory.importance_score * 100)}%</dd>
        </div>
        <div>
          <dt>Updated</dt>
          <dd>{formatDate(memory.updated_at)}</dd>
        </div>
      </dl>

      <div className="term-row">
        {terms.map((term) => <span key={`term-${term}`}>{term}</span>)}
      </div>

      <section className="events">
        <h3>Evidence</h3>
        {notice ? (
          <div className={`notice ${notice.tone}`} role="status">
            <span>{notice.message}</span>
            <button type="button" onClick={() => dispatch(clearTransientErrors())}>Dismiss</button>
          </div>
        ) : null}
        {eventsLoading && <div className="empty-state compact">Loading evidence...</div>}
        {!eventsLoading && selectedEvents.length === 0 && <div className="empty-state compact">No evidence events found.</div>}
        {!eventsLoading && selectedEvents.map((event) => (
          <article className="event-row" key={event.id}>
            <div className="event-top">
              <strong>{event.event_type}</strong>
              <span>{formatDate(event.created_at)}</span>
            </div>
            <p>{event.signal_text}</p>
            {event.context && <p className="event-context">{event.context}</p>}
            {event.originator && (
              <div className="event-meta">
                by <span className="event-originator">{event.originator}</span>
              </div>
            )}
            {allowWrite ? (
              <div className="event-actions">
                <button
                  type="button"
                  className="secondary-button"
                  title="Promote this revision's text to the canonical note"
                  disabled={saving}
                  onClick={() => void dispatch(applyEvidence({
                    memoryId: memory.id,
                    eventId: event.id,
                    baseRevision: memory.revision,
                  }))}
                >
                  Apply
                </button>
                <button
                  type="button"
                  className="danger-button"
                  title="Delete this revision and re-derive the note"
                  disabled={saving}
                  onClick={() => setDropEventId(event.id)}
                >
                  Drop
                </button>
              </div>
            ) : null}
          </article>
        ))}
      </section>

      {dropEventId ? (
        <ConfirmDialog
          title="Drop this revision?"
          message="Drop this revision? The note will be re-derived from the remaining evidence."
          confirmLabel="Drop"
          tone="danger"
          busy={saving}
          onCancel={() => setDropEventId(null)}
          onConfirm={() => {
            const eventId = dropEventId;
            setDropEventId(null);
            void dispatch(deleteEvidence({
              memoryId: memory.id,
              eventId,
              baseRevision: memory.revision,
            }));
          }}
        />
      ) : null}
    </aside>
  );
}
