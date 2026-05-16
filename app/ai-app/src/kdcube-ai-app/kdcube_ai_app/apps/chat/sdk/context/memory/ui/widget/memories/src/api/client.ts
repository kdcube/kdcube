import { settings } from './settings';

export type MemoryWidgetCallOperation = <T>(
  operation: string,
  payload?: Record<string, unknown>,
) => Promise<T>;

let hostCallOperation: MemoryWidgetCallOperation | null = null;

export function setMemoryWidgetCallOperation(callOperation: MemoryWidgetCallOperation): () => void {
  hostCallOperation = callOperation;
  return () => {
    if (hostCallOperation === callOperation) hostCallOperation = null;
  };
}

function telegramInitData(): string {
  const own = (window as unknown as { Telegram?: { WebApp?: { initData?: string } } }).Telegram?.WebApp?.initData || '';
  if (own) return own;
  try {
    return (window.parent as unknown as { Telegram?: { WebApp?: { initData?: string } } }).Telegram?.WebApp?.initData || '';
  } catch {
    return '';
  }
}

function telegramOperationAlias(operation: string): string {
  return operation.startsWith('memories_widget_') ? `telegram_${operation}` : '';
}

function useTelegramPublicBridge(operation: string): boolean {
  return Boolean(telegramOperationAlias(operation)) && (
    settings.getWidgetAlias() === 'telegram_memories' || telegramInitData().length > 0
  );
}

function operationUrl(operation: string): string {
  const tenant = encodeURIComponent(settings.getTenant());
  const project = encodeURIComponent(settings.getProject());
  const bundleId = encodeURIComponent(settings.getBundleId());
  if (useTelegramPublicBridge(operation)) {
    const alias = telegramOperationAlias(operation);
    return `${settings.getBaseUrl()}/api/integrations/bundles/${tenant}/${project}/${bundleId}/public/${alias}`;
  }
  return `${settings.getBaseUrl()}/api/integrations/bundles/${tenant}/${project}/${bundleId}/operations/${operation}`;
}

export async function callOperation<T>(operation: string, payload: Record<string, unknown> = {}): Promise<T> {
  if (hostCallOperation) {
    return hostCallOperation<T>(operation, payload);
  }
  const headers = settings.authHeaders({ 'Content-Type': 'application/json' });
  const initData = telegramInitData();
  if (initData) headers.set('X-Telegram-Init-Data', initData);
  const response = await fetch(operationUrl(operation), {
    method: 'POST',
    credentials: 'include',
    headers,
    body: JSON.stringify({ data: payload }),
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
    throw new Error(detail);
  }
  const telegramAlias = telegramOperationAlias(operation);
  if (telegramAlias && parsed && typeof parsed === 'object' && telegramAlias in parsed) {
    return (parsed as Record<string, unknown>)[telegramAlias] as T;
  }
  if (parsed && typeof parsed === 'object' && operation in parsed) {
    return (parsed as Record<string, unknown>)[operation] as T;
  }
  return parsed as T;
}
