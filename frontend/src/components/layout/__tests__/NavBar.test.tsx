import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../lib/navigation', () => ({
  NAV_ITEMS: [
    { page: 'voice', label: 'Voice', iconName: 'Mic' },
    { page: 'analytics', label: 'Analytics', iconName: 'BarChart3' },
    { page: 'marketing', label: 'Marketing', iconName: 'Megaphone' },
    { page: 'admin', label: 'Admin', iconName: 'Settings' },
  ],
  navigateTo: vi.fn(),
}))

import { navigateTo } from '../../../lib/navigation'
import { NavBar } from '../NavBar'

describe('NavBar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders four nav items with correct labels', () => {
    render(<NavBar activePage="voice" />)
    expect(screen.getByText('Voice')).toBeInTheDocument()
    expect(screen.getByText('Analytics')).toBeInTheDocument()
    expect(screen.getByText('Marketing')).toBeInTheDocument()
    expect(screen.getByText('Admin')).toBeInTheDocument()
  })

  it('marks the active page tab with aria-current', () => {
    render(<NavBar activePage="analytics" />)
    const analyticsBtn = screen.getByRole('tab', { name: /analytics/i })
    expect(analyticsBtn).toHaveAttribute('aria-current', 'page')
    const voiceBtn = screen.getByRole('tab', { name: /voice/i })
    expect(voiceBtn).not.toHaveAttribute('aria-current', 'page')
  })

  it('calls navigateTo on click of inactive tab', async () => {
    const user = userEvent.setup()
    render(<NavBar activePage="voice" />)
    const adminBtn = screen.getByRole('tab', { name: /admin/i })
    await user.click(adminBtn)
    expect(navigateTo).toHaveBeenCalledWith('admin')
  })

  it('does not call navigateTo on click of active tab', async () => {
    const user = userEvent.setup()
    render(<NavBar activePage="voice" />)
    const voiceBtn = screen.getByRole('tab', { name: /voice/i })
    await user.click(voiceBtn)
    expect(navigateTo).not.toHaveBeenCalled()
  })

  it('renders a nav element with tablist role', () => {
    render(<NavBar activePage="voice" />)
    expect(screen.getByRole('tablist')).toBeInTheDocument()
  })

  it('applies active CSS class to active tab', () => {
    render(<NavBar activePage="admin" />)
    const adminBtn = screen.getByRole('tab', { name: /admin/i })
    expect(adminBtn.className).toContain('nav-tab-active')
  })

  it('triggers navigateTo on Enter key for inactive tab', async () => {
    const user = userEvent.setup()
    render(<NavBar activePage="voice" />)
    const adminBtn = screen.getByRole('tab', { name: /admin/i })
    adminBtn.focus()
    await user.keyboard('{Enter}')
    expect(navigateTo).toHaveBeenCalledWith('admin')
  })

  it('triggers navigateTo on Space key for inactive tab', async () => {
    const user = userEvent.setup()
    render(<NavBar activePage="voice" />)
    const analyticsBtn = screen.getByRole('tab', { name: /analytics/i })
    analyticsBtn.focus()
    await user.keyboard(' ')
    expect(navigateTo).toHaveBeenCalledWith('analytics')
  })
})
