import { createRoot } from 'react-dom/client'
import './index.css'
// The widget mounts the npm @kdcube/components-* chat (package <Chat/> + engine +
// iframe host-bridge) via the package engine-root. There is no in-tree App/engine.
import { EngineRoot } from './app/packageUIRoot.tsx'

// Build-time stamp (set in vite.config) — visible in the running widget.
declare const __KDCUBE_CHAT_IMPL__: string
const chatImpl = typeof __KDCUBE_CHAT_IMPL__ !== 'undefined' ? __KDCUBE_CHAT_IMPL__ : 'package-ui'
document.documentElement.setAttribute('data-kdcube-chat-impl', chatImpl)
console.info(`[kdcube.chat] UI implementation = ${chatImpl}`)

createRoot(document.getElementById('root')!).render(<EngineRoot />)
