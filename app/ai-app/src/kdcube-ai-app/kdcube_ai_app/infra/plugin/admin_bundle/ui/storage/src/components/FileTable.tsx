import { useAppDispatch, useAppSelector } from '../app/hooks';
import { loadList, setPath, togglePath } from '../features/storage/storageSlice';
import { formatBytes, formatDate, topSegment } from '../utils/format';
import type { StorageEntry } from '../api/types';

function EntryIcon({ entry }: { entry: StorageEntry }) {
  if (entry.kind === 'directory') return <span className="entry-icon">▣</span>;
  if (entry.kind === 'symlink') return <span className="entry-icon">↗</span>;
  return <span className="entry-icon">□</span>;
}

export function FileTable() {
  const dispatch = useAppDispatch();
  const { entries, selectedPaths, loading, selectedRootId, activeManagedFolders } = useAppSelector((s) => s.storage);

  const openDirectory = (entry: StorageEntry) => {
    if (entry.kind !== 'directory') return;
    dispatch(setPath(entry.path));
    window.setTimeout(() => void dispatch(loadList()), 0);
  };

  if (loading) {
    return <div className="empty-state">Loading storage path...</div>;
  }
  if (!entries.length) {
    return <div className="empty-state">No entries in this path.</div>;
  }

  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            <th className="check-col"></th>
            <th>Name</th>
            <th>Kind</th>
            <th>Size</th>
            <th>Modified</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => {
            const managedFolder = topSegment(entry.path);
            const orphaned = selectedRootId === 'managed_bundles'
              && entry.kind === 'directory'
              && managedFolder
              && !activeManagedFolders.includes(managedFolder);
            return (
              <tr key={entry.path || entry.name} className={orphaned ? 'orphaned-row' : ''}>
                <td className="check-col">
                  <input
                    type="checkbox"
                    disabled={!entry.deletable}
                    checked={selectedPaths.includes(entry.path)}
                    onChange={() => dispatch(togglePath(entry.path))}
                  />
                </td>
                <td>
                  <button
                    className={`entry-name ${entry.kind === 'directory' ? 'clickable' : ''}`}
                    type="button"
                    onClick={() => openDirectory(entry)}
                    disabled={entry.kind !== 'directory'}
                  >
                    <EntryIcon entry={entry} />
                    <span>{entry.name}</span>
                  </button>
                  {entry.symlink_target ? <div className="subtle">→ {entry.symlink_target}</div> : null}
                </td>
                <td>{entry.kind}</td>
                <td>{formatBytes(entry.size_bytes)}</td>
                <td>{formatDate(entry.modified_at)}</td>
                <td>{orphaned ? <span className="badge warning">orphaned</span> : <span className="badge">active/unknown</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
