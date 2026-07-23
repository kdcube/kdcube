import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import '@kdcube/components-react/apps-config/styles/apps-config.css'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
