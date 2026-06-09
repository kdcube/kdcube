import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: process.env.OUTDIR || process.env.VITE_BUILD_DEST_ABSOLUTE_PATH || 'dist',
    emptyOutDir: true,
  },
});
