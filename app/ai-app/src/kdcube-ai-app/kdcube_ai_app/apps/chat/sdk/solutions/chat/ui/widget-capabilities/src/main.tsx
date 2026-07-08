import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
// The kdcube chat stylesheet (aliased in vite.config): the picker's k-menu
// family, chips, and the expanded/page wrap rules.
import '@kdcube/chat-ui.css'

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
