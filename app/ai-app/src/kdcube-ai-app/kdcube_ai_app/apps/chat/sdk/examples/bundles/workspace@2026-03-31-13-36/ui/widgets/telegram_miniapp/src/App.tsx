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

// Which telegram_miniapp_data slice a tab needs. The connections tab renders
// its own page and needs no data fetch.
function dataKeyFor(t: TabId): string | null {
  if (t === 'conversations') return 'chats';
  if (t === 'memory') return 'memory';
  return null;
}

export default function App() {
  const [tab, setTab] = useState<TabId>(activeTabFromPath(ROUTE_CONTEXT.widgetPath));
  const [payloadByTab, setPayloadByTab] = useState<Record<string, WebAppPayload>>({});
  const [profile, setProfile] = useState<TelegramProfile | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const connectionLinkedRef = useRef<boolean | null>(null);
  const profileLoadedRef = useRef(false);
  const didMountRef = useRef(false);

  const payload: WebAppPayload = payloadByTab[dataKeyFor(tab) ?? ''] ?? {};

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

  // Load the current view.
  // - The Telegram profile is fetched ONCE and reused across tab switches; only
  //   `force` (initial load, connection change, manual reload) re-fetches it.
  // - Per-tab data is cached by slice key: returning to a visited tab shows the
  //   cached data immediately and revalidates in the background (no blank), and
  //   the connections tab triggers no data fetch at all.
  async function load(opts?: { force?: boolean }) {
    const force = opts?.force ?? false;
    setError('');
    try {
      if (isTelegramWebApp()) {
        if (force || !profileLoadedRef.current) {
          setLoading(true);
          const nextProfile = await callOperation<TelegramProfile>('telegram_profile', {});
          profileLoadedRef.current = true;
          setProfile(nextProfile);
          applyRuntimeSettings(nextProfile);
          if (nextProfile.connection?.linked === false || nextProfile.connection?.required === true) {
            if (tab !== 'connections') setTab('connections');
            setLoading(false);
            return;
          }
        }
      } else {
        setProfile(null);
      }
      const key = dataKeyFor(tab);
      if (key === null) {
        setLoading(false);
        return;
      }
      const cached = payloadByTab[key] !== undefined;
      // Only blank the view when there is nothing cached to show; a revisit
      // keeps its cached content visible while it revalidates.
      if (!cached) setLoading(true);
      const data = await callOperation<WebAppPayload>('telegram_miniapp_data', {
        widget_path: key,
        mark_memory_seen: tab === 'memory',
      });
      applyRuntimeSettings(data);
      setPayloadByTab((prev) => ({ ...prev, [key]: data }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (isTelegramWebApp() && !profileLoadedRef.current) {
        setProfile(telegramDeniedProfile());
      }
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    prepareTelegramWebApp();
    void settings.setupParentListener().then(() => load({ force: true }));
  }, []);

  useEffect(() => {
    setBrowserTabPath(tab);
    // The initial load is driven by the mount effect above; skip the first run
    // here so a fresh mount does not fetch twice.
    if (!didMountRef.current) {
      didMountRef.current = true;
      return;
    }
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
      void load({ force: true });
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
      {!loading && !pendingTelegramApproval && !connectionRequired && tab === 'memory' && <MemoryPage memory={payload.memory} reload={() => load({ force: true })} />}
      {!loading && !pendingTelegramApproval && !connectionRequired && tab === 'conversations' && <ConversationsPage conversations={payload.conversations} reload={() => load({ force: true })} />}
      {!loading && !pendingTelegramApproval && !connectionRequired && tab === 'connections' && <ConnectionsPage />}
    </AppShell>
  );
}
