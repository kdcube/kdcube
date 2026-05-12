import './types';

export function telegramInitData(): string {
  return window.Telegram?.WebApp?.initData || '';
}

export function isTelegramWebApp(): boolean {
  return telegramInitData().length > 0;
}

export function prepareTelegramWebApp(): void {
  window.Telegram?.WebApp?.ready?.();
  window.Telegram?.WebApp?.expand?.();
}
