import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { PlayerProvider } from './player'
import { SettingsProvider } from './state'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SettingsProvider>
      <PlayerProvider>
        <App />
      </PlayerProvider>
    </SettingsProvider>
  </StrictMode>,
)
