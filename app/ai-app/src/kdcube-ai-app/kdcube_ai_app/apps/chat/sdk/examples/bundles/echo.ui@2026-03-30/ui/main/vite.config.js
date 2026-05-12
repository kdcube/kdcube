import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: './',
  build: {
    // OUTDIR is injected by BaseEntrypoint._ensure_ui_build() at build time.
    // Falls back to 'dist' for local development.
    outDir: process.env.OUTDIR || 'dist',
    emptyOutDir: true,
  },
})
