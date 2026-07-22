/**
 * Top-level container for the app-config viewer: a two-pane layout — the app
 * list on the left, the selected app's detail on the right. Assumes it is
 * rendered inside an <AppsConfigProvider> (which loads the app list on mount).
 */
import { selectSelectedAppId } from '@kdcube/components-core/apps-config';
import { useAppsConfigSelector } from '../binding.tsx';
import { AppList } from './AppList.tsx';
import { AppDetail } from './AppDetail.tsx';
import { ErrorBoundary } from './primitives/ErrorBoundary.tsx';

export interface AppConfigPanelProps {
  /** Optional heading shown above the panes. */
  title?: string;
}

export function AppConfigPanel({ title = 'Apps' }: AppConfigPanelProps) {
  // Reset the detail-pane error boundary whenever the selected app changes, so a
  // crash on one app is cleared by picking another — no page reload needed.
  const selectedId = useAppsConfigSelector(selectSelectedAppId);
  return (
    <div className="apps-config">
      {title && (
        <header className="ac-topbar">
          <h1 className="ac-topbar__title">{title}</h1>
        </header>
      )}
      <div className="ac-panes">
        <aside className="ac-pane ac-pane--list">
          <AppList />
        </aside>
        <main className="ac-pane ac-pane--detail">
          <ErrorBoundary resetKey={selectedId} label="Couldn't render this app's config.">
            <AppDetail />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
