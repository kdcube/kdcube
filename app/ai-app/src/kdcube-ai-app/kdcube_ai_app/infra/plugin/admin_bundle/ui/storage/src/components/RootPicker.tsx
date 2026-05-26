import { useAppDispatch, useAppSelector } from '../app/hooks';
import { loadTenants, selectRoot } from '../features/storage/storageSlice';

export function RootPicker() {
  const dispatch = useAppDispatch();
  const roots = useAppSelector((s) => s.storage.roots);
  const selectedRootId = useAppSelector((s) => s.storage.selectedRootId);
  const selected = roots.find((root) => root.id === selectedRootId);

  return (
    <section className="panel roots-panel">
      <div className="panel-title">Storage surface</div>
      <div className="root-grid">
        {roots.map((root) => (
          <button
            key={root.id}
            className={`root-card ${root.id === selectedRootId ? 'selected' : ''}`}
            onClick={() => {
              dispatch(selectRoot(root.id));
              void dispatch(loadTenants(root.id));
            }}
            type="button"
          >
            <span className="root-label">{root.label}</span>
            <span className="root-kind">{root.kind}</span>
            <span className="root-desc">{root.description}</span>
            <span className={`root-state ${root.exists ? 'ok' : 'warn'}`}>{root.exists ? 'available' : 'missing'}</span>
          </button>
        ))}
      </div>
      {selected ? <div className="root-path">{selected.path || selected.uri || 'not configured'}</div> : null}
    </section>
  );
}
