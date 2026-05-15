import type { AppSettings, RouteContext, TabId } from './types';

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

export function routeContextFromLocation(): RouteContext {
  const path = window.location.pathname;
  const marker = '/api/integrations/bundles/';
  const index = path.indexOf(marker);
  if (index < 0) {
    return {
      tenant: '',
      project: '',
      bundleId: 'kdcube.copilot@2026-04-03-19-05',
      widgetAlias: 'telegram_copilot_webapp',
      widgetPath: '',
    };
  }
  const rest = path.slice(index + marker.length);
  const parts = rest.split('/').map((part) => decodeURIComponent(part));
  const widgetsIndex = parts.indexOf('widgets');
  return {
    tenant: parts[0] || '',
    project: parts[1] || '',
    bundleId: parts[2] || 'kdcube.copilot@2026-04-03-19-05',
    widgetAlias: widgetsIndex >= 0 ? parts[widgetsIndex + 1] || 'telegram_copilot_webapp' : 'telegram_copilot_webapp',
    widgetPath: widgetsIndex >= 0 ? parts.slice(widgetsIndex + 2).join('/') : '',
  };
}

export const ROUTE_CONTEXT = routeContextFromLocation();

export function activeTabFromPath(widgetPath: string): TabId {
  const first = String(widgetPath || '').trim().replace(/^\/+/, '').split('/', 1)[0].toLowerCase();
  return first === 'admin' || first === 'telegram' || first === 'telegram-admin' || first === 'telegram_admin'
    ? 'telegram_admin'
    : 'memory';
}

function tabPath(tab: TabId): string {
  const path = window.location.pathname;
  const marker = '/widgets/';
  const index = path.indexOf(marker);
  if (index < 0) return path;
  const before = path.slice(0, index + marker.length);
  const rest = path.slice(index + marker.length);
  const alias = rest.split('/')[0] || ROUTE_CONTEXT.widgetAlias || 'telegram_copilot_webapp';
  return `${before}${alias}/${tab === 'telegram_admin' ? 'telegram-admin' : 'memory'}`;
}

export function setBrowserTabPath(tab: TabId): void {
  try {
    window.history.replaceState({}, '', tabPath(tab));
  } catch {
    // Embedded webviews may not expose a mutable history.
  }
}

class SettingsManager {
  private values: AppSettings = {
    baseUrl: PLACEHOLDER_BASE_URL,
    accessToken: PLACEHOLDER_ACCESS_TOKEN,
    idToken: PLACEHOLDER_ID_TOKEN,
    idTokenHeader: PLACEHOLDER_ID_TOKEN_HEADER,
    defaultTenant: PLACEHOLDER_TENANT,
    defaultProject: PLACEHOLDER_PROJECT,
    defaultAppBundleId: PLACEHOLDER_BUNDLE_ID,
  };

  getBaseUrl(): string {
    if (isPlaceholder(this.values.baseUrl)) return window.location.origin;
    const trimmed = this.values.baseUrl.replace(/\/+$/, '');
    return trimmed.endsWith('/api') ? trimmed.slice(0, -4) : trimmed;
  }

  getTenant(): string {
    return isPlaceholder(this.values.defaultTenant) ? ROUTE_CONTEXT.tenant : this.values.defaultTenant;
  }

  getProject(): string {
    return isPlaceholder(this.values.defaultProject) ? ROUTE_CONTEXT.project : this.values.defaultProject;
  }

  getBundleId(): string {
    return isPlaceholder(this.values.defaultAppBundleId)
      ? ROUTE_CONTEXT.bundleId || 'kdcube.copilot@2026-04-03-19-05'
      : this.values.defaultAppBundleId;
  }

  getIdTokenHeader(): string {
    return isPlaceholder(this.values.idTokenHeader) ? 'X-ID-Token' : this.values.idTokenHeader;
  }

  getAccessToken(): string | null {
    return !this.values.accessToken || isPlaceholder(this.values.accessToken) ? null : this.values.accessToken;
  }

  getIdToken(): string | null {
    return !this.values.idToken || isPlaceholder(this.values.idToken) ? null : this.values.idToken;
  }

  setupParentListener(): Promise<boolean> {
    const identity = 'KDCUBE_COPILOT_TELEGRAM_WEBAPP';
    window.addEventListener('message', (event: MessageEvent) => {
      if (event.data?.type !== 'CONN_RESPONSE' && event.data?.type !== 'CONFIG_RESPONSE') return;
      if (event.data.identity !== identity || !event.data.config) return;
      const config = event.data.config;
      this.values = {
        ...this.values,
        baseUrl: config.baseUrl || this.values.baseUrl,
        accessToken: config.accessToken ?? this.values.accessToken,
        idToken: config.idToken ?? this.values.idToken,
        idTokenHeader: config.idTokenHeader || this.values.idTokenHeader,
        defaultTenant: config.defaultTenant || this.values.defaultTenant,
        defaultProject: config.defaultProject || this.values.defaultProject,
        defaultAppBundleId: config.defaultAppBundleId || this.values.defaultAppBundleId,
      };
    });
    window.parent.postMessage(
      {
        type: 'CONFIG_REQUEST',
        data: {
          identity,
          requestedFields: ['baseUrl', 'accessToken', 'idToken', 'idTokenHeader', 'defaultTenant', 'defaultProject', 'defaultAppBundleId'],
        },
      },
      '*',
    );
    return new Promise((resolve) => window.setTimeout(() => resolve(true), 500));
  }
}

export const settings = new SettingsManager();
