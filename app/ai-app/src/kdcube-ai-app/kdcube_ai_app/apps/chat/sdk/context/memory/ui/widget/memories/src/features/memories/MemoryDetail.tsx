import { useEffect } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { confirmMemory, loadMemoryEvents, pinMemory, retireMemory } from './memoriesSlice';

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

interface MemoryDetailProps {
  onEdit: () => void;
}

export function MemoryDetail({ onEdit }: MemoryDetailProps) {
  const dispatch = useAppDispatch();
  const { allowWrite, eventsLoading, memories, memoryUseEnabled, saving, selectedEvents, selectedId } = useAppSelector((state) => state.memories);
  const memory = memories.find((item) => item.id === selectedId);

  useEffect(() => {
    if (selectedId) void dispatch(loadMemoryEvents(selectedId));
  }, [dispatch, selectedId]);

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

      {allowWrite ? (
        <div className="detail-actions">
          <button type="button" className="secondary-button" onClick={onEdit} disabled={saving || !memoryUseEnabled}>Edit</button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => void dispatch(confirmMemory(memory.id)).then(() => dispatch(loadMemoryEvents(memory.id)))}
            disabled={saving || !memoryUseEnabled}
          >
            Confirm
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => void dispatch(pinMemory({ id: memory.id, pinned: !memory.pinned })).then(() => dispatch(loadMemoryEvents(memory.id)))}
            disabled={saving || !memoryUseEnabled || memory.status !== 'active'}
          >
            {memory.pinned ? 'Unpin' : 'Pin'}
          </button>
          <button
            type="button"
            className="danger-button"
            onClick={() => {
              if (!window.confirm('Delete this memory note and its memory events?')) return;
              void dispatch(retireMemory(memory.id));
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
        {memory.labels.map((label) => <span key={`label-${label}`}>{label}</span>)}
        {memory.keywords.map((keyword) => <span key={`keyword-${keyword}`}>{keyword}</span>)}
      </div>

      <section className="events">
        <h3>Evidence</h3>
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
          </article>
        ))}
      </section>
    </aside>
  );
}
