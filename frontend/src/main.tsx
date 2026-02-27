import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import { AdminDashboard } from './components/admin/AdminDashboard'

const isAdminPath = window.location.pathname.startsWith('/admin')

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {isAdminPath ? <AdminDashboard /> : <App />}
  </StrictMode>,
)
