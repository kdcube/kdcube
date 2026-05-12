import { useEffect, useMemo, useState } from 'react';
import { AppShell } from './components/AppShell';
import { ConversationsPage } from './pages/ConversationsPage';
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
      const data = await callOperation<WebAppPayload>('versatile_webapp_data', {
        widget_path: tab === 'conversations' ? 'chats' : tab === 'telegram_admin' ? 'telegram-admin' : 'memory',
        mark_memory_seen: tab === 'memory',
      });
      setPayload(data);
      if (isTelegramWebApp()) {
        const nextProfile = await callOperation<TelegramProfile>('telegram_profile', {});
        setProfile(nextProfile);
      }
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
    <AppShell
      activeTab={tab}
      showAdmin={showAdmin}
      loading={loading}
      error={error}
      onTabChange={setTab}
    >
      {tab === 'memory' && <MemoryPage memory={payload.memory} reload={load} />}
      {tab === 'conversations' && <ConversationsPage conversations={payload.conversations} reload={load} />}
      {tab === 'telegram_admin' && showAdmin && <TelegramAdminPage />}
    </AppShell>
  );
}
