import { useEffect, useState } from 'react';
import { assertOk, callOperation } from '../store/apiClient';
import type { TelegramAdminPayload, TelegramUser } from '../store/types';

function fmt(value?: string): string {
  const text = String(value || '').trim();
  return text || '-';
}

export function TelegramAdminPage() {
  const [payload, setPayload] = useState<TelegramAdminPayload>({});
  const [selected, setSelected] = useState<TelegramUser | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function load() {
    setBusy(true);
    setError('');
    try {
      const data = await callOperation<TelegramAdminPayload>('telegram_user_admin_data', {});
      assertOk(data, 'Load failed');
      setPayload(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function save() {
    if (!selected?.telegram_user_id) return;
    setBusy(true);
    setError('');
    try {
      const data = await callOperation<TelegramAdminPayload>('telegram_user_admin_upsert', selected as unknown as Record<string, unknown>);
      assertOk(data, 'Save failed');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(telegramUserId: string) {
    setBusy(true);
    setError('');
    try {
      const data = await callOperation<TelegramAdminPayload>('telegram_user_admin_delete', { telegram_user_id: telegramUserId });
      assertOk(data, 'Delete failed');
      if (selected?.telegram_user_id === telegramUserId) setSelected(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const users = payload.users || [];
  const roles = payload.roles || ['anonymous', 'registered', 'admin'];
  const draft = selected || {
    telegram_user_id: '',
    telegram_chat_id: '',
    telegram_username: '',
    kdcube_user_id: '',
    role: 'anonymous',
    conversation_id: '',
    notes: '',
  };

  return (
    <section className="page page-wide">
      <div className="page-header">
        <div>
          <h1>Telegram Admin</h1>
          <p>{users.length} mapped users</p>
        </div>
        <button type="button" className="ghost-button" onClick={load} disabled={busy}>Refresh</button>
      </div>
      {error && <div className="notice error">{error}</div>}
      <div className="admin-layout">
        <div className="content-card list-card">
          {users.map((user) => (
            <article className="list-row admin-row" key={user.telegram_user_id}>
              <div className="row-main">
                <div className="row-title">
                  <strong>{user.telegram_username || user.telegram_user_id}</strong>
                  <span className="pill neutral">{fmt(user.role)}</span>
                </div>
                <span>{fmt(user.kdcube_user_id)} · chat {fmt(user.telegram_chat_id)} · conversation {fmt(user.conversation_id)}</span>
              </div>
              <div className="row-actions">
                <button type="button" className="link-button" disabled={busy} onClick={() => setSelected(user)}>Edit</button>
                <button type="button" className="link-button danger" disabled={busy} onClick={() => void remove(user.telegram_user_id)}>Delete</button>
              </div>
            </article>
          ))}
          {users.length === 0 && <div className="empty-state">No Telegram users.</div>}
        </div>
        <form
          className="content-card edit-form"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <input
            value={draft.telegram_user_id}
            placeholder="Telegram user id"
            onChange={(event) => setSelected({ ...draft, telegram_user_id: event.target.value })}
          />
          <input
            value={draft.telegram_chat_id || ''}
            placeholder="Telegram chat id"
            onChange={(event) => setSelected({ ...draft, telegram_chat_id: event.target.value })}
          />
          <input
            value={draft.telegram_username || ''}
            placeholder="Telegram username"
            onChange={(event) => setSelected({ ...draft, telegram_username: event.target.value })}
          />
          <input
            value={draft.kdcube_user_id || ''}
            placeholder="KDCube user id"
            onChange={(event) => setSelected({ ...draft, kdcube_user_id: event.target.value })}
          />
          <select value={draft.role || 'anonymous'} onChange={(event) => setSelected({ ...draft, role: event.target.value })}>
            {roles.map((role) => <option key={role} value={role}>{role}</option>)}
          </select>
          <input
            value={draft.conversation_id || ''}
            placeholder="Conversation id"
            onChange={(event) => setSelected({ ...draft, conversation_id: event.target.value })}
          />
          <textarea
            rows={4}
            value={draft.notes || ''}
            placeholder="Admin notes"
            onChange={(event) => setSelected({ ...draft, notes: event.target.value })}
          />
          <div className="actions">
            <button type="button" className="ghost-button" onClick={() => setSelected(null)}>Clear</button>
            <button type="submit" className="primary-button" disabled={busy || !draft.telegram_user_id}>Save</button>
          </div>
        </form>
      </div>
    </section>
  );
}
