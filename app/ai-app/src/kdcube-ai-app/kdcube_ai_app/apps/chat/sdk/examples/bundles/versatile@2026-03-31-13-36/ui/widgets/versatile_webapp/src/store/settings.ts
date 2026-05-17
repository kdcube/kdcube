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
      bundleId: params.get('bundle_id') || params.get('bundleId') || 'versatile@2026-03-31-13-36',
      widgetAlias: params.get('widget') || 'versatile_webapp',
      widgetPath: params.get('widget_path') || params.get('widgetPath') || '',
    };
  }
  const rest = path.slice(index + marker.length);
  const parts = rest.split('/').map(decodePathPart);
  const widgetsIndex = parts.indexOf('widgets');
  const publicWidgetsIndex = parts.indexOf('public');
  const widgetAnchor = widgetsIndex >= 0 ? widgetsIndex : publicWidgetsIndex >= 0 ? parts.indexOf('widgets', publicWidgetsIndex) : -1;
  return {
    tenant: parts[0] || '',
    project: parts[1] || '',
    bundleId: parts[2] || 'versatile@2026-03-31-13-36',
    widgetAlias: widgetAnchor >= 0 ? parts[widgetAnchor + 1] || 'versatile_webapp' : 'versatile_webapp',
    widgetPath: widgetAnchor >= 0 ? parts.slice(widgetAnchor + 2).join('/') : '',
  };
}

export const ROUTE_CONTEXT = routeContextFromLocation();

export function activeTabFromPath(widgetPath: string): TabId {
  const first = String(widgetPath || '').trim().replace(/^\/+/, '').split('/', 1)[0].toLowerCase();
  if (first === 'chat' || first === 'chats' || first === 'conversation' || first === 'conversations') return 'conversations';
  if (first === 'admin' || first === 'telegram' || first === 'telegram-admin' || first === 'telegram_admin') return 'telegram_admin';
  return 'memory';
}

export function tabPath(tab: TabId): string {
  const path = window.location.pathname;
  const marker = '/widgets/';
  const index = path.indexOf(marker);
  if (index < 0) return path;
  const before = path.slice(0, index + marker.length);
  const rest = path.slice(index + marker.length);
  const alias = rest.split('/')[0] || ROUTE_CONTEXT.widgetAlias || 'versatile_webapp';
  const segment = tab === 'telegram_admin' ? 'telegram-admin' : tab === 'conversations' ? 'chats' : 'memory';
  return `${before}${alias}/${segment}`;
}

export function setBrowserTabPath(tab: TabId): void {
  try {
    window.history.replaceState({}, '', tabPath(tab));
  } catch {
    // srcDoc iframes do not have a useful browser path.
  }
}

class SettingsManager {
  private settings: AppSettings = {
    baseUrl: PLACEHOLDER_BASE_URL,
    accessToken: PLACEHOLDER_ACCESS_TOKEN,
    idToken: PLACEHOLDER_ID_TOKEN,
    idTokenHeader: PLACEHOLDER_ID_TOKEN_HEADER,
    defaultTenant: PLACEHOLDER_TENANT,
    defaultProject: PLACEHOLDER_PROJECT,
    defaultAppBundleId: PLACEHOLDER_BUNDLE_ID,
  };

  private callback: (() => void) | null = null;

  getBaseUrl(): string {
    // Fallback to this widget frame's own origin. Do not use window.top or a
    // parent page origin; embedded host pages may live on another domain.
    if (isPlaceholder(this.settings.baseUrl)) return window.location.origin;
    const trimmed = this.settings.baseUrl.replace(/\/+$/, '');
    return trimmed.endsWith('/api') ? trimmed.slice(0, -4) : trimmed;
  }

  getTenant(): string {
    return isPlaceholder(this.settings.defaultTenant) ? ROUTE_CONTEXT.tenant : this.settings.defaultTenant;
  }

  getProject(): string {
    return isPlaceholder(this.settings.defaultProject) ? ROUTE_CONTEXT.project : this.settings.defaultProject;
  }

  getBundleId(): string {
    return isPlaceholder(this.settings.defaultAppBundleId)
      ? ROUTE_CONTEXT.bundleId || 'versatile@2026-03-31-13-36'
      : this.settings.defaultAppBundleId;
  }

  getIdTokenHeader(): string {
    return isPlaceholder(this.settings.idTokenHeader) ? 'X-ID-Token' : this.settings.idTokenHeader;
  }

  getAccessToken(): string | null {
    return !this.settings.accessToken || isPlaceholder(this.settings.accessToken) ? null : this.settings.accessToken;
  }

  getIdToken(): string | null {
    return !this.settings.idToken || isPlaceholder(this.settings.idToken) ? null : this.settings.idToken;
  }

  update(partial: Partial<AppSettings>): void {
    this.settings = { ...this.settings, ...partial };
    this.callback?.();
  }

  onConfigReceived(callback: () => void): void {
    this.callback = callback;
  }

  private needsRuntimeConfig(): boolean {
    return (
      isPlaceholder(this.settings.baseUrl) ||
      isPlaceholder(this.settings.defaultTenant) ||
      isPlaceholder(this.settings.defaultProject) ||
      isPlaceholder(this.settings.defaultAppBundleId)
    );
  }

  private applyRuntimeConfig(config: RuntimeConfigPayload, options: { notify?: boolean } = {}): void {
    const tenant = config.defaultTenant || config.tenant || config.tenant_id;
    const project = config.defaultProject || config.project || config.project_id;
    this.settings = {
      ...this.settings,
      baseUrl: config.baseUrl || this.settings.baseUrl,
      accessToken: config.accessToken ?? this.settings.accessToken,
      idToken: config.idToken ?? this.settings.idToken,
      idTokenHeader:
        config.idTokenHeader ||
        config.idTokenHeaderName ||
        config.auth?.idTokenHeaderName ||
        this.settings.idTokenHeader,
      defaultTenant: tenant || this.settings.defaultTenant,
      defaultProject: project || this.settings.defaultProject,
      defaultAppBundleId: config.defaultAppBundleId || this.settings.defaultAppBundleId,
    };
    if (options.notify !== false) {
      this.callback?.();
    }
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
      this.applyRuntimeConfig(config, { notify: false });
      return true;
    } catch {
      return false;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  setupParentListener(): Promise<boolean> {
    const identity = 'VERSATILE_WEBAPP';
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

    return new Promise<boolean>((resolve) => {
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
              requestedFields: [
                'baseUrl',
                'accessToken',
                'idToken',
                'idTokenHeader',
                'defaultTenant',
                'defaultProject',
                'defaultAppBundleId',
              ],
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
