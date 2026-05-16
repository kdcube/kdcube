import { useEffect, useMemo, useState } from 'react';
import { AppShell } from './components/AppShell';
import { ConversationsPage } from './pages/ConversationsPage';
import { MemoryPage } from './pages/MemoryPage';
import { TelegramAdminPage } from './pages/TelegramAdminPage';
import { callOperation } from './store/apiClient';
import { TelegramPendingApproval } from '@kdcube/telegram-widget';
import {
  activeTabFromPath,
  ROUTE_CONTEXT,
  setBrowserTabPath,
  settings,
} from './store/settings';
import type { TabId, TelegramProfile, WebAppPayload } from './store/types';
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
      show_admin_component: false,
    },
  };
}

export default function App() {
  const [tab, setTab] = useState<TabId>(activeTabFromPath(ROUTE_CONTEXT.widgetPath));
  const [payload, setPayload] = useState<WebAppPayload>({});
  const [profile, setProfile] = useState<TelegramProfile | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const showAdmin = useMemo(() => {
    if (!isTelegramWebApp()) return Boolean(payload.permissions?.show_admin_component);
    return Boolean(profile?.permissions?.show_admin_component || profile?.telegram?.is_admin);
  }, [payload.permissions?.show_admin_component, profile]);

  const pendingTelegramApproval = useMemo(() => {
    if (!isTelegramWebApp() || !profile) return false;
    if (profile.ok === false) return true;
    if (profile.permissions?.can_use_widget === false) return true;
    if (profile.telegram?.allowed === false) return true;
    return String(profile.telegram?.role || '').toLowerCase() === 'anonymous';
  }, [profile]);
  const telegramGateActive = isTelegramWebApp() && (loading || !profile || pendingTelegramApproval);

  async function load() {
    setLoading(true);
    setError('');
    try {
      let nextShowAdminFromProfile = false;
      if (isTelegramWebApp()) {
        const nextProfile = await callOperation<TelegramProfile>('telegram_profile', {});
        setProfile(nextProfile);
        nextShowAdminFromProfile = Boolean(nextProfile.permissions?.show_admin_component || nextProfile.telegram?.is_admin);
        const role = String(nextProfile.telegram?.role || '').toLowerCase();
        const allowed = nextProfile.ok !== false
          && nextProfile.permissions?.can_use_widget !== false
          && nextProfile.telegram?.allowed !== false
          && role !== 'anonymous';
        if (!allowed) {
          setPayload({});
          if (tab === 'telegram_admin') setTab('memory');
          return;
        }
      } else {
        setProfile(null);
      }
      const data = await callOperation<WebAppPayload>('versatile_webapp_data', {
        widget_path: tab === 'conversations' ? 'chats' : tab === 'telegram_admin' ? 'telegram-admin' : 'memory',
        mark_memory_seen: tab === 'memory',
      });
      setPayload(data);
      let nextShowAdmin = Boolean(data.permissions?.show_admin_component);
      if (isTelegramWebApp()) {
        nextShowAdmin = nextShowAdminFromProfile;
      }
      if (tab === 'telegram_admin' && !nextShowAdmin) {
        setTab('memory');
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (isTelegramWebApp()) {
        setProfile(telegramDeniedProfile());
        setPayload({});
        if (tab === 'telegram_admin') setTab('memory');
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

  return (
    <AppShell
      activeTab={tab}
      showAdmin={showAdmin}
      hideTabs={telegramGateActive}
      loading={loading}
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
      {!loading && !pendingTelegramApproval && tab === 'memory' && <MemoryPage memory={payload.memory} reload={load} callOperation={callOperation} />}
      {!loading && !pendingTelegramApproval && tab === 'conversations' && <ConversationsPage conversations={payload.conversations} reload={load} />}
      {!loading && !pendingTelegramApproval && tab === 'telegram_admin' && showAdmin && <TelegramAdminPage />}
    </AppShell>
  );
}
