import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { ErrorBoundary } from './app/ErrorBoundary'
import { installClientErrorReporting } from './app/clientErrorReporter'
import { AuthProvider } from './context/AuthContext'
import { CreditProvider } from './context/CreditContext'
import './styles/fonts.css'
import './styles/tokens.css'
import './styles/base.css'

installClientErrorReporting()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <AuthProvider>
        <CreditProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </CreditProvider>
      </AuthProvider>
    </ErrorBoundary>
  </StrictMode>,
)
