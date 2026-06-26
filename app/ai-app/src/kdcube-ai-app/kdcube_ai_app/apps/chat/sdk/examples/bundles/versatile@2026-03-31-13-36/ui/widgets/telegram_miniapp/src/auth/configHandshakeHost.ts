// Host side of the standard KDCube widget config handshake.
//
// The Telegram Mini App hosts the memory widget as an iframe. The widget asks
// for its runtime config the normal way:
//   iframe -> host: { type: 'CONFIG_REQUEST', data: { identity, requestedFields } }
//   host -> iframe: { type: 'CONFIG_RESPONSE', identity, config: { ... } }
//
// The only Telegram-specific part is that, when running inside Telegram, the
// host adds `telegramInitData` and the server-configured `authConnectionId` to
// the SAME config payload. The widget then attaches X-Telegram-Init-Data plus
// X-KDCube-Auth-Connection-ID; the gateway + Connection Hub validate it
// centrally. The host NEVER sends the bot token or any server secret — only
// the public initData proof and a non-secret connection selector.
//
// When initData becomes available after the iframe mounted, the host posts the
// standard `kdcube-auth-changed` nudge so the widget re-requests config. No new
// message family is introduced.

import { settings } from '../store/settings';

interface ConfigHandshakeHostOptions {
  // Identity advertised by the child in its CONFIG_REQUEST. The host answers
  // only requests carrying this identity (the memory widget uses
  // 'MEMORIES_WIDGET').
  identity: string;
}

function telegramInitData(): string {
  return window.Telegram?.WebApp?.initData || '';
}

function buildConfig(): Record<string, unknown> {
  const config: Record<string, unknown> = {
    baseUrl: settings.getBaseUrl(),
    defaultTenant: settings.getTenant(),
    defaultProject: settings.getProject(),
    defaultAppBundleId: settings.getBundleId(),
    authProvider: settings.getAuthProvider(),
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
  // The Telegram proof rides the same payload when present.
  const initData = telegramInitData();
  if (initData) config.telegramInitData = initData;
  const connectionId = settings.getAuthConnectionId();
  if (connectionId) config.authConnectionId = connectionId;
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

// Install the host handshake against a child iframe (element or its window):
//   - answer the child's CONFIG_REQUEST (matched by identity) with the host
//     runtime config + any Telegram proof, replying to the requesting window
//   - when initData populates after mount (the Telegram client can be late),
//     post `kdcube-auth-changed` so the child re-requests config
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
    // Reply to the requester directly when resolvable; otherwise the child.
    const source = event.source as Window | null;
    postConfigResponse(source ?? targetWindow(target), identity);
  };
  window.addEventListener('message', onMessage);

  // Standard CONFIG_RESPONSE is safe to push as well as to return. This covers
  // the iframe-load race where the child posts CONFIG_REQUEST before this host
  // effect has installed its listener.
  window.setTimeout(() => postConfigResponse(targetWindow(target), identity), 0);

  // initData can populate slightly after mount inside the Telegram client.
  // Watch for it and, once it appears, nudge the child to re-request config
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
