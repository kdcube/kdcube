import { io, type Socket } from 'socket.io-client';
import { authHeaders } from './apiClient';
import { settings } from './settings';
import { isTelegramWebApp } from '../telegram/utils';

interface FederatedClaimPayload {
  ok?: boolean;
  federated_token?: string;
  session_id?: string;
  expires_at?: number;
  bundle_id?: string;
  error?: string;
  message?: string;
}

interface ProfilePayload {
  session_id?: string;
}

interface SocketContext {
  auth: Record<string, unknown>;
  sessionId: string;
  key: string;
}

export interface DataBusServiceEnvelope {
  type?: string;
  data?: Record<string, unknown>;
  event?: Record<string, unknown>;
  service?: Record<string, unknown>;
  conversation?: Record<string, unknown>;
}

let dataBusSocket: Socket | null = null;
let dataBusSocketKey = '';
let dataBusSessionId = '';
let dataBusConnectPromise: Promise<void> | null = null;

async function fetchProfileSessionId(): Promise<string> {
  const response = await fetch(`${settings.getBaseUrl()}/profile`, {
    method: 'GET',
    credentials: 'include',
    cache: 'no-store',
    headers: authHeaders({ Accept: 'application/json' }),
  });
  if (!response.ok) throw new Error(`Profile request failed: ${response.status}`);
  const profile = (await response.json()) as ProfilePayload;
  const sessionId = String(profile.session_id || '').trim();
  if (!sessionId) throw new Error('Profile did not return a session_id.');
  return sessionId;
}

async function buildSocketContext(): Promise<SocketContext> {
  if (isTelegramWebApp()) {
    const claim = await claimConnectionHubDataBusToken();
    const token = String(claim.federated_token || '').trim();
    if (!token) throw new Error('Federated Data Bus token was not issued.');
    const sessionId = String(claim.session_id || '').trim();
    if (!sessionId) throw new Error('Federated Data Bus claim did not return a session_id.');
    const bundleId = String(claim.bundle_id || settings.getConnectionHubBundleId()).trim();
    return {
      key: [
        settings.getBaseUrl(),
        settings.getTenant(),
        settings.getProject(),
        bundleId,
        sessionId,
      ].join('|'),
      sessionId,
      auth: {
        tenant: settings.getTenant(),
        project: settings.getProject(),
        bundle_id: bundleId,
        federated_token: token,
      },
    };
  }

  const sessionId = await fetchProfileSessionId();
  const accessToken = settings.getAccessToken();
  const idToken = settings.getIdToken();
  const bundleId = settings.getBundleId();
  return {
    key: [
      settings.getBaseUrl(),
      settings.getTenant(),
      settings.getProject(),
      bundleId,
      sessionId,
      'browser',
    ].join('|'),
    sessionId,
    auth: {
      tenant: settings.getTenant(),
      project: settings.getProject(),
      bundle_id: bundleId,
      user_session_id: sessionId,
      ...(accessToken ? { bearer_token: accessToken } : {}),
      ...(idToken ? { id_token: idToken } : {}),
    },
  };
}

function unwrapConnectionHubClaim(parsed: unknown): FederatedClaimPayload {
  if (parsed && typeof parsed === 'object' && 'federated_data_bus_claim' in parsed) {
    return (parsed as Record<string, unknown>).federated_data_bus_claim as FederatedClaimPayload;
  }
  return parsed as FederatedClaimPayload;
}

async function claimConnectionHubDataBusToken(): Promise<FederatedClaimPayload> {
  const tenant = encodeURIComponent(settings.getTenant());
  const project = encodeURIComponent(settings.getProject());
  const bundleId = encodeURIComponent(settings.getConnectionHubBundleId());
  const url = `${settings.getBaseUrl()}/api/integrations/bundles/${tenant}/${project}/${bundleId}/public/federated_data_bus_claim`;
  const headers = authHeaders({ Accept: 'application/json', 'Content-Type': 'application/json' });
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    cache: 'no-store',
    headers,
    body: JSON.stringify({ data: {} }),
  });
  const text = await response.text();
  let parsed: unknown = {};
  try {
    parsed = text ? JSON.parse(text) : {};
  } catch {
    parsed = { raw: text };
  }
  if (!response.ok) {
    const detail = typeof parsed === 'object' && parsed && 'detail' in parsed
      ? String((parsed as Record<string, unknown>).detail)
      : text || response.statusText;
    throw new Error(detail || `Connection Hub Data Bus claim failed: ${response.status}`);
  }
  const claim = unwrapConnectionHubClaim(parsed);
  if (claim?.ok === false) {
    throw new Error(claim.message || claim.error || 'Connection Hub Data Bus claim failed');
  }
  return claim;
}

