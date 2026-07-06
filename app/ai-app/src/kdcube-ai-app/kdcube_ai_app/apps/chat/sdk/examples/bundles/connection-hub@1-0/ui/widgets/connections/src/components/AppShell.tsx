import type { ReactNode } from 'react';

export type ConnectionsTab = 'identity' | 'delegatedToKdcube' | 'delegatedAccess' | 'authenticators';

export interface AppShellProps {
  errors: string[];
  onDismissError: () => void;
  onRefresh: () => void;
  refreshing?: boolean;
  activeTab: ConnectionsTab;
  onTabChange: (tab: ConnectionsTab) => void;
  telegramConnectStatus?: 'idle' | 'connecting' | 'connected' | 'failed';
  // Authenticator configuration is operator surface; the tab renders only
  // for platform admins (the backend enforces it regardless).
  showAuthenticators?: boolean;
  children: ReactNode;
}

// Page chrome shared by every view: title + Refresh, the standing tip notice, and
// any transient error banners surfaced from the slices.
export function AppShell({
  errors,
  onDismissError,
  onRefresh,
  refreshing,
  activeTab,
  onTabChange,
  telegramConnectStatus = 'idle',
  showAuthenticators = true,
  children,
}: AppShellProps) {
  return (
    <div className="page">
      <div className="page-head">
        <h1>Connections</h1>
        <button className="btn btn-ghost" onClick={onRefresh} disabled={refreshing}>
          {refreshing ? 'Refreshing…' : '↻ Refresh'}
        </button>
      </div>
      <p className="note">
        Link identities for login/routing, delegate external accounts to KDCube,
        or issue bounded KDCube credentials to automation. OAuth connects open in
        a new tab.
      </p>
      <nav className="tabs" aria-label="Connection Hub sections">
        <button
          type="button"
          className={`tab ${activeTab === 'identity' ? 'active' : ''}`}
          onClick={() => onTabChange('identity')}
        >
          Identity
        </button>
        <button
          type="button"
          className={`tab ${activeTab === 'delegatedToKdcube' ? 'active' : ''}`}
          onClick={() => onTabChange('delegatedToKdcube')}
        >
          Delegated to KDCube
        </button>
        <button
          type="button"
          className={`tab ${activeTab === 'delegatedAccess' ? 'active' : ''}`}
          onClick={() => onTabChange('delegatedAccess')}
        >
          Delegated by KDCube
        </button>
        {showAuthenticators ? (
          <button
            type="button"
            className={`tab ${activeTab === 'authenticators' ? 'active' : ''}`}
            onClick={() => onTabChange('authenticators')}
          >
            Authenticators
          </button>
        ) : null}
      </nav>
      {telegramConnectStatus === 'connecting' && (
        <div className="notice">Connecting the Telegram account to your signed-in KDCube user…</div>
      )}
      {telegramConnectStatus === 'connected' && (
        <div className="notice success">Telegram account connected. You can close Telegram and continue in KDCube.</div>
      )}
      {telegramConnectStatus === 'failed' && (
        <div className="error" role="alert">
          Telegram connection did not finish. Reopen the Telegram Mini App and start the link again.
        </div>
      )}
      {errors.map((err, i) => (
        <div className="error" key={`${i}-${err}`} role="alert" onClick={onDismissError}>
          {err}
        </div>
      ))}
      {children}
    </div>
  );
}
