import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { AuthProvider } from './context/AuthContext'
import { CreditProvider } from './context/CreditContext'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider>
      <CreditProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </CreditProvider>
    </AuthProvider>
  </StrictMode>,
)
