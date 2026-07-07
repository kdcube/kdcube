import { useCallback, useEffect, useRef, useState } from 'react';
import { settings } from './api/settings';
import { useAppDispatch, useAppSelector } from './app/hooks';
import { AppShell, type ConnectionsTab } from './components/AppShell';
import { AuthenticatorsPanel } from './features/authenticators/AuthenticatorsPanel';
import { clearAuthenticatorsError, loadAuthenticators } from './features/authenticators/authenticatorsSlice';
import { DelegatedAccessPanel } from './features/delegatedAccess/DelegatedAccessPanel';
import { clearDelegatedAccessError, loadDelegatedAccess } from './features/delegatedAccess/delegatedAccessSlice';
import { ConnectionEdgesPanel } from './features/identity/ConnectionEdgesPanel';
import { clearIdentityError, loadConnectionEdges } from './features/identity/identitySlice';
import { TelegramClaimPage } from './features/identity/TelegramClaimPage';
import { TelegramMiniAppLinkPanel } from './features/identity/TelegramMiniAppLinkPanel';
import { DelegatedToKdcubePanel } from './features/delegatedToKdcube/DelegatedToKdcubePanel';
import { clearDelegatedToKdcubeError, loadDelegatedToKdcube } from './features/delegatedToKdcube/delegatedToKdcubeSlice';
import { ProviderConnectionsPanel, type ProviderSummon } from './features/providerConnections/ProviderConnectionsPanel';
import { clearProviderConnectionsError, loadProviderConnections } from './features/providerConnections/providerConnectionsSlice';
import { ackConnectionsHubOpen, parseConnectionsHubOpen } from './api/surfaceCommand';

function claimChallengeFromLocation(): string {
  const params = new URLSearchParams(window.location.search);
  return params.get('claim_challenge') || params.get('claimChallenge') || '';
}

function widgetModeFromLocation(): string {
  const params = new URLSearchParams(window.location.search);
  return (params.get('mode') || params.get('surface') || '').toLowerCase();
}

type TelegramConnectStatus = 'idle' | 'connecting' | 'connected' | 'failed';

// One tab-token normalizer for both entry paths: the URL (?tab=…/#…) and the
// scene's `connections.hub.open` surface command.
function tabFromValue(raw: string): ConnectionsTab | null {
  const value = String(raw || '').trim().toLowerCase();
  if (value === 'authenticators' || value === 'identity') return value;
  if (
    value === 'accounts'
    || value === 'delegatedintegrations'
    || value === 'delegated-integrations'
    || value === 'delegated_integrations'
    || value === 'delegatedtokdcube'
    || value === 'delegated-to-kdcube'
    || value === 'delegated_to_kdcube'
  ) return 'delegatedToKdcube';
  if (
    value === 'providerconnections'
    || value === 'provider-connections'
    || value === 'provider_connections'
    || value === 'providers'
  ) return 'providerConnections';
  if (value === 'delegatedaccess' || value === 'delegated-access' || value === 'delegated_access') return 'delegatedAccess';
  if (value === 'delegatedbykdcube' || value === 'delegated-by-kdcube' || value === 'delegated_by_kdcube') return 'delegatedAccess';
  return null;
}

