import { io, type Socket } from 'socket.io-client';
import { callOperation } from './apiClient';
import { settings } from './settings';
import { isTelegramWebApp } from '../telegram/utils';

const ECHO_SUBJECT = 'versatile.echo';

interface FederatedClaimPayload {
  ok?: boolean;
  federated_token?: string;
  session_id?: string;
  expires_at?: number;
  bundle_id?: string;
  allowed_subjects?: string[];
}

interface ProfilePayload {
  session_id?: string;
}

export interface DataBusEchoOutcome {
  ack: Record<string, unknown>;
  event?: Record<string, unknown>;
  messageId: string;
}

function createLocalId(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

function authHeaders(base?: HeadersInit): Headers {
  const headers = new Headers(base);
  const accessToken = settings.getAccessToken();
  const idToken = settings.getIdToken();
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
  if (idToken) headers.set(settings.getIdTokenHeader(), idToken);
  return headers;
}

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

async function buildSocketAuth(): Promise<Record<string, unknown>> {
  const baseAuth: Record<string, unknown> = {
    tenant: settings.getTenant(),
    project: settings.getProject(),
    bundle_id: settings.getBundleId(),
  };

  if (isTelegramWebApp()) {
    const claim = await callOperation<FederatedClaimPayload>('federated_data_bus_claim', {});
    const token = String(claim.federated_token || '').trim();
    if (!token) throw new Error('Federated Data Bus token was not issued.');
    return {
      ...baseAuth,
      federated_token: token,
    };
  }

  const sessionId = await fetchProfileSessionId();
  const accessToken = settings.getAccessToken();
  const idToken = settings.getIdToken();
  return {
    ...baseAuth,
    user_session_id: sessionId,
    ...(accessToken ? { bearer_token: accessToken } : {}),
    ...(idToken ? { id_token: idToken } : {}),
  };
}

function connectSocket(auth: Record<string, unknown>): Promise<Socket> {
  const socket = io(settings.getBaseUrl(), {
    path: '/socket.io',
    transports: ['websocket', 'polling'],
    upgrade: false,
    withCredentials: true,
    auth,
    reconnectionAttempts: 0,
  });

  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      socket.disconnect();
      reject(new Error('Timed out connecting to Socket.IO.'));
    }, 8000);

    socket.once('connect', () => {
      window.clearTimeout(timeout);
      resolve(socket);
    });
    socket.once('connect_error', (error: unknown) => {
      window.clearTimeout(timeout);
      socket.disconnect();
      reject(error instanceof Error ? error : new Error(String(error)));
    });
  });
}

function waitForEchoEvent(socket: Socket, messageId: string): Promise<Record<string, unknown> | undefined> {
  return new Promise((resolve) => {
    const timeout = window.setTimeout(() => {
      socket.off('chat_service', onServiceEvent);
      resolve(undefined);
    }, 8000);

    function onServiceEvent(payload: unknown) {
      if (!payload || typeof payload !== 'object') return;
      const envelope = payload as Record<string, unknown>;
      if (envelope.type !== 'kdcube.data_bus.result') return;
      const data = envelope.data;
      if (!data || typeof data !== 'object') return;
      if ((data as Record<string, unknown>).message_id !== messageId) return;
      window.clearTimeout(timeout);
      socket.off('chat_service', onServiceEvent);
      resolve(envelope);
    }

    socket.on('chat_service', onServiceEvent);
  });
}

export async function sendDataBusEcho(payload: Record<string, unknown>): Promise<DataBusEchoOutcome> {
  const auth = await buildSocketAuth();
  const socket = await connectSocket(auth);
  const messageId = createLocalId('dbmsg');
  const result = waitForEchoEvent(socket, messageId);

  try {
    const ack = await socket.timeout(8000).emitWithAck('data_bus.publish', {
      schema: 'kdcube.data_bus.ingress.v1',
      bundle_id: settings.getBundleId(),
      messages: [
        {
          message_id: messageId,
          subject: ECHO_SUBJECT,
          object_ref: 'probe:memory',
          idempotency_key: createLocalId('echo'),
          payload,
          client: {
            widget: 'versatile_webapp',
            source: 'memory_page',
          },
        },
      ],
    });
    const ackObject = ack as Record<string, unknown>;
    const status = String(ackObject.status || '');
    if (status !== 'accepted' && status !== 'partial') {
      const rejected = Array.isArray(ackObject.rejected) ? ackObject.rejected : [];
      const first = rejected[0] as Record<string, unknown> | undefined;
      throw new Error(String(first?.error || `Data Bus publish rejected: ${status || 'unknown'}`));
    }
    const event = await result;
    return { ack: ackObject, event, messageId };
  } finally {
    socket.disconnect();
  }
}
