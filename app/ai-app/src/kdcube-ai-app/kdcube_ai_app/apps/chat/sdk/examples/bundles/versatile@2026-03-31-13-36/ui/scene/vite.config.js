import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const require = createRequire(import.meta.url)

const materializedCanvasComponent = path.resolve(__dirname, '_shared/canvas-component/src/index.ts')
const envCanvasComponent = process.env.KDCUBE_CANVAS_COMPONENT_SRC
  ? path.resolve(process.env.KDCUBE_CANVAS_COMPONENT_SRC)
  : ''
const repoCanvasComponent = path.resolve(
  __dirname,
  '../../../../..',
  'solutions/canvas/ui/component/src/index.ts',
)
const materializedSceneRuntime = path.resolve(__dirname, '_shared/scene-runtime/src/index.ts')
const envSceneRuntime = process.env.KDCUBE_SCENE_RUNTIME_SRC
  ? path.resolve(process.env.KDCUBE_SCENE_RUNTIME_SRC)
  : ''
const repoSceneRuntime = path.resolve(
  __dirname,
  '../../../../..',
  'solutions/scene/src/index.ts',
)

const canvasComponentEntry = fs.existsSync(materializedCanvasComponent)
  ? materializedCanvasComponent
  : envCanvasComponent || repoCanvasComponent
const sceneRuntimeEntry = fs.existsSync(materializedSceneRuntime)
  ? materializedSceneRuntime
  : envSceneRuntime || repoSceneRuntime

export default defineConfig({
  plugins: [react()],
  base: './',
  resolve: {
    alias: [
      { find: '@kdcube/canvas-component', replacement: canvasComponentEntry },
      { find: '@kdcube/scene-runtime', replacement: sceneRuntimeEntry },
      { find: 'lucide-react', replacement: require.resolve('lucide-react') },
      { find: /^react$/, replacement: require.resolve('react') },
      { find: /^react-dom$/, replacement: require.resolve('react-dom') },
      { find: /^react-dom\/client$/, replacement: require.resolve('react-dom/client') },
      { find: /^react\/jsx-runtime$/, replacement: require.resolve('react/jsx-runtime') },
    ],
  },
  build: {
    outDir: process.env.OUTDIR || 'dist',
    emptyOutDir: true,
  },
})
