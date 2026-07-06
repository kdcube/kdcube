import type { ReactNode } from 'react';
import { isTelegramWebApp } from '../telegram/utils';
import type { TabId } from '../store/types';

interface AppShellProps {
  activeTab: TabId;
  hideTabs?: boolean;
  connectOnly?: boolean;
  loading: boolean;
  error: string;
  onTabChange: (tab: TabId) => void;
  children: ReactNode;
}

export function AppShell({ activeTab, hideTabs = false, connectOnly = false, loading, error, onTabChange, children }: AppShellProps) {
  return (
    <main className="app-shell">
      <header className="app-nav">
        <div className="app-mark">
          <span className="app-name">KDCube Companion</span>
          <span className="app-context">{isTelegramWebApp() ? 'Telegram' : 'Widget'}</span>
        </div>
        {!hideTabs && (
          <nav className="page-tabs" aria-label="Views">
            {!connectOnly && (
              <>
                <button type="button" className={activeTab === 'memory' ? 'active' : ''} onClick={() => onTabChange('memory')}>Memory</button>
                <button type="button" className={activeTab === 'conversations' ? 'active' : ''} onClick={() => onTabChange('conversations')}>Chats</button>
              </>
            )}
            {isTelegramWebApp() && (
              <button type="button" className={activeTab === 'connections' ? 'active' : ''} onClick={() => onTabChange('connections')}>Connect</button>
            )}
          </nav>
        )}
      </header>
      {error && <div className="notice error app-error">{error}</div>}
      {loading ? <div className="loading-state">Loading</div> : children}
    </main>
  );
}
