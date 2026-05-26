export type RootInfo = {
  id: string;
  label: string;
  description: string;
  kind: string;
  uri: string;
  path: string | null;
  exists: boolean;
  writable: boolean;
  tenant_project_mode: 'required' | 'optional' | 'none';
  capabilities: string[];
};

export type StorageEntry = {
  name: string;
  path: string;
  kind: 'directory' | 'file' | 'symlink' | 'other';
  size_bytes: number | null;
  modified_at: number | null;
  child_count: number | null;
  deletable: boolean;
  exportable: boolean;
  symlink_target?: string | null;
};

export type RegistryBundle = {
  id: string;
  name?: string | null;
  path?: string | null;
  repo?: string | null;
  ref?: string | null;
  subdir?: string | null;
  managed_folder?: string | null;
  default?: boolean;
};

export type TenantProjects = {
  tenant: string;
  projects: string[];
};

export type StorageState = {
  ready: boolean;
  roots: RootInfo[];
  selectedRootId: string;
  tenant: string;
  project: string;
  tenants: TenantProjects[];
  path: string;
  entries: StorageEntry[];
  current: StorageEntry | null;
  selectedPaths: string[];
  registryBundles: RegistryBundle[];
  activeManagedFolders: string[];
  loading: boolean;
  deleting: boolean;
  exporting: boolean;
  error: string | null;
  message: string | null;
};
