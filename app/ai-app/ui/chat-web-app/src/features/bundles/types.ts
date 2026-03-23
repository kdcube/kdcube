export interface BundleEntry {
    id: string;
    name?: string | null;
    path: string;
    module?: string | null;
    singleton?: boolean | null;
    description?: string | null;
    version?: string | null;
    repo?: string | null;
    ref?: string | null;
    subdir?: string | null;
    git_commit?: string | null;
}

export interface BundlesResponse {
    available_bundles: Record<string, BundleEntry>;
    default_bundle_id?: string | null;
    tenant?: string;
    project?: string;
}

export interface BundlesInfo {
    defaultBundle: string | null | undefined;
    bundles: Record<string, BundleEntry>;
}

export interface BundlesState {
    currentBundle: string | null;
}