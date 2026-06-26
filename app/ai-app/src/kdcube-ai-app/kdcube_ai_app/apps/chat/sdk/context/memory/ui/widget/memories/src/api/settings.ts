const PLACEHOLDER_BASE_URL = '{{CHAT_BASE_URL}}';
const PLACEHOLDER_ACCESS_TOKEN = '{{ACCESS_TOKEN}}';
const PLACEHOLDER_ID_TOKEN = '{{ID_TOKEN}}';
const PLACEHOLDER_ID_TOKEN_HEADER = '{{ID_TOKEN_HEADER}}';
const PLACEHOLDER_TENANT = '{{DEFAULT_TENANT}}';
const PLACEHOLDER_PROJECT = '{{DEFAULT_PROJECT}}';
const PLACEHOLDER_BUNDLE_ID = '{{DEFAULT_APP_BUNDLE_ID}}';

type RuntimeConfigPayload = {
  baseUrl?: string;
  accessToken?: string | null;
  idToken?: string | null;
  idTokenHeader?: string;
  idTokenHeaderName?: string;
  auth?: {
    idTokenHeaderName?: string;
    provider?: string;
    connection_id?: string;
    connectionId?: string;
  };
  authProvider?: string;
  authConnectionId?: string;
  connection_id?: string;
  connectionId?: string;
  defaultTenant?: string;
  defaultProject?: string;
  defaultAppBundleId?: string;
  tenant?: string;
  tenant_id?: string;
  project?: string;
  project_id?: string;
  // Telegram Mini App host adds this to the SAME CONFIG_RESPONSE the scene /
  // browser hosts use. When present, the API client attaches it as
  // X-Telegram-Init-Data; the gateway + Connection Hub validate it centrally.
  telegramInitData?: string | null;
};

function isPlaceholder(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.includes('{{') && value.includes('}}');
}

