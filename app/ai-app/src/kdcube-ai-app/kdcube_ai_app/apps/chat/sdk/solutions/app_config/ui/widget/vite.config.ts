import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * The app-config viewer built as a served widget. Package sources resolve like
 * the other package-ui widgets: the bundle build materializes them under
 * `_shared/` (npm:// shared_sources); a plain-checkout fallback walks up to the
 * workspace `npm/packages`.
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
  return shared
}

const CORE = pkgSrc('components-core', 'components-core')
const REACT = pkgSrc('components-react', 'components-react')

export default defineConfig({
  plugins: [react()],
  base: './',
  resolve: {
    alias: [
      { find: '@kdcube/components-react/apps-config', replacement: resolve(REACT, 'apps-config') },
      { find: '@kdcube/components-react', replacement: REACT },
      { find: '@kdcube/components-core/apps-config', replacement: resolve(CORE, 'apps-config') },
      { find: '@kdcube/components-core', replacement: CORE },
    ],
    dedupe: ['react', 'react-dom', 'react-redux', '@reduxjs/toolkit'],
  },
  build: {
    outDir: process.env.OUTDIR || 'dist',
    emptyOutDir: true,
  },
})
