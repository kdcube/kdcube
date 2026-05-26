import { useEffect } from 'react';
import { useAppDispatch, useAppSelector } from '../../app/hooks';
import { Breadcrumbs } from '../../components/Breadcrumbs';
import { FileTable } from '../../components/FileTable';
import { RegistryPanel } from '../../components/RegistryPanel';
import { RootPicker } from '../../components/RootPicker';
import { ScopePicker } from '../../components/ScopePicker';
import { StatusBanner } from '../../components/StatusBanner';
import { StorageActions } from '../../components/StorageActions';
import { loadList, loadRegistry, loadRoots, loadTenants } from './storageSlice';

export function StorageDashboard() {
  const dispatch = useAppDispatch();
  const { ready, selectedRootId, tenant, project, roots } = useAppSelector((s) => s.storage);
  const root = roots.find((item) => item.id === selectedRootId);

  useEffect(() => {
    if (!ready) return;
    void dispatch(loadRoots()).then((result) => {
      const rootsPayload = 'payload' in result ? result.payload as { roots?: { id: string }[] } : null;
      const rootId = rootsPayload?.roots?.[0]?.id || selectedRootId;
      void dispatch(loadTenants(rootId));
    });
  }, [dispatch, ready, selectedRootId]);

  useEffect(() => {
    if (!ready || !selectedRootId) return;
    void dispatch(loadTenants(selectedRootId));
  }, [dispatch, ready, selectedRootId]);

  useEffect(() => {
    if (!ready || !root) return;
    if (root.tenant_project_mode === 'required' && (!tenant || !project)) return;
    void dispatch(loadRegistry());
    void dispatch(loadList());
  }, [dispatch, ready, root, selectedRootId, tenant, project]);

  return (
    <div className="storage-layout">
      <header className="page-header">
        <div>
          <div className="eyebrow">Operational storage</div>
          <h1>Bundle storage</h1>
        </div>
        <StorageActions />
      </header>
      <StatusBanner />
      <div className="content-grid">
        <aside className="sidebar">
          <RootPicker />
          <ScopePicker />
          <RegistryPanel />
        </aside>
        <main className="browser-panel panel">
          <div className="browser-header">
            <div>
              <div className="panel-title">File browser</div>
              <div className="muted">Browse, export, and delete selected storage entries.</div>
            </div>
            <Breadcrumbs />
          </div>
          <FileTable />
        </main>
      </div>
    </div>
  );
}
