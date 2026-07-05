import type { ReactNode } from 'react';

export type ConnectionsTab = 'identity' | 'accounts' | 'delegatedAccess' | 'authenticators';

export interface AppShellProps {
  errors: string[];
  onDismissError: () => void;
  onRefresh: () => void;
  refreshing?: boolean;
  activeTab: ConnectionsTab;
  onTabChange: (tab: ConnectionsTab) => void;
  telegramConnectStatus?: 'idle' | 'connecting' | 'connected' | 'failed';
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
        Link alternate identities for login/routing, and connect accounts when
        automation needs delegated access. OAuth connects open in a new tab.
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
          className={`tab ${activeTab === 'accounts' ? 'active' : ''}`}
          onClick={() => onTabChange('accounts')}
        >
          Accounts
        </button>
        <button
          type="button"
          className={`tab ${activeTab === 'delegatedAccess' ? 'active' : ''}`}
          onClick={() => onTabChange('delegatedAccess')}
        >
          Delegated Access
        </button>
        <button
          type="button"
          className={`tab ${activeTab === 'authenticators' ? 'active' : ''}`}
          onClick={() => onTabChange('authenticators')}
        >
          Authenticators
        </button>
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