function resetDataBusSocket(socket: Socket): void {
  if (dataBusSocket === socket) {
    dataBusSocket = null;
    dataBusSocketKey = '';
    dataBusSessionId = '';
    dataBusConnectPromise = null;
  }
}

function createSocket(auth: Record<string, unknown>): Socket {
  const socket = io(settings.getBaseUrl(), {
    path: '/socket.io',
    transports: ['websocket'],
    upgrade: false,
    withCredentials: true,
    autoConnect: false,
    auth,
    reconnectionAttempts: 0,
  });
  socket.on('connect_error', (error: Error) => {
    console.warn('[telegram-miniapp:data-bus] connect_error', { message: error.message });
  });
  socket.on('disconnect', (reason: string) => {
    console.info('[telegram-miniapp:data-bus] disconnected', { reason });
  });
  return socket;
}

function ensureSocketConnected(socket: Socket): Promise<void> {
  if (socket.connected) return Promise.resolve();
  if (dataBusConnectPromise) return dataBusConnectPromise;
  dataBusConnectPromise = new Promise<void>((resolve, reject) => {
    let timeout: number | undefined;
    const cleanup = () => {
      if (timeout !== undefined) window.clearTimeout(timeout);
      socket.off('connect', onConnect);
      socket.off('connect_error', onConnectError);
    };
    function onConnect() {
      cleanup();
      resolve();
    }
    function onConnectError(error: unknown) {
      cleanup();
      socket.disconnect();
      resetDataBusSocket(socket);
      reject(error instanceof Error ? error : new Error(String(error)));
    }

    timeout = window.setTimeout(() => {
      cleanup();
      socket.disconnect();
      resetDataBusSocket(socket);
      reject(new Error('Timed out connecting to Socket.IO.'));
    }, 8000);
    socket.once('connect', onConnect);
    socket.once('connect_error', onConnectError);
    socket.connect();
  }).finally(() => {
    dataBusConnectPromise = null;
  });
  return dataBusConnectPromise;
}

async function dataBusSocketFor(): Promise<{ socket: Socket; sessionId: string }> {
  const context = await buildSocketContext();
  if (dataBusSocket && dataBusSocketKey === context.key) {
    dataBusSocket.auth = context.auth;
    dataBusSessionId = context.sessionId;
    await ensureSocketConnected(dataBusSocket);
    return { socket: dataBusSocket, sessionId: context.sessionId };
  }
  if (dataBusSocket) {
    dataBusSocket.disconnect();
    resetDataBusSocket(dataBusSocket);
  }
  const socket = createSocket(context.auth);
  dataBusSocket = socket;
  dataBusSocketKey = context.key;
  dataBusSessionId = context.sessionId;
  await ensureSocketConnected(socket);
  return { socket, sessionId: context.sessionId };
}

export async function getDataBusSessionId(): Promise<string> {
  const { sessionId } = await dataBusSocketFor();
  return sessionId;
}

export async function reconnectDataBus(): Promise<string> {
  if (dataBusSocket) {
    dataBusSocket.disconnect();
    resetDataBusSocket(dataBusSocket);
  }
  const { sessionId } = await dataBusSocketFor();
  return sessionId;
}

export function subscribeDataBusServiceEvents(
  onEvent: (envelope: DataBusServiceEnvelope) => void,
  onError?: (error: Error) => void,
): () => void {
  let closed = false;
  let subscribedSocket: Socket | null = null;
  const onService = (payload: unknown) => {
    if (!payload || typeof payload !== 'object') return;
    onEvent(payload as DataBusServiceEnvelope);
  };

  void (async () => {
    try {
      const { socket } = await dataBusSocketFor();
      if (closed) return;
      subscribedSocket = socket;
      socket.on('chat_service', onService);
    } catch (error) {
      if (closed) return;
      onError?.(error instanceof Error ? error : new Error(String(error)));
    }
  })();

  return () => {
    closed = true;
    if (subscribedSocket) {
      subscribedSocket.off('chat_service', onService);
    }
  };
}
