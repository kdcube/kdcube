import type { AppSettings, RouteContext, TabId } from './types';

const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}';
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}';
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}';
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}';
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}';
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}';
const PLACEHOLDER_BUNDLE_ID = '{{DEFAULT_APP_BUNDLE_ID}}';

interface RuntimeConfigPayload {
  baseUrl?: string;
  accessToken?: string | null;
  idToken?: string | null;
  idTokenHeader?: string;
  idTokenHeaderName?: string;
  defaultTenant?: string;
  defaultProject?: string;
  defaultAppBundleId?: string;
  tenant?: string;
  project?: string;
  tenant_id?: string;
  project_id?: string;
  auth?: {
    idTokenHeaderName?: string;
  };
}

function isPlaceholder(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.includes('{{') && value.includes('}}');
}

function decodePathPart(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

export function routeContextFromLocation(): RouteContext {
  const path = window.location.pathname;
  const marker = '/api/integrations/bundles/';
  const index = path.indexOf(marker);
  if (index < 0) {
    const params = new URLSearchParams(window.location.search);
    return {
      tenant: params.get('tenant') || '',
      project: params.get('project') || '',
      bundleId: params.get('bundle_id') || params.get('bundleId') || 'kdcube.copilot@2026-04-03-19-05',
      widgetAlias: params.get('widget') || 'copilot_webapp',
      widgetPath: params.get('widget_path') || params.get('widgetPath') || '',
    };
  }
  const rest = path.slice(index + marker.length);
  const parts = rest.split('/').map(decodePathPart);
  const widgetsIndex = parts.indexOf('widgets');
  return {
    tenant: parts[0] || '',
    project: parts[1] || '',
    bundleId: parts[2] || 'kdcube.copilot@2026-04-03-19-05',
    widgetAlias: widgetsIndex >= 0 ? parts[widgetsIndex + 1] || 'copilot_webapp' : 'copilot_webapp',
    widgetPath: widgetsIndex >= 0 ? parts.slice(widgetsIndex + 2).join('/') : '',
  };
}

export const ROUTE_CONTEXT = routeContextFromLocation();

export function activeTabFromPath(widgetPath: string): TabId {
  const first = String(widgetPath || '').trim().replace(/^\/+/, '').split('/', 1)[0].toLowerCase();
  if (first === 'chat' || first === 'chats' || first === 'conversation' || first === 'conversations') return 'conversations';
  if (first === 'admin' || first === 'telegram' || first === 'telegram-admin' || first === 'telegram_admin') return 'telegram_admin';
  return 'memory';
}

function tabPath(tab: TabId): string {
  const path = window.location.pathname;
  const marker = '/widgets/';
  const index = path.indexOf(marker);
  if (index < 0) return path;
  const before = path.slice(0, index + marker.length);
  const rest = path.slice(index + marker.length);
  const alias = rest.split('/')[0] || ROUTE_CONTEXT.widgetAlias || 'copilot_webapp';
  const segment = tab === 'telegram_admin' ? 'telegram-admin' : tab === 'conversations' ? 'chats' : 'memory';
  return `${before}${alias}/${segment}`;
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

  private needsRuntimeConfig(): boolean {
    return (
      isPlaceholder(this.values.baseUrl) ||
      isPlaceholder(this.values.defaultTenant) ||
      isPlaceholder(this.values.defaultProject) ||
      isPlaceholder(this.values.defaultAppBundleId)
    );
  }

  private applyRuntimeConfig(config: RuntimeConfigPayload): void {
    const tenant = config.defaultTenant || config.tenant || config.tenant_id;
    const project = config.defaultProject || config.project || config.project_id;
    this.values = {
      ...this.values,
      baseUrl: config.baseUrl || this.values.baseUrl,
      accessToken: config.accessToken ?? this.values.accessToken,
      idToken: config.idToken ?? this.values.idToken,
      idTokenHeader:
        config.idTokenHeader ||
        config.idTokenHeaderName ||
        config.auth?.idTokenHeaderName ||
        this.values.idTokenHeader,
      defaultTenant: tenant || this.values.defaultTenant,
      defaultProject: project || this.values.defaultProject,
      defaultAppBundleId: config.defaultAppBundleId || this.values.defaultAppBundleId,
    };
  }

  private async loadFrontendConfig(): Promise<boolean> {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 1000);
    try {
      const response = await fetch(`${this.getBaseUrl()}/api/cp-frontend-config`, {
        method: 'GET',
        credentials: 'include',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      });
      if (!response.ok) return false;
      const config = (await response.json()) as RuntimeConfigPayload | null;
      if (!config || typeof config !== 'object') return false;
      this.applyRuntimeConfig(config);
      return true;
    } catch {
      return false;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  setupParentListener(): Promise<boolean> {
    const identity = 'KDCUBE_COPILOT_WEBAPP';
    let resolved = false;
    let resolveReady: ((value: boolean) => void) | null = null;
    const finish = (value: boolean) => {
      if (resolved) return;
      resolved = true;
      resolveReady?.(value);
    };
    window.addEventListener('message', (event: MessageEvent) => {
      if (event.data?.type !== 'CONN_RESPONSE' && event.data?.type !== 'CONFIG_RESPONSE') return;
      if (event.data.identity !== identity || !event.data.config) return;
      this.applyRuntimeConfig(event.data.config);
      finish(true);
    });

    return new Promise((resolve) => {
      resolveReady = resolve;
      if (!this.needsRuntimeConfig()) {
        finish(true);
        return;
      }
      const requestParentConfig = () => {
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
        window.setTimeout(() => finish(Boolean(this.getTenant() && this.getProject())), 3000);
      };
      void this.loadFrontendConfig().then((loaded) => {
        if (loaded) {
          finish(true);
        } else {
          requestParentConfig();
        }
      });
    });
  }
}

export const settings = new SettingsManager();
