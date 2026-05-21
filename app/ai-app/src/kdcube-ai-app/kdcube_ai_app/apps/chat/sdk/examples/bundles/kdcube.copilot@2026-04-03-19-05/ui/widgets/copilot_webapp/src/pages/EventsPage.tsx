import type { CopilotEventsPayload } from '../store/types';

interface EventsPageProps {
  events?: CopilotEventsPayload;
  reload: () => void;
}

function formatTime(value?: string): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function shortId(value?: string | null): string {
  if (!value) return '';
  return value.length > 12 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value;
}

export function EventsPage({ events, reload }: EventsPageProps) {
  const items = events?.events || [];
  const bySource = Object.entries(events?.by_source || {}).sort((a, b) => b[1] - a[1]);
  const byType = Object.entries(events?.by_type || {}).sort((a, b) => b[1] - a[1]).slice(0, 5);

  return (
    <section className="page page-wide events-page">
      <div className="page-header">
        <div>
          <h1>Events</h1>
          <p>{events?.bundle_id || 'kdcube.copilot'} - {events?.count || 0} retained</p>
        </div>
        <button type="button" className="ghost-button" onClick={reload}>Refresh</button>
      </div>

      {events?.error && <div className="notice error">{events.error}</div>}

      <div className="event-summary">
        <div>
          <span className="summary-label">Sources</span>
          <div className="summary-chips">
            {bySource.length === 0 && <span className="muted-chip">None</span>}
            {bySource.map(([source, count]) => (
              <span key={source} className="metric-chip">{source}<b>{count}</b></span>
            ))}
          </div>
        </div>
        <div>
          <span className="summary-label">Top types</span>
          <div className="summary-chips">
            {byType.length === 0 && <span className="muted-chip">None</span>}
            {byType.map(([type, count]) => (
              <span key={type} className="metric-chip">{type}<b>{count}</b></span>
            ))}
          </div>
        </div>
      </div>

      <div className="event-list">
        {items.length === 0 && (
          <div className="empty-state">No copilot events have been recorded yet.</div>
        )}
        {items.map((item) => (
          <article key={item.event_id} className="event-row">
            <div className="event-row-main">
              <div className="event-title-line">
                <span className={`status-dot status-${String(item.status || 'unknown').toLowerCase()}`} />
                <strong>{item.title || item.type || 'Event'}</strong>
                <span className="event-type">{item.type}</span>
              </div>
              <div className="event-meta">
                <span>{formatTime(item.timestamp_iso)}</span>
                <span>{item.source}</span>
                {item.step && <span>{item.step}</span>}
                {item.context?.turn_id && <span>turn {shortId(item.context.turn_id)}</span>}
              </div>
            </div>
            <div className="event-data">
              {item.data?.tool !== undefined && <span>tool: {String(item.data.tool)}</span>}
              {item.data?.duration_ms !== undefined && <span>{String(item.data.duration_ms)} ms</span>}
              {item.data?.result_count !== undefined && <span>{String(item.data.result_count)} results</span>}
              {item.data?.has_answer !== undefined && <span>answer: {String(item.data.has_answer)}</span>}
              {item.status && <span>{item.status}</span>}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
