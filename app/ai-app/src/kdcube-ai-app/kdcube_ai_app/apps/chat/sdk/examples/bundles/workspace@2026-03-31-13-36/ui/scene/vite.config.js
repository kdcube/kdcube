import react from '@vitejs/plugin-react'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const require = createRequire(import.meta.url)

function findInWorkspace(start, rel) {
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

// Canvas = the npm @kdcube/components-react/canvas package. The bundle build
// materializes it next to this config under _shared/; a plain-checkout fallback walks
// up to the workspace npm/packages. (Files sit at the package root — no src/ subdir.)
const materializedComponentsReactCanvas = path.resolve(__dirname, '_shared/components-react/canvas/index.ts')
const envCanvasComponent = process.env.KDCUBE_CANVAS_COMPONENT_SRC
  ? path.resolve(process.env.KDCUBE_CANVAS_COMPONENT_SRC)
  : ''
const repoCanvasComponent = findInWorkspace(__dirname, 'npm/packages/components-react/src/canvas/index.ts')
// Scene runtime = the npm @kdcube/components-core/scene package (files at the package
// root, no src/ subdir); materialized under _shared/ by the bundle build, workspace
// npm/packages fallback for plain checkouts.
const materializedComponentsCoreScene = path.resolve(__dirname, '_shared/components-core/scene/index.ts')
const envSceneRuntime = process.env.KDCUBE_SCENE_RUNTIME_SRC
  ? path.resolve(process.env.KDCUBE_SCENE_RUNTIME_SRC)
  : ''
const repoSceneRuntime = findInWorkspace(__dirname, 'npm/packages/components-core/src/scene/index.ts')
const materializedComponentsCoreEvents = path.resolve(__dirname, '_shared/components-core/events/index.ts')
const envEventsRuntime = process.env.KDCUBE_EVENTS_RUNTIME_SRC
  ? path.resolve(process.env.KDCUBE_EVENTS_RUNTIME_SRC)
  : ''
const repoEventsRuntime = findInWorkspace(__dirname, 'npm/packages/components-core/src/events/index.ts')

const canvasComponentEntry = fs.existsSync(materializedComponentsReactCanvas)
  ? materializedComponentsReactCanvas
  : envCanvasComponent || repoCanvasComponent
const sceneRuntimeEntry = fs.existsSync(materializedComponentsCoreScene)
  ? materializedComponentsCoreScene
  : envSceneRuntime || repoSceneRuntime
const eventsRuntimeEntry = fs.existsSync(materializedComponentsCoreEvents)
  ? materializedComponentsCoreEvents
  : envEventsRuntime || repoEventsRuntime
// Canvas core logic (@kdcube/components-core/canvas — model/ingress/types), imported by
// the canvas component. Same materialized-_shared + workspace-fallback resolution.
const materializedComponentsCoreCanvas = path.resolve(__dirname, '_shared/components-core/canvas/index.ts')
const componentsCoreCanvasEntry = fs.existsSync(materializedComponentsCoreCanvas)
  ? materializedComponentsCoreCanvas
  : findInWorkspace(__dirname, 'npm/packages/components-core/src/canvas/index.ts')
// Scene-host shell (@kdcube/components-react/scene — rail, window manager,
// registry, host plumbing, sceneHost.css). Same materialized-_shared +
// workspace-fallback resolution; the stylesheet resolves next to the entry.
const materializedComponentsReactScene = path.resolve(__dirname, '_shared/components-react/scene/index.ts')
const sceneShellEntry = fs.existsSync(materializedComponentsReactScene)
  ? materializedComponentsReactScene
  : findInWorkspace(__dirname, 'npm/packages/components-react/src/scene/index.ts')
const sceneShellCss = path.resolve(path.dirname(sceneShellEntry), 'sceneHost.css')
// The canvas stylesheet sits next to the component entry; resolve the subpath
// explicitly so the `@import '@kdcube/components-react/canvas/canvasBoard.css'` in
// styles.css finds it (the entry alias points at index.ts).
const canvasComponentCss = path.resolve(path.dirname(canvasComponentEntry), 'canvasBoard.css')

export default defineConfig({
  plugins: [react()],
  base: './',
  resolve: {
    alias: [
      { find: '@kdcube/components-react/canvas/canvasBoard.css', replacement: canvasComponentCss },
      { find: '@kdcube/components-react/canvas', replacement: canvasComponentEntry },
      { find: '@kdcube/components-react/scene/sceneHost.css', replacement: sceneShellCss },
      { find: '@kdcube/components-react/scene', replacement: sceneShellEntry },
      { find: '@kdcube/components-core/scene', replacement: sceneRuntimeEntry },
      { find: '@kdcube/components-core/events', replacement: eventsRuntimeEntry },
      { find: '@kdcube/components-core/canvas', replacement: componentsCoreCanvasEntry },
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
