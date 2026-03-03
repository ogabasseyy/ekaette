export type AppPage = 'voice' | 'admin' | 'analytics' | 'marketing'

const PAGE_PATHS: Record<AppPage, string> = {
  voice: '/',
  admin: '/admin',
  analytics: '/analytics',
  marketing: '/marketing',
}

export const NAV_ITEMS: ReadonlyArray<{
  page: AppPage
  label: string
  iconName: string
}> = [
  { page: 'voice', label: 'Voice', iconName: 'Mic' },
  { page: 'analytics', label: 'Analytics', iconName: 'BarChart3' },
  { page: 'marketing', label: 'Marketing', iconName: 'Megaphone' },
  { page: 'admin', label: 'Admin', iconName: 'Settings' },
]

export function currentPage(): AppPage {
  if (typeof window === 'undefined') return 'voice'
  const pathname = window.location.pathname
  if (pathname.startsWith('/admin')) return 'admin'
  if (pathname.startsWith('/analytics')) return 'analytics'
  if (pathname.startsWith('/marketing')) return 'marketing'
  return 'voice'
}

export function navigateTo(page: AppPage): void {
  const path = PAGE_PATHS[page]
  if (path === undefined) return
  window.location.assign(path)
}
