import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * The capability-picker widget builds the SAME picker the chat composer uses
 * (`CapabilityPickerPage` from `@kdcube/components-react/chat`) as a served
 * full-page widget. Package sources resolve exactly like the chat widget:
 * the bundle build materializes them under `_shared/` (npm:// shared_sources);
 * a plain-checkout fallback walks up to the workspace `npm/packages`.
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

/** The kdcube chat stylesheet (the standalone-example copy, kept in lockstep
 *  with the in-tree widget stylesheet by the style test rig). */
function chatUiCss(): string {
  const shared = resolve(__dirname, '_shared', 'chat_ui_css', 'chat-ui.css')
  if (existsSync(shared)) return shared
  const workspace = findWorkspacePackages(__dirname)
  if (workspace) return resolve(workspace, 'components-react', 'examples', 'standalone', 'chat-ui.css')
  return shared
}

const CORE = pkgSrc('components_core', 'components-core')
const REACT = pkgSrc('components_react', 'components-react')

export default defineConfig({
  plugins: [react()],
  base: './',
  resolve: {
    alias: [
      { find: '@kdcube/chat-ui.css', replacement: chatUiCss() },
      { find: '@kdcube/components-react/chat', replacement: resolve(REACT, 'chat') },
      { find: '@kdcube/components-react', replacement: REACT },
      { find: '@kdcube/components-core/chat', replacement: resolve(CORE, 'chat') },
      { find: '@kdcube/components-core', replacement: CORE },
    ],
    dedupe: ['react', 'react-dom', 'react-redux', '@reduxjs/toolkit'],
  },
  build: {
    outDir: process.env.OUTDIR || 'dist',
    emptyOutDir: true,
  },
})
