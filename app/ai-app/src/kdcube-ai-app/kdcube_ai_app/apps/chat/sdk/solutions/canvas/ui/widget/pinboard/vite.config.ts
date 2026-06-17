import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const require = createRequire(import.meta.url)

// The Pin Board widget hosts the canvas component — the npm
// @kdcube/components-react/canvas package (same component the multi-widget scene
// mounts). The bundle build materializes it under `_shared/`; a plain-checkout
// fallback walks up to the workspace npm/packages. (Files sit at the package root.)
function findInWorkspace(start: string, rel: string): string {
  let dir = start
  for (let i = 0; i < 16; i++) {
    const candidate = path.resolve(dir, rel)
    if (fs.existsSync(candidate)) return candidate
    const parent = path.resolve(dir, '..')
    if (parent === dir) break
    dir = parent
  }
  return ''
}

const materializedComponentsReactCanvas = path.resolve(__dirname, '_shared/components-react/canvas/index.ts')
const envCanvasComponent = process.env.KDCUBE_CANVAS_COMPONENT_SRC
  ? path.resolve(process.env.KDCUBE_CANVAS_COMPONENT_SRC)
  : ''
const repoCanvasComponent = findInWorkspace(__dirname, 'npm/packages/components-react/src/canvas/index.ts')

const canvasComponentEntry = fs.existsSync(materializedComponentsReactCanvas)
  ? materializedComponentsReactCanvas
  : envCanvasComponent || repoCanvasComponent

// Canvas core logic (@kdcube/components-core/canvas — model/ingress/types), imported by
// the canvas component. Same materialized-_shared + workspace-fallback resolution.
const materializedComponentsCoreCanvas = path.resolve(__dirname, '_shared/components-core/canvas/index.ts')
const componentsCoreCanvasEntry = fs.existsSync(materializedComponentsCoreCanvas)
  ? materializedComponentsCoreCanvas
  : findInWorkspace(__dirname, 'npm/packages/components-core/src/canvas/index.ts')

// The board's stylesheet lives next to the component entry. Resolve the
// component stylesheet subpath explicitly (the entry
// alias points at `index.ts`, so a subpath import would not find it) and
// keep it ahead of the general alias so the more specific match wins.
const canvasComponentCss = path.resolve(path.dirname(canvasComponentEntry), 'canvasBoard.css')

export default defineConfig({
  plugins: [react()],
  base: './',
  resolve: {
    alias: [
      { find: '@kdcube/components-react/canvas/canvasBoard.css', replacement: canvasComponentCss },
      { find: '@kdcube/components-react/canvas', replacement: canvasComponentEntry },
      { find: '@kdcube/components-core/canvas', replacement: componentsCoreCanvasEntry },
      { find: 'lucide-react', replacement: require.resolve('lucide-react') },
      { find: /^react$/, replacement: require.resolve('react') },
      { find: /^react-dom$/, replacement: require.resolve('react-dom') },
      { find: /^react-dom\/client$/, replacement: require.resolve('react-dom/client') },
      { find: /^react\/jsx-runtime$/, replacement: require.resolve('react/jsx-runtime') },
    ],
  },
  build: {
    outDir: process.env.OUTDIR || process.env.VITE_BUILD_DEST_ABSOLUTE_PATH || 'dist',
    emptyOutDir: true,
  },
})
