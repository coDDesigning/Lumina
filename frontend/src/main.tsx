import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { ErrorBoundary } from './app/ErrorBoundary'
import { ThemeProvider } from './app/ThemeProvider'
import { AuthProvider } from './context/AuthContext'
import { CreditProvider } from './context/CreditContext'
import { ToastProvider } from './ui/ToastProvider'
import './styles/fonts.css'
import './styles/tokens.css'
import './styles/base.css'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <ThemeProvider>
        <ToastProvider>
          <AuthProvider>
            <CreditProvider>
              <BrowserRouter>
                <App />
              </BrowserRouter>
            </CreditProvider>
          </AuthProvider>
        </ToastProvider>
      </ThemeProvider>
    </ErrorBoundary>
  </StrictMode>,
)
