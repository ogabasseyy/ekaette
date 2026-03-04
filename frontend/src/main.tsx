import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import { AdminDashboard } from './components/admin/AdminDashboard'
import { AnalyticsDashboard } from './components/analytics/AnalyticsDashboard'
import { MarketingDashboard } from './components/marketing/MarketingDashboard'

const pathname = window.location.pathname
const isAdminPath = pathname.startsWith('/admin')
const isAnalyticsPath = pathname.startsWith('/analytics')
const isMarketingPath = pathname.startsWith('/marketing')

function RootPage() {
  if (isAdminPath) return <AdminDashboard />
  if (isAnalyticsPath) return <AnalyticsDashboard />
  if (isMarketingPath) return <MarketingDashboard />
  return <App />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RootPage />
  </StrictMode>,
)
