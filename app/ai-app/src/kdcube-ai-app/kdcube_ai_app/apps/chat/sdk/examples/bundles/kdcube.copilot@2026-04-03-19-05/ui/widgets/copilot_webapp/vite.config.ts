import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const materializedMemoryWidget = path.resolve(__dirname, '_shared/memory-widget/src/embed.tsx');
const sdkMemoryWidget = path.resolve(__dirname, '../../../../../../context/memory/ui/widget/memories/src/embed.tsx');
const memoryWidgetEntry = fs.existsSync(materializedMemoryWidget) ? materializedMemoryWidget : sdkMemoryWidget;

export default defineConfig({
  plugins: [react()],
  base: './',
  resolve: {
    alias: {
      '@kdcube/memory-widget': memoryWidgetEntry,
    },
  },
  build: {
    outDir: process.env.OUTDIR || 'dist',
    emptyOutDir: true,
  },
});
