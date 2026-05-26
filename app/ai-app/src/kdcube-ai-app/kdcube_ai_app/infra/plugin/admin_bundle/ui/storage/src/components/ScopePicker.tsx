import { useAppDispatch, useAppSelector } from '../app/hooks';
import { loadList, loadRegistry, selectProject, selectTenant } from '../features/storage/storageSlice';

export function ScopePicker() {
  const dispatch = useAppDispatch();
  const { roots, selectedRootId, tenant, project, tenants } = useAppSelector((s) => s.storage);
  const root = roots.find((item) => item.id === selectedRootId);
  const tenantEntry = tenants.find((item) => item.tenant === tenant);
  const scopeNeeded = root?.tenant_project_mode !== 'none';
  const tenantOptions = tenants.some((item) => item.tenant === tenant) || !tenant
    ? tenants
    : [{ tenant, projects: project ? [project] : [] }, ...tenants];
  const projectOptions = tenantEntry?.projects?.length
    ? tenantEntry.projects
    : project
      ? [project]
      : [];

  if (!scopeNeeded) {
    return (
      <section className="panel scope-panel">
        <div className="panel-title">Scope</div>
        <div className="muted">Global storage root</div>
      </section>
    );
  }

  return (
    <section className="panel scope-panel">
      <div className="panel-title">Scope</div>
      <div className="field-row">
        <label>
          Tenant
          <select
            value={tenant}
            onChange={(event) => {
              dispatch(selectTenant(event.target.value));
              window.setTimeout(() => {
                void dispatch(loadRegistry());
                void dispatch(loadList());
              }, 0);
            }}
          >
            <option value="">Select tenant</option>
            {tenantOptions.map((item) => (
              <option key={item.tenant} value={item.tenant}>{item.tenant}</option>
            ))}
          </select>
        </label>
        <label>
          Project
          <select
            value={project}
            onChange={(event) => {
              dispatch(selectProject(event.target.value));
              window.setTimeout(() => {
                void dispatch(loadRegistry());
                void dispatch(loadList());
              }, 0);
            }}
          >
            <option value="">Select project</option>
            {projectOptions.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </label>
      </div>
    </section>
  );
}
