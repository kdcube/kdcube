const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}';
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}';
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}';
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}';
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}';
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}';
const PLACEHOLDER_BUNDLE_ID = '{{DEFAULT_APP_BUNDLE_ID}}';

function isPlaceholder(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.includes('{{') && value.includes('}}');
}

function routeContext() {
  const marker = '/api/integrations/bundles/';
  const path = window.location.pathname;
  const index = path.indexOf(marker);
  if (index < 0) return { tenant: '', project: '', bundleId: '', widgetAlias: 'memories' };
  const parts = path.slice(index + marker.length).split('/').map((part) => decodeURIComponent(part));
  const widgetsIndex = parts.indexOf('widgets');
  return {
    tenant: parts[0] || '',
    project: parts[1] || '',
    bundleId: parts[2] || '',
    widgetAlias: widgetsIndex >= 0 ? parts[widgetsIndex + 1] || 'memories' : 'memories',
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
    bundleId: PLACEHOLDER_BUNDLE_ID,
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

  getBundleId(): string {
    return isPlaceholder(this.values.bundleId) ? context.bundleId : this.values.bundleId;
  }

  getWidgetAlias(): string {
    return context.widgetAlias || 'memories';
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

  setupParentListener(): Promise<boolean> {
    window.addEventListener('message', (event: MessageEvent) => {
      if (event.data?.type !== 'CONN_RESPONSE' && event.data?.type !== 'CONFIG_RESPONSE') return;
      if (event.data.identity !== 'MEMORIES_WIDGET' || !event.data.config) return;
      const config = event.data.config;
      this.values = {
        ...this.values,
        baseUrl: config.baseUrl || this.values.baseUrl,
        accessToken: config.accessToken ?? this.values.accessToken,
        idToken: config.idToken ?? this.values.idToken,
        idTokenHeader: config.idTokenHeader || this.values.idTokenHeader,
        tenant: config.defaultTenant || this.values.tenant,
        project: config.defaultProject || this.values.project,
        bundleId: config.defaultAppBundleId || this.values.bundleId,
      };
    });
    window.parent.postMessage({
      type: 'CONFIG_REQUEST',
      data: {
        identity: 'MEMORIES_WIDGET',
        requestedFields: ['baseUrl', 'accessToken', 'idToken', 'idTokenHeader', 'defaultTenant', 'defaultProject', 'defaultAppBundleId'],
      },
    }, '*');
    return new Promise((resolve) => window.setTimeout(() => resolve(true), 250));
  }
}

export const settings = new Settings();
