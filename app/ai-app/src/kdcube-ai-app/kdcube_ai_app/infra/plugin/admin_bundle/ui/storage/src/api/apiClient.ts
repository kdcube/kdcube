import { settings } from './settings';
import type { RegistryBundle, RootInfo, StorageEntry, TenantProjects } from './types';

export function query(params: Record<string, string | number | null | undefined>) {
  const out = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value) !== '') out.set(key, String(value));
  });
  const value = out.toString();
  return value ? `?${value}` : '';
}

export async function apiFetch(path: string, init: RequestInit = {}) {
  const headers = settings.authHeaders(init.headers);
  if (!(init.body instanceof FormData) && init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(`${settings.getBaseUrl()}${path}`, {
    ...init,
    credentials: 'include',
    headers,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      message = payload?.detail?.message || payload?.detail || payload?.message || message;
    } catch {
      // keep status text
    }
    throw new Error(message);
  }
  return response;
}

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiFetch(path, init);
  return response.json() as Promise<T>;
}

export function fetchRoots() {
  return apiJson<{ roots: RootInfo[] }>('/api/admin/control-plane/storage/roots');
}

export function fetchTenants(rootId: string) {
  return apiJson<{ tenants: TenantProjects[] }>(`/api/admin/control-plane/storage/tenants-projects${query({ root_id: rootId })}`);
}

export function fetchRegistry(tenant: string, project: string) {
  return apiJson<{ bundles: RegistryBundle[]; active_managed_folders: string[] }>(
    `/admin/integrations/bundles/storage-registry${query({ tenant, project })}`,
  );
}

export function fetchList(params: {
  rootId: string;
  tenant: string;
  project: string;
  path: string;
}) {
  return apiJson<{ entries: StorageEntry[]; current: StorageEntry }>(
    `/api/admin/control-plane/storage/list${query({
      root_id: params.rootId,
      tenant: params.tenant,
      project: params.project,
      path: params.path,
      limit: 500,
    })}`,
  );
}

export function deletePaths(payload: {
  rootId: string;
  tenant: string;
  project: string;
  paths: string[];
}) {
  return apiJson<{ deleted_count: number }>('/api/admin/control-plane/storage/delete', {
    method: 'POST',
    body: JSON.stringify({
      root_id: payload.rootId,
      tenant: payload.tenant,
      project: payload.project,
      paths: payload.paths,
      confirm: true,
    }),
  });
}

export async function exportPaths(payload: {
  rootId: string;
  tenant: string;
  project: string;
  paths: string[];
}) {
  const response = await apiFetch('/api/admin/control-plane/storage/export', {
    method: 'POST',
    body: JSON.stringify({
      root_id: payload.rootId,
      tenant: payload.tenant,
      project: payload.project,
      paths: payload.paths,
    }),
  });
  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const filenameMatch = disposition.match(/filename="([^"]+)"/);
  const filename = filenameMatch?.[1] || `storage-export-${Date.now()}.zip`;
  const href = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = href;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(href);
}