function tabFromLocation(): ConnectionsTab {
  const params = new URLSearchParams(window.location.search);
  const value = params.get('tab') || window.location.hash.replace(/^#/, '') || '';
  return tabFromValue(value) ?? 'identity';
}

export default function App() {
  const dispatch = useAppDispatch();
  const [claimChallengeId] = useState(claimChallengeFromLocation);
  const [widgetMode] = useState(widgetModeFromLocation);
  const telegramMiniAppMode = widgetMode === 'telegram-miniapp' || widgetMode === 'telegram_miniapp';
  const [runtimeReady, setRuntimeReady] = useState(false);
  const [activeTab, setActiveTab] = useState<ConnectionsTab>(tabFromLocation);
  const [hubSummon, setHubSummon] = useState<ProviderSummon | null>(null);
  const [telegramConnectStatus] = useState<TelegramConnectStatus>('idle');
  const authenticatorsLoading = useAppSelector((s) => s.authenticators.loading);
  const authenticatorsAllowed = useAppSelector((s) => s.authenticators.allowed);
  const delegatedAccessLoading = useAppSelector((s) => s.delegatedAccess.loading);
  const identityLoading = useAppSelector((s) => s.identity.loading);
  const delegatedToKdcubeLoading = useAppSelector((s) => s.delegatedToKdcube.loading);
  const providerConnectionsLoading = useAppSelector((s) => s.providerConnections.loading);
  const authenticatorsError = useAppSelector((s) => s.authenticators.error);
  const delegatedAccessError = useAppSelector((s) => s.delegatedAccess.error);
  const identityError = useAppSelector((s) => s.identity.error);
  const delegatedToKdcubeError = useAppSelector((s) => s.delegatedToKdcube.error);
  const providerConnectionsError = useAppSelector((s) => s.providerConnections.error);

  useEffect(() => {
    void settings.setupParentListener().then(async () => {
      setRuntimeReady(true);
      if (telegramMiniAppMode || claimChallengeId) return;
      void dispatch(loadConnectionEdges());
      void dispatch(loadAuthenticators());
      void dispatch(loadDelegatedAccess());
      void dispatch(loadDelegatedToKdcube());
      void dispatch(loadProviderConnections());
    });
  }, [claimChallengeId, dispatch, telegramMiniAppMode]);

  const [refreshing, setRefreshing] = useState(false);
  const loading = identityLoading || authenticatorsLoading || delegatedAccessLoading || delegatedToKdcubeLoading || providerConnectionsLoading;
  const errors = [identityError, authenticatorsError, delegatedAccessError, delegatedToKdcubeError, providerConnectionsError].filter(Boolean) as string[];

  const dismissErrors = () => {
    dispatch(clearIdentityError());
    dispatch(clearAuthenticatorsError());
    dispatch(clearDelegatedAccessError());
    dispatch(clearDelegatedToKdcubeError());
    dispatch(clearProviderConnectionsError());
  };

  // Re-fetch after finishing OAuth in another tab. Doesn't blank the page.
  const refreshInFlight = useRef(false);
  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    setRefreshing(true);
    try {
      await Promise.all([
        dispatch(loadConnectionEdges()).unwrap().catch(() => undefined),
        dispatch(loadAuthenticators()).unwrap().catch(() => undefined),
        dispatch(loadDelegatedAccess()).unwrap().catch(() => undefined),
        dispatch(loadDelegatedToKdcube()).unwrap().catch(() => undefined),
        dispatch(loadProviderConnections()).unwrap().catch(() => undefined),
      ]);
    } finally {
      refreshInFlight.current = false;
      setRefreshing(false);
    }
  }, [dispatch]);

  // The OAuth approval happens in another tab. Primary completion signal:
  // the callback page pushes a same-origin BroadcastChannel message the
  // moment it loads (instant, even while this tab is backgrounded).
  // Fallback: a ONE-SHOT focus refresh, armed only while an approval is in
  // flight (sessionStorage flag set when the approval tab is opened) — no
  // standing focus/visibility polling.
  useEffect(() => {
    if (telegramMiniAppMode || claimChallengeId || !runtimeReady) return;
    const consumePending = () => {
      if (sessionStorage.getItem('kdc-oauth-pending') !== '1') return false;
      sessionStorage.removeItem('kdc-oauth-pending');
      return true;
    };
    const onReturn = () => {
      if (document.visibilityState === 'visible' && consumePending()) void refresh();
    };
    window.addEventListener('focus', onReturn);
    document.addEventListener('visibilitychange', onReturn);
    let channel: BroadcastChannel | null = null;
    try {
      channel = new BroadcastChannel('kdcube-connection-hub');
      channel.onmessage = (event) => {
        const type = String((event.data as { type?: string } | null)?.type || '');
        if (type.startsWith('delegated_to_kdcube.') || type.startsWith('provider_connections.')) {
          sessionStorage.removeItem('kdc-oauth-pending');
          void refresh();
        }
      };
    } catch {
      // BroadcastChannel unavailable (very old browser); the armed focus
      // refresh covers it.
    }
    return () => {
      window.removeEventListener('focus', onReturn);
      document.removeEventListener('visibilitychange', onReturn);
      channel?.close();
    };
  }, [telegramMiniAppMode, claimChallengeId, runtimeReady, refresh]);

  // A non-admin can still land on the tab via URL/stale state; send them home.
  useEffect(() => {
    if (!authenticatorsAllowed && activeTab === 'authenticators') {
      setActiveTab('identity');
    }
  }, [authenticatorsAllowed, activeTab]);

  const changeTab = useCallback((next: ConnectionsTab) => {
    setActiveTab(next);
    try {
      const url = new URL(window.location.href);
      url.searchParams.set('tab', next);
      window.history.replaceState({}, '', url.toString());
    } catch {
      // Embedded/test contexts may not allow history mutation.
    }
  }, []);

  // Scene hosts summon this widget with a `connections.hub.open` surface
  // command (?tab / provider / tiers / account_id as ui_event payload) — apply
  // the same state as the URL deep-link path, at runtime, and ack the host.
  useEffect(() => {
    if (telegramMiniAppMode || claimChallengeId) return;
    const onSurfaceCommand = (event: MessageEvent) => {
      const command = parseConnectionsHubOpen(event.data);
      if (!command) return;
      const tab = tabFromValue(command.tab)
        ?? ((command.provider || command.tiers.length || command.accountId) ? 'providerConnections' : null);
      if (tab) changeTab(tab);
      if (command.provider) {
        setHubSummon({
          nonce: Date.now(),
          provider: command.provider,
          tiers: command.tiers,
          accountId: command.accountId,
        });
      }
      ackConnectionsHubOpen(command, 'applied');
    };
    window.addEventListener('message', onSurfaceCommand);
    return () => window.removeEventListener('message', onSurfaceCommand);
  }, [telegramMiniAppMode, claimChallengeId, changeTab]);

  if (telegramMiniAppMode) {
    if (!runtimeReady) {
      return (
        <div className="page">
          <p className="muted">Loading…</p>
        </div>
      );
    }
    return <TelegramMiniAppLinkPanel />;
  }

  if (claimChallengeId) {
    if (!runtimeReady) {
      return (
        <div className="page">
          <p className="muted">Loading…</p>
        </div>
      );
    }
    return <TelegramClaimPage challengeId={claimChallengeId} />;
  }

  if (loading) {
    return (
      <div className="page">
        <h1>Integrations</h1>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  return (
    <AppShell
      errors={errors}
      onDismissError={dismissErrors}
      onRefresh={refresh}
      refreshing={refreshing}
      activeTab={activeTab}
      onTabChange={changeTab}
      telegramConnectStatus={telegramConnectStatus}
      showAuthenticators={authenticatorsAllowed}
    >
      {activeTab === 'identity' ? <ConnectionEdgesPanel telegramConnectStatus={telegramConnectStatus} /> : null}
      {activeTab === 'authenticators' && authenticatorsAllowed ? <AuthenticatorsPanel /> : null}
      {activeTab === 'delegatedAccess' ? <DelegatedAccessPanel /> : null}
      {activeTab === 'delegatedToKdcube' ? <DelegatedToKdcubePanel /> : null}
      {activeTab === 'providerConnections' ? <ProviderConnectionsPanel summon={hubSummon ?? undefined} /> : null}
    </AppShell>
  );
}
