import { useEffect, useMemo, useRef, useState } from 'react';
import { AppShell } from './components/AppShell';
import { ConnectionsPage } from './pages/ConnectionsPage';
import { ConversationsPage } from './pages/ConversationsPage';
import { MemoryPage } from './pages/MemoryPage';
import { callOperation } from './store/apiClient';
import { TelegramPendingApproval } from '@kdcube/telegram-widget';
import {
  activeTabFromPath,
  ROUTE_CONTEXT,
  setBrowserTabPath,
  settings,
} from './store/settings';
import type { AppSettings, TabId, TelegramProfile, WebAppPayload } from './store/types';
import { isTelegramWebApp, prepareTelegramWebApp } from './telegram/utils';

function telegramDeniedProfile(): TelegramProfile {
  return {
    ok: false,
    telegram: {
      role: 'anonymous',
      allowed: false,
      is_admin: false,
    },
    permissions: {
      can_use_chatbot: false,
      can_use_widget: false,
    },
  };
}

function applyRuntimeSettings(data: Pick<WebAppPayload, 'authContext' | 'connections'> | Pick<TelegramProfile, 'authContext' | 'connections'>): void {
  const settingsUpdate: Partial<AppSettings> = {};
  if (data.authContext?.headers) {
    settingsUpdate.authContextHeaders = Object.fromEntries(
      Object.entries(data.authContext.headers)
        .filter(([name, value]) => name && value !== undefined && value !== null && String(value) !== '')
        .map(([name, value]) => [name, String(value)]),
    );
  }
  if (data.connections?.connection_hub?.bundle_id) {
    settingsUpdate.connectionHubBundleId = data.connections.connection_hub.bundle_id;
  }
  if (Object.keys(settingsUpdate).length > 0) {
    settings.update(settingsUpdate);
  }
}

export default function App() {
  const [tab, setTab] = useState<TabId>(activeTabFromPath(ROUTE_CONTEXT.widgetPath));
  const [payload, setPayload] = useState<WebAppPayload>({});
  const [profile, setProfile] = useState<TelegramProfile | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const connectionLinkedRef = useRef<boolean | null>(null);

  const connectionRequired = useMemo(() => {
    if (!isTelegramWebApp() || !profile) return false;
    return profile.connection?.linked === false || profile.connection?.required === true;
  }, [profile]);

  const pendingTelegramApproval = useMemo(() => {
    if (!isTelegramWebApp() || !profile) return false;
    if (connectionRequired) return false;
    if (profile.connection?.linked === true) return false;
    if (profile.ok === false) return true;
    if (profile.telegram?.allowed === false) return true;
    return String(profile.telegram?.role || '').toLowerCase() === 'anonymous';
  }, [profile, connectionRequired]);
  const telegramGateActive = isTelegramWebApp() && !connectionRequired && (loading || !profile || pendingTelegramApproval);

  useEffect(() => {
    connectionLinkedRef.current = profile?.connection?.linked ?? null;
  }, [profile?.connection?.linked]);

  async function load() {
    setLoading(true);
    setError('');
    try {
      if (isTelegramWebApp()) {
        const nextProfile = await callOperation<TelegramProfile>('telegram_profile', {});
        setProfile(nextProfile);
        applyRuntimeSettings(nextProfile);
        if (nextProfile.connection?.linked === false || nextProfile.connection?.required === true) {
          if (tab !== 'connections') setTab('connections');
          setPayload({});
          return;
        }
      } else {
        setProfile(null);
      }
      const data = await callOperation<WebAppPayload>('telegram_miniapp_data', {
        widget_path: tab === 'conversations' ? 'chats' : 'memory',
        mark_memory_seen: tab === 'memory',
      });
      applyRuntimeSettings(data);
      setPayload(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (isTelegramWebApp()) {
        setProfile(telegramDeniedProfile());
        setPayload({});
      }
      setError(message);
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

  useEffect(() => {
    function onConnectionStatusChanged(event: MessageEvent) {
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      if ((data as Record<string, unknown>).type !== 'kdcube-connection-status-changed') return;
      if ((data as Record<string, unknown>).provider !== 'telegram') return;
      const linked = Boolean((data as Record<string, unknown>).linked);
      if (connectionLinkedRef.current === linked) return;
      connectionLinkedRef.current = linked;
      void load();
    }
    window.addEventListener('message', onConnectionStatusChanged);
    return () => window.removeEventListener('message', onConnectionStatusChanged);
  }, []);

  return (
    <AppShell
      activeTab={tab}
      hideTabs={telegramGateActive}
      connectOnly={connectionRequired}
      loading={loading && !connectionRequired}
      error={pendingTelegramApproval ? '' : error}
      onTabChange={setTab}
    >
      {!loading && pendingTelegramApproval && (
        <TelegramPendingApproval
          title="Access request received"
          message="Please wait for an admin to approve this Telegram user."
          detail="Once approved, reopen this Mini App and it will load normally."
        />
      )}
      {!pendingTelegramApproval && connectionRequired && <ConnectionsPage />}
      {!loading && !pendingTelegramApproval && !connectionRequired && tab === 'memory' && <MemoryPage memory={payload.memory} reload={load} />}
      {!loading && !pendingTelegramApproval && !connectionRequired && tab === 'conversations' && <ConversationsPage conversations={payload.conversations} reload={load} />}
      {!loading && !pendingTelegramApproval && !connectionRequired && tab === 'connections' && <ConnectionsPage />}
    </AppShell>
  );
}
