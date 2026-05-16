import { useEffect, useState } from 'react';

export interface TelegramUser {
  telegram_user_id: string;
  telegram_chat_id?: string;
  telegram_username?: string;
  kdcube_user_id?: string;
  role?: string;
  conversation_id?: string;
  notes?: string;
}

export interface TelegramAdminPayload {
  ok?: boolean;
  roles?: string[];
  users?: TelegramUser[];
  error?: string;
  current_kdcube_user_id?: string;
  current_user?: {
    user_id?: string;
    username?: string;
    roles?: string[];
  };
}

export interface ConversationItem {
  conversation_id: string;
  title?: string;
  source?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ConversationsPayload {
  active_conversation_id?: string;
  items?: ConversationItem[];
  conversations?: ConversationItem[];
  count?: number;
  telegram_user_id?: string;
  kdcube_user_id?: string;
  error?: { code?: string; message?: string };
}

export type TelegramWidgetCallOperation = <T>(
  operation: string,
  payload?: Record<string, unknown>,
) => Promise<T>;

export interface TelegramAdminPanelProps {
  callOperation: TelegramWidgetCallOperation;
  dataOperation?: string;
  upsertOperation?: string;
  deleteOperation?: string;
  title?: string;
}

export interface TelegramConversationsPanelProps {
  conversations?: ConversationsPayload;
  reload: () => Promise<void>;
  callOperation: TelegramWidgetCallOperation;
  createOperation?: string;
  switchOperation?: string;
  deleteOperation?: string;
  title?: string;
}

export interface TelegramPendingApprovalProps {
  title?: string;
  message?: string;
  detail?: string;
}

function fmt(value?: string): string {
  const text = String(value || '').trim();
  return text || '-';
}

function assertOk(result: unknown, fallback: string): void {
  if (!result || typeof result !== 'object') return;
  const object = result as Record<string, unknown>;
  if (object.ok !== false) return;
  const error = object.error && typeof object.error === 'object'
    ? object.error as Record<string, unknown>
    : {};
  throw new Error(String(error.message || object.error || fallback));
}

function conversationItems(conversations?: ConversationsPayload): ConversationItem[] {
  return conversations?.items || conversations?.conversations || [];
}

export function TelegramPendingApproval({
  title = 'Nice to hear from you.',
  message = 'You will be able to enter after an admin approves your Telegram access.',
  detail = 'You can close this window for now. Once approved, this app will open normally.',
}: TelegramPendingApprovalProps) {
  return (
    <section className="telegram-pending-card" aria-live="polite">
      <span className="telegram-pending-badge">Telegram access pending</span>
      <h1>{title}</h1>
      <p>{message}</p>
      <p className="telegram-pending-detail">{detail}</p>
    </section>
  );
}

export function TelegramAdminPanel({
  callOperation,
  dataOperation = 'telegram_user_admin_data',
  upsertOperation = 'telegram_user_admin_upsert',
  deleteOperation = 'telegram_user_admin_delete',
  title = 'Telegram Admin',
}: TelegramAdminPanelProps) {
  const [payload, setPayload] = useState<TelegramAdminPayload>({});
  const [selected, setSelected] = useState<TelegramUser | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function load() {
    setBusy(true);
    setError('');
    try {
      const data = await callOperation<TelegramAdminPayload>(dataOperation, {});
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
      const data = await callOperation<TelegramAdminPayload>(
        upsertOperation,
        selected as unknown as Record<string, unknown>,
      );
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
      const data = await callOperation<TelegramAdminPayload>(deleteOperation, { telegram_user_id: telegramUserId });
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
  const currentKdcubeUserId = fmt(payload.current_kdcube_user_id || payload.current_user?.user_id);

  return (
    <section className="page page-wide">
      <div className="page-header">
        <div>
          <h1>{title}</h1>
          <p>{users.length} mapped users · current KDCube user {currentKdcubeUserId}</p>
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
            placeholder={currentKdcubeUserId !== '-' ? `KDCube user id, current: ${currentKdcubeUserId}` : 'KDCube user id'}
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
            {currentKdcubeUserId !== '-' && (
              <button
                type="button"
                className="ghost-button"
                onClick={() => setSelected({ ...draft, kdcube_user_id: currentKdcubeUserId })}
              >
                Use current KDCube user
              </button>
            )}
            <button type="submit" className="primary-button" disabled={busy || !draft.telegram_user_id}>Save</button>
          </div>
        </form>
      </div>
    </section>
  );
}

export function TelegramConversationsPanel({
  conversations,
  reload,
  callOperation,
  createOperation = 'conversations_create',
  switchOperation = 'conversations_switch',
  deleteOperation = 'conversations_delete',
  title: panelTitle = 'Chats',
}: TelegramConversationsPanelProps) {
  const [title, setTitle] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const items = conversationItems(conversations);
  const activeId = String(conversations?.active_conversation_id || '');

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
          <h1>{panelTitle}</h1>
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
            void mutate(createOperation, { title });
            setTitle('');
          }}
        >
          Create
        </button>
      </div>
      <div className="content-card list-card">
        {items.map((item) => {
          const active = item.conversation_id === activeId;
          return (
            <article className={`list-row conversation-row ${active ? 'active' : ''}`} key={item.conversation_id}>
              <div className="row-main">
                <div className="row-title">
                  <strong>{item.title || item.conversation_id}</strong>
                  {active && <span className="pill neutral">active</span>}
                </div>
                <span>{item.conversation_id}</span>
              </div>
              <div className="row-actions">
                {!active && (
                  <button type="button" className="link-button" disabled={busy} onClick={() => void mutate(switchOperation, { conversation_id: item.conversation_id })}>
                    Use
                  </button>
                )}
                <button type="button" className="link-button danger" disabled={busy} onClick={() => void mutate(deleteOperation, { conversation_id: item.conversation_id })}>
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
