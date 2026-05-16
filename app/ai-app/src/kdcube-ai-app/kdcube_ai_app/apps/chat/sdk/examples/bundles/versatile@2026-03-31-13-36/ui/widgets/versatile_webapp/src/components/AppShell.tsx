import type { ReactNode } from 'react';
import { isTelegramWebApp } from '../telegram/utils';
import type { TabId } from '../store/types';

interface AppShellProps {
  activeTab: TabId;
  showAdmin: boolean;
  hideTabs?: boolean;
  loading: boolean;
  error: string;
  onTabChange: (tab: TabId) => void;
  children: ReactNode;
}

export function AppShell({ activeTab, showAdmin, hideTabs = false, loading, error, onTabChange, children }: AppShellProps) {
  return (
    <main className="app-shell">
      <header className="app-nav">
        <div className="app-mark">
          <span className="app-name">Versatile</span>
          <span className="app-context">{isTelegramWebApp() ? 'Telegram WebApp' : 'KDCube widget'}</span>
        </div>
        {!hideTabs && (
          <nav className="page-tabs" aria-label="Views">
            <button type="button" className={activeTab === 'memory' ? 'active' : ''} onClick={() => onTabChange('memory')}>Memory</button>
            <button type="button" className={activeTab === 'conversations' ? 'active' : ''} onClick={() => onTabChange('conversations')}>Chats</button>
            {showAdmin && (
              <button type="button" className={activeTab === 'telegram_admin' ? 'active' : ''} onClick={() => onTabChange('telegram_admin')}>Admin</button>
            )}
          </nav>
        )}
      </header>
      {error && <div className="notice error app-error">{error}</div>}
      {loading ? <div className="loading-state">Loading</div> : children}
    </main>
  );
}
