// Host side of the standard KDCube widget config handshake.
//
// The Telegram Mini App hosts the memory widget as an iframe. The widget asks
// for its runtime config the normal way:
//   iframe -> host: { type: 'CONFIG_REQUEST', data: { identity, requestedFields } }
//   host -> iframe: { type: 'CONFIG_RESPONSE', identity, config: { ... } }
//
// The host gets a server-authored authContext header template and promotes it
// to the iframe. For Telegram, the host adds the browser-owned initData proof
// only when the server template declares X-KDCube-Auth-Provider=telegram. The
// host NEVER sends provider tokens or server secrets.
//
// When initData becomes available after the iframe mounted, the host posts the
// standard `kdcube-auth-changed` nudge so the widget re-requests config. No new
// message family is introduced.

import { settings } from '../store/settings';

interface ConfigHandshakeHostOptions {
  // Identity advertised by the iframe in its CONFIG_REQUEST. The host answers
  // only requests carrying this identity (the memory widget uses
  // 'MEMORIES_WIDGET').
  identity: string;
}

function telegramInitData(): string {
  return window.Telegram?.WebApp?.initData || '';
}

function providerFrom(headers: Record<string, string>): string {
  const direct = headers['X-KDCube-Auth-Provider'] || headers['x-kdcube-auth-provider'] || '';
  if (direct) return direct.toLowerCase();
  const found = Object.entries(headers).find(([name]) => name.toLowerCase() === 'x-kdcube-auth-provider');
  return String(found?.[1] || '').toLowerCase();
}

function buildConfig(): Record<string, unknown> {
  const config: Record<string, unknown> = {
    baseUrl: settings.getBaseUrl(),
    defaultTenant: settings.getTenant(),
    defaultProject: settings.getProject(),
    defaultAppBundleId: settings.getBundleId(),
  };
  // Forward normal token fields only when the host actually has them; the
  // memory widget keeps its cookie/credentials fallback otherwise.
  const accessToken = settings.getAccessToken();
  const idToken = settings.getIdToken();
  if (accessToken) config.accessToken = accessToken;
  if (idToken) {
    config.idToken = idToken;
    config.idTokenHeader = settings.getIdTokenHeader();
  }
  // Surface auth rides the same config payload as an opaque header map. The
  // backend provides the selector headers; the host only fills browser-owned
  // proof material that the backend cannot know.
  const authHeaders = settings.getAuthContextHeaders();
  if (providerFrom(authHeaders) === 'telegram') {
    const initData = telegramInitData();
    if (initData) authHeaders['X-Telegram-Init-Data'] = initData;
  }
  if (Object.keys(authHeaders).length > 0) {
    config.authContext = { headers: authHeaders };
  }
  return config;
}

function targetWindow(target: HTMLIFrameElement | Window | null): Window | null {
  if (!target) return null;
  if (target instanceof Window) return target;
  return target.contentWindow ?? null;
}

function postConfigResponse(win: Window | null, identity: string): void {
  if (!win) return;
  win.postMessage({ type: 'CONFIG_RESPONSE', identity, config: buildConfig() }, '*');
}

// Install the host handshake against a hosted iframe (element or its window):
//   - answer the iframe's CONFIG_REQUEST (matched by identity) with the host
//     runtime config + host-owned authContext, replying to the requesting window
//   - when initData populates after mount (the Telegram client can be late),
//     post `kdcube-auth-changed` so the iframe re-requests config
// Returns a disposer that removes the listener and stops the poll.
export function installConfigHandshakeHost(
  target: HTMLIFrameElement | Window | null,
  options: ConfigHandshakeHostOptions,
): () => void {
  const { identity } = options;

  const onMessage = (event: MessageEvent) => {
    const data = event.data;
    if (!data || typeof data !== 'object') return;
    if ((data as Record<string, unknown>).type !== 'CONFIG_REQUEST') return;
    const requested = (data as { data?: { identity?: unknown } }).data?.identity
      ?? (data as { identity?: unknown }).identity;
    if (requested && requested !== identity) return;
    // Reply to the requester directly when resolvable; otherwise the iframe.
    const source = event.source as Window | null;
    postConfigResponse(source ?? targetWindow(target), identity);
  };
  window.addEventListener('message', onMessage);

  // Standard CONFIG_RESPONSE is safe to push as well as to return. This covers
  // the iframe-load race where the iframe posts CONFIG_REQUEST before this host
  // effect has installed its listener.
  window.setTimeout(() => postConfigResponse(targetWindow(target), identity), 0);

  // initData can populate slightly after mount inside the Telegram client.
  // Watch for it and, once it appears, nudge the iframe to re-request config
  // via the standard kdcube-auth-changed signal.
  let lastInitData = telegramInitData();
  const timer = window.setInterval(() => {
    const current = telegramInitData();
    if (current === lastInitData) return;
    lastInitData = current;
    targetWindow(target)?.postMessage({ type: 'kdcube-auth-changed' }, '*');
  }, 1000);

  return () => {
    window.removeEventListener('message', onMessage);
    window.clearInterval(timer);
  };
}
