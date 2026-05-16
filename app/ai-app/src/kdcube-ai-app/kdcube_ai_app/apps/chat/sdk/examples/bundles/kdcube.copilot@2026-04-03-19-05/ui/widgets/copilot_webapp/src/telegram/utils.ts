export function telegramInitData(): string {
  return (window as unknown as { Telegram?: { WebApp?: { initData?: string } } }).Telegram?.WebApp?.initData || '';
}

export function isTelegramWebApp(): boolean {
  return telegramInitData().length > 0;
}

export function prepareTelegramWebApp(): void {
  if (!isTelegramWebApp()) return;
  const webApp = (window as unknown as { Telegram?: { WebApp?: { ready?: () => void; expand?: () => void } } }).Telegram?.WebApp;
  webApp?.ready?.();
  webApp?.expand?.();
}
