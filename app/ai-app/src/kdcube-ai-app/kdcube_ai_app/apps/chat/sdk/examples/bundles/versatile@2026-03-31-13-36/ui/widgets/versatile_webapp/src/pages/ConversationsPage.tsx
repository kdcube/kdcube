import { useState } from 'react';
import { assertOk, callOperation, conversationItems } from '../store/apiClient';
import type { ConversationsPayload } from '../store/types';
import { fmt } from './pageUtils';

interface ConversationsPageProps {
  conversations?: ConversationsPayload;
  reload: () => Promise<void>;
}

export function ConversationsPage({ conversations, reload }: ConversationsPageProps) {
  const [title, setTitle] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const items = conversationItems(conversations);

  async function mutate(operation: string, payload: Record<string, unknown>) {
    setBusy(true);
    setError('');
    try {
      const result = await callOperation<ConversationsPayload>(operation, payload);
      assertOk(result, 'Operation failed');
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <h1>Chats</h1>
          <p>{fmt(conversations?.telegram_user_id || conversations?.kdcube_user_id)} · {items.length} channels</p>
        </div>
        <button type="button" className="ghost-button" onClick={reload} disabled={busy}>Refresh</button>
      </div>
      {conversations?.error?.message && <div className="notice error">{conversations.error.message}</div>}
      {error && <div className="notice error">{error}</div>}
      <div className="new-row">
        <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="New chat title" />
        <button
          type="button"
          className="primary-button"
          disabled={busy}
          onClick={() => {
            void mutate('conversations_create', { title });
            setTitle('');
          }}
        >
          Create
        </button>
      </div>
      <div className="content-card list-card">
        {items.map((item) => {
          const active = item.conversation_id === conversations?.active_conversation_id;
          return (
            <article className={`list-row conversation-row ${active ? 'active' : ''}`} key={item.conversation_id}>
              <div className="row-main">
                <div className="row-title">
                  <strong>{item.title || item.conversation_id}</strong>
                  {active && <span className="pill">active</span>}
                </div>
                <span>{item.conversation_id}</span>
              </div>
              <div className="row-actions">
                {!active && (
                  <button type="button" className="link-button" disabled={busy} onClick={() => void mutate('conversations_switch', { conversation_id: item.conversation_id })}>
                    Use
                  </button>
                )}
                <button type="button" className="link-button danger" disabled={busy} onClick={() => void mutate('conversations_delete', { conversation_id: item.conversation_id })}>
                  Delete
                </button>
              </div>
            </article>
          );
        })}
        {items.length === 0 && <div className="empty-state">No connected chats.</div>}
      </div>
    </section>
  );
}
