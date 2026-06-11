import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const require = createRequire(import.meta.url)

// The Pin Board widget hosts the canvas component (the same component the
// multi-widget scene mounts). Resolve it the way the scene does: prefer a
// materialized copy the bundle build may stage under `_shared/`, then an
// explicit env override, then the in-repo source two directories up.
const materializedCanvasComponent = path.resolve(__dirname, '_shared/canvas-component/src/index.ts')
const envCanvasComponent = process.env.KDCUBE_CANVAS_COMPONENT_SRC
  ? path.resolve(process.env.KDCUBE_CANVAS_COMPONENT_SRC)
  : ''
const repoCanvasComponent = path.resolve(__dirname, '../../component/src/index.ts')

const canvasComponentEntry = fs.existsSync(materializedCanvasComponent)
  ? materializedCanvasComponent
  : envCanvasComponent || repoCanvasComponent

// The board's stylesheet lives next to the component entry. Resolve the
// `@kdcube/canvas-component/canvasBoard.css` subpath explicitly (the entry
// alias points at `index.ts`, so a subpath import would not find it) and
// keep it ahead of the general alias so the more specific match wins.
const canvasComponentCss = path.resolve(path.dirname(canvasComponentEntry), 'canvasBoard.css')

export default defineConfig({
  plugins: [react()],
  base: './',
  resolve: {
    alias: [
      { find: '@kdcube/canvas-component/canvasBoard.css', replacement: canvasComponentCss },
      { find: '@kdcube/canvas-component', replacement: canvasComponentEntry },
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
