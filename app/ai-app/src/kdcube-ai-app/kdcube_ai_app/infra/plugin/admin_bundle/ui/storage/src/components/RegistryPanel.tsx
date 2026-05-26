import { useAppSelector } from '../app/hooks';

export function RegistryPanel() {
  const bundles = useAppSelector((s) => s.storage.registryBundles);
  const activeManagedFolders = useAppSelector((s) => s.storage.activeManagedFolders);

  return (
    <section className="panel registry-panel">
      <div className="panel-title">Active registry</div>
      <div className="registry-summary">
        <span>{bundles.length} bundle(s)</span>
        <span>{activeManagedFolders.length} managed folder(s)</span>
      </div>
      <div className="registry-list">
        {bundles.slice(0, 80).map((bundle) => (
          <div className="registry-item" key={bundle.id}>
            <div>
              <strong>{bundle.id}</strong>
              {bundle.default ? <span className="badge">default</span> : null}
            </div>
            <div className="subtle">{bundle.repo || bundle.path || 'local/built-in'}</div>
            {bundle.ref ? <div className="subtle">ref: {bundle.ref}</div> : null}
            {bundle.managed_folder ? <div className="subtle">managed: {bundle.managed_folder}</div> : null}
          </div>
        ))}
      </div>
    </section>
  );
}
