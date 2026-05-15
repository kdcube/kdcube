import { useEffect, useMemo, useState } from 'react';
import { MemoryPage } from './pages/MemoryPage';
import { TelegramAdminPage } from './pages/TelegramAdminPage';
import { callOperation } from './store/apiClient';
import {
  activeTabFromPath,
  ROUTE_CONTEXT,
  setBrowserTabPath,
  settings,
} from './store/settings';
import type { TabId, TelegramProfile, WebAppPayload } from './store/types';
import { isTelegramWebApp, prepareTelegramWebApp } from './telegram/utils';

export default function App() {
  const [tab, setTab] = useState<TabId>(activeTabFromPath(ROUTE_CONTEXT.widgetPath));
  const [payload, setPayload] = useState<WebAppPayload>({});
  const [profile, setProfile] = useState<TelegramProfile | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const showAdmin = useMemo(() => {
    if (!isTelegramWebApp()) return true;
    return Boolean(profile?.permissions?.show_admin_component || profile?.telegram?.is_admin);
  }, [profile]);

  async function load() {
    setLoading(true);
    setError('');
    try {
      if (!isTelegramWebApp()) {
        setPayload({});
        setProfile(null);
        return;
      }
      const data = await callOperation<WebAppPayload>('telegram_copilot_webapp_data', {
        widget_path: tab === 'telegram_admin' ? 'telegram-admin' : 'memory',
        mark_memory_seen: tab === 'memory',
      });
      setPayload(data);
      const nextProfile = await callOperation<TelegramProfile>('telegram_profile', {});
      setProfile(nextProfile);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    prepareTelegramWebApp();
    void settings.setupParentListener().then(() => load());
  }, []);

  useEffect(() => {
    setBrowserTabPath(tab);
    void load();
  }, [tab]);

  return (
    <main className="app-shell">
      <header className="app-nav">
        <div className="app-mark">
          <span className="app-name">KDCube Copilot</span>
          <span className="app-context">Telegram WebApp</span>
        </div>
        <nav className="page-tabs" aria-label="Copilot Telegram sections">
          <button
            type="button"
            className={tab === 'memory' ? 'active' : ''}
            onClick={() => setTab('memory')}
          >
            Memory
          </button>
          {showAdmin && (
            <button
              type="button"
              className={tab === 'telegram_admin' ? 'active' : ''}
              onClick={() => setTab('telegram_admin')}
            >
              Telegram Admin
            </button>
          )}
        </nav>
      </header>
      {loading && <div className="status-line">Loading...</div>}
      {error && <div className="notice error shell-notice">{error}</div>}
      {tab === 'memory' && <MemoryPage memory={payload.memory} reload={load} />}
      {tab === 'telegram_admin' && showAdmin && <TelegramAdminPage />}
    </main>
  );
}
