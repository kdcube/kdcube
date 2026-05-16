import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const materializedMemoryWidget = path.resolve(__dirname, '_shared/memory-widget/src/embed.tsx');
const sdkMemoryWidget = path.resolve(__dirname, '../../../../../../context/memory/ui/widget/memories/src/embed.tsx');
const memoryWidgetEntry = fs.existsSync(materializedMemoryWidget) ? materializedMemoryWidget : sdkMemoryWidget;
const materializedTelegramWidget = path.resolve(__dirname, '_shared/telegram-widget/src/index.tsx');
const sdkTelegramWidget = path.resolve(__dirname, '../../../../../../integrations/telegram/ui/widget.telegram/src/index.tsx');
const telegramWidgetEntry = fs.existsSync(materializedTelegramWidget) ? materializedTelegramWidget : sdkTelegramWidget;

export default defineConfig({
  plugins: [react()],
  base: './',
  resolve: {
    alias: {
      '@kdcube/memory-widget': memoryWidgetEntry,
      '@kdcube/telegram-widget': telegramWidgetEntry,
    },
  },
  build: {
    outDir: process.env.OUTDIR || 'dist',
    emptyOutDir: true,
  },
});
