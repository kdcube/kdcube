const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}';
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}';
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}';
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}';
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}';
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}';

type RuntimeConfigPayload = {
  baseUrl?: string;
  accessToken?: string | null;
  idToken?: string | null;
  idTokenHeader?: string;
  idTokenHeaderName?: string;
  auth?: { idTokenHeaderName?: string };
  defaultTenant?: string;
  defaultProject?: string;
  tenant?: string;
  tenant_id?: string;
  project?: string;
  project_id?: string;
};

function isPlaceholder(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.includes('{{') && value.includes('}}');
}

function routeContext() {
  const path = window.location.pathname;
  const marker = '/api/integrations/bundles/';
  const index = path.indexOf(marker);
  const query = new URLSearchParams(window.location.search);
  if (index < 0) {
    return {
      tenant: query.get('tenant') || '',
      project: query.get('project') || '',
    };
  }
  const parts = path.slice(index + marker.length).split('/').map((part) => decodeURIComponent(part));
  return {
    tenant: parts[0] || query.get('tenant') || '',
    project: parts[1] || query.get('project') || '',
  };
}

const context = routeContext();

class Settings {
  private values = {
    baseUrl: PLACEHOLDER_BASE_URL,
    accessToken: PLACEHOLDER_ACCESS_TOKEN,
    idToken: PLACEHOLDER_ID_TOKEN,
    idTokenHeader: PLACEHOLDER_ID_TOKEN_HEADER,
    tenant: PLACEHOLDER_TENANT,
    project: PLACEHOLDER_PROJECT,
  };

  getBaseUrl(): string {
    if (isPlaceholder(this.values.baseUrl)) return window.location.origin;
    const trimmed = this.values.baseUrl.replace(/\/+$/, '');
    return trimmed.endsWith('/api') ? trimmed.slice(0, -4) : trimmed;
  }

  getTenant(): string {
    return isPlaceholder(this.values.tenant) ? context.tenant : this.values.tenant;
  }

  getProject(): string {
    return isPlaceholder(this.values.project) ? context.project : this.values.project;
  }

  authHeaders(base?: HeadersInit): Headers {
    const headers = new Headers(base);
    if (this.values.accessToken && !isPlaceholder(this.values.accessToken)) {
      headers.set('Authorization', `Bearer ${this.values.accessToken}`);
    }
    if (this.values.idToken && !isPlaceholder(this.values.idToken)) {
      headers.set(isPlaceholder(this.values.idTokenHeader) ? 'X-ID-Token' : this.values.idTokenHeader, this.values.idToken);
    }
    return headers;
  }

  private applyRuntimeConfig(config: RuntimeConfigPayload): boolean {
    const tenant = config.defaultTenant || config.tenant || config.tenant_id;
    const project = config.defaultProject || config.project || config.project_id;
    const idTokenHeader = config.idTokenHeader || config.idTokenHeaderName || config.auth?.idTokenHeaderName;
    this.values = {
      ...this.values,
      baseUrl: config.baseUrl || this.values.baseUrl,
      accessToken: config.accessToken ?? this.values.accessToken,
      idToken: config.idToken ?? this.values.idToken,
      idTokenHeader: idTokenHeader || this.values.idTokenHeader,
      tenant: tenant || this.values.tenant,
      project: project || this.values.project,
    };
    return Boolean(tenant || project || config.baseUrl || config.accessToken !== undefined || config.idToken !== undefined || idTokenHeader);
  }

  private async loadFrontendConfig(): Promise<boolean> {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 1000);
    try {
      const response = await fetch(`${this.getBaseUrl()}/api/cp-frontend-config`, {
        credentials: 'include',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      });
      if (!response.ok) return false;
      return this.applyRuntimeConfig(await response.json());
    } catch {
      return false;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  setupParentListener(): Promise<boolean> {
    let resolveReady: ((value: boolean) => void) | null = null;
    let resolved = false;
    const finish = (ready: boolean) => {
      if (resolved) return;
      resolved = true;
      resolveReady?.(ready);
    };

    window.addEventListener('message', (event: MessageEvent) => {
      if (event.data?.type !== 'CONN_RESPONSE' && event.data?.type !== 'CONFIG_RESPONSE') return;
      if (event.data.identity !== 'ADMIN_STORAGE_WIDGET' || !event.data.config) return;
      this.applyRuntimeConfig(event.data.config);
      finish(true);
    });

    return new Promise((resolve) => {
      resolveReady = resolve;
      const requestParentConfig = () => {
        window.parent.postMessage({
          type: 'CONFIG_REQUEST',
          data: {
            identity: 'ADMIN_STORAGE_WIDGET',
            requestedFields: ['baseUrl', 'accessToken', 'idToken', 'idTokenHeader', 'defaultTenant', 'defaultProject'],
          },
        }, '*');
        window.setTimeout(() => finish(true), 2500);
      };
      this.loadFrontendConfig().then((loaded) => {
        if (loaded) finish(true);
        else requestParentConfig();
      });
    });
  }
}

export const settings = new Settings();
