import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * The chat widget builds the npm `@kdcube/components-*` chat (package `<Chat/>` +
 * the framework-agnostic engine + iframe host-bridge). There is no in-tree engine
 * or UI anymore.
 *
 * The `@kdcube/*` packages resolve to the package `src` trees that the bundle build
 * materializes next to this config under `_shared/` (via the widget's `npm://`
 * `shared_sources`). A plain-checkout fallback walks up to the workspace
 * `npm/packages` so `npm run build` works without the bundle pipeline.
 */
function findWorkspacePackages(start: string): string | null {
  let dir = start
  for (let i = 0; i < 12; i++) {
    const candidate = resolve(dir, 'npm', 'packages')
    if (existsSync(candidate)) return candidate
    const parent = resolve(dir, '..')
    if (parent === dir) break
    dir = parent
  }
  return null
}

function pkgSrc(materializedName: string, packageName: string): string {
  const shared = resolve(__dirname, '_shared', materializedName)
  if (existsSync(shared)) return shared
  const workspace = findWorkspacePackages(__dirname)
  if (workspace) return resolve(workspace, packageName, 'src')
  // Last resort: the materialized path (vite reports a clear missing-alias error).
  return shared
}

const CORE = pkgSrc('components_core', 'components-core')
const REACT = pkgSrc('components_react', 'components-react')

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: './',
  resolve: {
    alias: [
      { find: '@kdcube/components-react/chat', replacement: resolve(REACT, 'chat') },
      { find: '@kdcube/components-react', replacement: REACT },
      { find: '@kdcube/components-core/chat', replacement: resolve(CORE, 'chat') },
      { find: '@kdcube/components-core', replacement: CORE },
    ],
    // The materialized package source imports react / redux as bare specifiers;
    // dedupe so they bind to the widget's single copy, not a nested one.
    dedupe: ['react', 'react-dom', 'react-redux', '@reduxjs/toolkit'],
  },
  build: {
    outDir: process.env.OUTDIR || 'dist',
    emptyOutDir: true,
  },
  // Build-impl marker, surfaced on <html data-kdcube-chat-impl> + console by main.tsx.
  define: {
    __KDCUBE_CHAT_IMPL__: JSON.stringify('package-ui'),
  },
})
