import { TelegramAdminPanel } from '@kdcube/telegram-widget';
import { callOperation } from '../store/apiClient';

export function TelegramAdminPage() {
  return <TelegramAdminPanel callOperation={callOperation} />;
}
