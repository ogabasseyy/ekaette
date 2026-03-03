import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import { AdminDashboard } from './components/admin/AdminDashboard'
import { AnalyticsDashboard } from './components/analytics/AnalyticsDashboard'
import { MarketingDashboard } from './components/marketing/MarketingDashboard'

function RootPage() {
  const pathname = window.location.pathname
  if (pathname.startsWith('/admin')) return <AdminDashboard />
  if (pathname.startsWith('/analytics')) return <AnalyticsDashboard />
  if (pathname.startsWith('/marketing')) return <MarketingDashboard />
  // voice page is the default for all unmatched paths
  return <App />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RootPage />
  </StrictMode>,
)
