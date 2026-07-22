/** Left pane: the searchable list of apps. Selecting one loads its config view. */
import { useMemo, useState } from 'react';
import { selectApps, selectSelectedAppId } from '@kdcube/components-core/apps-config';
import { useAppsConfigController, useAppsConfigSelector } from '../binding.tsx';
import { StatusNote } from './primitives/StatusNote.tsx';
import { Badge } from './primitives/Badge.tsx';

export function AppList() {
  const controller = useAppsConfigController();
  const apps = useAppsConfigSelector(selectApps);
  const selectedId = useAppsConfigSelector(selectSelectedAppId);
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return apps.data;
    return apps.data.filter(
      (a) => a.name.toLowerCase().includes(needle) || a.bundleId.toLowerCase().includes(needle),
    );
  }, [apps.data, query]);

  const empty = apps.status === 'ready' && filtered.length === 0;
  const showList = apps.status === 'ready' && filtered.length > 0;

  return (
    <div className="ac-applist">
      <div className="ac-applist__search">
        <input
          className="ac-input"
          placeholder="Search apps…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search apps"
        />
      </div>
      <StatusNote status={apps.status} error={apps.error} empty={empty} emptyLabel="No apps match." />
      {showList && (
        <ul className="ac-applist__items">
          {filtered.map((a) => (
            <li key={a.bundleId}>
              <button
                type="button"
                className={`ac-appitem${a.bundleId === selectedId ? ' is-selected' : ''}`}
                onClick={() => void controller.selectApp(a.bundleId)}
              >
                <span className="ac-appitem__row">
                  <span className="ac-appitem__name">{a.name}</span>
                  {a.isDefault && <Badge tone="accent">default</Badge>}
                  {a.origin && a.origin !== 'unknown' && <Badge tone="muted">{a.origin}</Badge>}
                </span>
                <span className="ac-appitem__id">{a.bundleId}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
