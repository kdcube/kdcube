import type { AppSettings, RouteContext, TabId } from './types';

const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}';
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}';
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}';
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}';
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}';
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}';
const PLACEHOLDER_BUNDLE_ID = '{{DEFAULT_APP_BUNDLE_ID}}';
const DEFAULT_CONNECTION_HUB_BUNDLE_ID = 'connection-hub@1-0';

interface RuntimeConfigPayload {
  baseUrl?: string;
  accessToken?: string | null;
  idToken?: string | null;
  idTokenHeader?: string;
  idTokenHeaderName?: string;
  defaultTenant?: string;
  defaultProject?: string;
  defaultAppBundleId?: string;
  connectionHubBundleId?: string;
  connections?: {
    connection_hub?: {
      bundle_id?: string;
    };
  };
  tenant?: string;
  project?: string;
  tenant_id?: string;
  project_id?: string;
  auth?: {
    idTokenHeaderName?: string;
    provider?: string;
    authority_id?: string;
    authorityId?: string;
    authenticator_id?: string;
    authenticatorId?: string;
  };
  authContext?: {
    headers?: Record<string, unknown>;
  };
  authProvider?: string;
  authAuthorityId?: string;
  authAuthenticatorId?: string;
  authority_id?: string;
  authorityId?: string;
  authenticator_id?: string;
  authenticatorId?: string;
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
      widgetAlias: params.get('widget') || 'telegram_miniapp',
      widgetPath: params.get('widget_path') || params.get('widgetPath') || '',
      publicRoute: params.get('public') === '1',
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
    widgetAlias: widgetAnchor >= 0 ? parts[widgetAnchor + 1] || 'telegram_miniapp' : 'telegram_miniapp',
    widgetPath: widgetAnchor >= 0 ? parts.slice(widgetAnchor + 2).join('/') : '',
    publicRoute: publicWidgetsIndex >= 0 && publicWidgetsIndex < widgetAnchor,
  };
}

export const ROUTE_CONTEXT = routeContextFromLocation();

function normalizeAuthContextHeaders(input?: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  if (!input || typeof input !== 'object') return out;
  Object.entries(input).forEach(([key, value]) => {
    const name = String(key || '').trim();
    if (!name || value === undefined || value === null) return;
    const text = String(value);
    if (!text) return;
    out[name] = text;
  });
  return out;
}

function fallbackAuthContextHeaders(config: RuntimeConfigPayload): Record<string, string> {
  const provider = config.authProvider || config.auth?.provider;
  const authorityId = (
    config.authAuthorityId ||
    config.authority_id ||
    config.authorityId ||
    config.auth?.authority_id ||
    config.auth?.authorityId
  );
  const authenticatorId = (
    config.authAuthenticatorId ||
    config.authenticator_id ||
    config.authenticatorId ||
    config.auth?.authenticator_id ||
    config.auth?.authenticatorId
  );
  const out: Record<string, string> = {};
  if (provider) out['X-KDCube-Auth-Provider'] = String(provider);
  if (authorityId) out['X-KDCube-Auth-Authority-ID'] = String(authorityId);
  if (authenticatorId) out['X-KDCube-Auth-Authenticator-ID'] = String(authenticatorId);
  return out;
}

export function activeTabFromPath(widgetPath: string): TabId {
  const first = String(widgetPath || '').trim().replace(/^\/+/, '').split('/', 1)[0].toLowerCase();
  if (first === 'chat' || first === 'chats' || first === 'conversation' || first === 'conversations') return 'conversations';
  if (first === 'connect' || first === 'connections' || first === 'link') return 'connections';
  return 'memory';
}

export function tabPath(tab: TabId): string {
  const path = window.location.pathname;
  const marker = '/widgets/';
  const index = path.indexOf(marker);
  if (index < 0) return path;
  const before = path.slice(0, index + marker.length);
  const rest = path.slice(index + marker.length);
  const alias = rest.split('/')[0] || ROUTE_CONTEXT.widgetAlias || 'telegram_miniapp';
  const segment = tab === 'connections' ? 'connections' : tab === 'conversations' ? 'chats' : 'memory';
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
    authContextHeaders: {},
    defaultTenant: PLACEHOLDER_TENANT,
    defaultProject: PLACEHOLDER_PROJECT,
    defaultAppBundleId: PLACEHOLDER_BUNDLE_ID,
    connectionHubBundleId: DEFAULT_CONNECTION_HUB_BUNDLE_ID,
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

  getConnectionHubBundleId(): string {
    const configured = String(this.settings.connectionHubBundleId || '').trim();
    return configured || DEFAULT_CONNECTION_HUB_BUNDLE_ID;
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

  getAuthContextHeaders(): Record<string, string> {
    return { ...this.settings.authContextHeaders };
  }

  // Build a served-widget iframe URL for another bundle (mirrors how the
  // scene host composes `widgetUrlForBundle`). Carries tenant/project/bundle
  // in the path and honours the public-vs-private route this host loaded from
  // so an anonymous Telegram session still resolves the public widget route.
  widgetUrlForBundle(bundleId: string, alias: string, params?: Record<string, string>): string {
    const tenant = encodeURIComponent(this.getTenant());
    const project = encodeURIComponent(this.getProject());
    const base = `${this.getBaseUrl()}/api/integrations/bundles/${tenant}/${project}/${encodeURIComponent(bundleId)}`;
    const route = ROUTE_CONTEXT.publicRoute ? 'public/widgets' : 'widgets';
    const url = new URL(`${base}/${route}/${encodeURIComponent(alias)}`);
    if (params) {
      Object.entries(params).forEach(([key, value]) => url.searchParams.set(key, value));
    }
    return url.toString();
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
    const authContextHeaders = normalizeAuthContextHeaders(config.authContext?.headers);
    const legacyHeaders = fallbackAuthContextHeaders(config);
    const nextAuthContextHeaders = Object.keys(authContextHeaders).length > 0
      ? authContextHeaders
      : Object.keys(legacyHeaders).length > 0
        ? legacyHeaders
        : this.settings.authContextHeaders;
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
      authContextHeaders: nextAuthContextHeaders,
      defaultTenant: tenant || this.settings.defaultTenant,
      defaultProject: project || this.settings.defaultProject,
      defaultAppBundleId: config.defaultAppBundleId || this.settings.defaultAppBundleId,
      connectionHubBundleId:
        config.connectionHubBundleId ||
        config.connections?.connection_hub?.bundle_id ||
        this.settings.connectionHubBundleId,
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
                'authContext',
                'defaultTenant',
                'defaultProject',
                'defaultAppBundleId',
                'connections',
                'connectionHubBundleId',
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