function routeContext() {
  const markers = ['/api/integrations/bundles/', '/api/integrations/static/'];
  const path = window.location.pathname;
  const marker = markers.find((candidate) => path.includes(candidate));
  const index = marker ? path.indexOf(marker) : -1;
  const query = new URLSearchParams(window.location.search);
  if (!marker || index < 0) {
    return {
      tenant: query.get('tenant') || '',
      project: query.get('project') || '',
      bundleId: query.get('bundle_id') || query.get('bundleId') || '',
      widgetAlias: query.get('widget') || 'memories',
    };
  }
  const parts = path.slice(index + marker.length).split('/').map((part) => decodeURIComponent(part));
  const widgetsIndex = parts.indexOf('widgets');
  return {
    tenant: parts[0] || query.get('tenant') || '',
    project: parts[1] || query.get('project') || '',
    bundleId: parts[2] || query.get('bundle_id') || query.get('bundleId') || '',
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
    authProvider: '',
    authConnectionId: '',
    tenant: PLACEHOLDER_TENANT,
    project: PLACEHOLDER_PROJECT,
    bundleId: PLACEHOLDER_BUNDLE_ID,
    // Host-supplied (Telegram Mini App) — empty until a CONFIG_RESPONSE
    // carries it. Never a placeholder: a browser/scene host simply omits it.
    telegramInitData: '',
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

  // The relayed Telegram proof, when a host supplied one. Empty for
  // browser/scene hosts — the client then uses the Bearer/cookie path.
  getTelegramInitData(): string {
    return this.values.telegramInitData || '';
  }

  getAuthProvider(): string {
    return this.values.authProvider || '';
  }

  getAuthConnectionId(): string {
    return this.values.authConnectionId || '';
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

  private needsRuntimeConfig(): boolean {
    return (
      isPlaceholder(this.values.baseUrl) ||
      isPlaceholder(this.values.tenant) ||
      isPlaceholder(this.values.project) ||
      isPlaceholder(this.values.bundleId)
    );
  }

  private applyRuntimeConfig(config: RuntimeConfigPayload): boolean {
    const tenant = config.defaultTenant || config.tenant || config.tenant_id;
    const project = config.defaultProject || config.project || config.project_id;
    const idTokenHeader = config.idTokenHeader || config.idTokenHeaderName || config.auth?.idTokenHeaderName;
    const authProvider = config.authProvider || config.auth?.provider;
    const authConnectionId = (
      config.authConnectionId ||
      config.connection_id ||
      config.connectionId ||
      config.auth?.connection_id ||
      config.auth?.connectionId
    );
    const telegramInitData = typeof config.telegramInitData === 'string' ? config.telegramInitData : undefined;
    this.values = {
      ...this.values,
      baseUrl: config.baseUrl || this.values.baseUrl,
      accessToken: config.accessToken ?? this.values.accessToken,
      idToken: config.idToken ?? this.values.idToken,
      idTokenHeader: idTokenHeader || this.values.idTokenHeader,
      tenant: tenant || this.values.tenant,
      project: project || this.values.project,
      bundleId: config.defaultAppBundleId || this.values.bundleId,
      authProvider: authProvider || this.values.authProvider,
      authConnectionId: authConnectionId || this.values.authConnectionId,
      telegramInitData: telegramInitData ?? this.values.telegramInitData,
    };
    return Boolean(
      tenant || project || config.baseUrl || config.accessToken !== undefined ||
      config.idToken !== undefined || idTokenHeader || config.defaultAppBundleId ||
      telegramInitData || authProvider || authConnectionId,
    );
  }

  private isEmbedded(): boolean {
    try {
      return window.parent !== window;
    } catch {
      return true;
    }
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

  private authChangedCallback: (() => void) | null = null;
  private parentListenerReady: Promise<boolean> | null = null;

  // Re-run the caller's initial data load when the auth context changes —
  // e.g. the Telegram host delivers its proof slightly after first render, or
  // a kdcube-auth-changed signal makes the user become authenticated.
  onAuthContextChanged(callback: () => void): void {
    this.authChangedCallback = callback;
  }

  private requestParentConfig(): void {
    if (!this.isEmbedded()) return;
    try {
      window.parent.postMessage({
        type: 'CONFIG_REQUEST',
        data: {
          identity: 'MEMORIES_WIDGET',
          // telegramInitData rides the SAME config payload as the tokens —
          // hosts that have a Telegram proof include it, others omit it.
          requestedFields: [
            'baseUrl',
            'accessToken',
            'idToken',
            'idTokenHeader',
            'defaultTenant',
            'defaultProject',
            'defaultAppBundleId',
            'telegramInitData',
            'authProvider',
            'authConnectionId',
          ],
        },
      }, '*');
    } catch {
      // Parent may be opaque; widget falls back to route-derived config.
    }
  }

  setupParentListener(): Promise<boolean> {
    if (this.parentListenerReady) return this.parentListenerReady;
    const embedded = this.isEmbedded();
    let resolveReady: ((value: boolean) => void) | null = null;
    let resolved = false;
    const finish = (ready: boolean) => {
      if (resolved) return;
      resolved = true;
      resolveReady?.(ready);
    };

    // Persistent listener: the host may push a fresh CONFIG_RESPONSE later
    // (e.g. once the Telegram client populates initData). Re-apply and signal
    // when the proof/tokens actually change so the caller can reload.
    window.addEventListener('message', (event: MessageEvent) => {
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      // The standard host re-auth nudge: re-request config from the parent.
      if (data.type === 'kdcube-auth-changed') {
        this.requestParentConfig();
        return;
      }
      if (data.type !== 'CONN_RESPONSE' && data.type !== 'CONFIG_RESPONSE') return;
      if (data.identity !== 'MEMORIES_WIDGET' || !data.config) return;
      const before = `${this.values.accessToken}|${this.values.idToken}|${this.values.telegramInitData}|${this.values.authProvider}|${this.values.authConnectionId}`;
      this.applyRuntimeConfig(data.config);
      const after = `${this.values.accessToken}|${this.values.idToken}|${this.values.telegramInitData}|${this.values.authProvider}|${this.values.authConnectionId}`;
      finish(true);
      if (resolved && after !== before) this.authChangedCallback?.();
    });

    this.parentListenerReady = new Promise((resolve) => {
      resolveReady = resolve;
      // Even when route-derived tenant/project already suffice, an embedded
      // widget must still handshake so a Telegram host can deliver its proof.
      if (!this.needsRuntimeConfig() && !embedded) {
        finish(true);
        return;
      }
      // Embedded widgets prefer the host handshake (so they pick up any
      // host-only proof); only standalone frames fall back to the platform
      // config endpoint.
      if (embedded) {
        this.requestParentConfig();
        window.setTimeout(() => finish(true), 3000);
        return;
      }
      this.loadFrontendConfig().then((loaded) => {
        finish(loaded || Boolean(this.getTenant() && this.getProject()));
      });
    });
    return this.parentListenerReady;
  }
}

export const settings = new Settings();
