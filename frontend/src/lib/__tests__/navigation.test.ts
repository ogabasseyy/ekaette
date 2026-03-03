import { beforeEach, describe, expect, it, vi } from 'vitest'

describe('navigation', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  describe('currentPage', () => {
    it('returns "voice" for root path', async () => {
      Object.defineProperty(window, 'location', {
        value: { pathname: '/' },
        writable: true,
      })
      const { currentPage } = await import('../navigation')
      expect(currentPage()).toBe('voice')
    })

    it('returns "admin" for /admin path', async () => {
      Object.defineProperty(window, 'location', {
        value: { pathname: '/admin' },
        writable: true,
      })
      const mod = await import('../navigation')
      expect(mod.currentPage()).toBe('admin')
    })

    it('returns "admin" for /admin/settings subpath', async () => {
      Object.defineProperty(window, 'location', {
        value: { pathname: '/admin/settings' },
        writable: true,
      })
      const mod = await import('../navigation')
      expect(mod.currentPage()).toBe('admin')
    })

    it('returns "analytics" for /analytics path', async () => {
      Object.defineProperty(window, 'location', {
        value: { pathname: '/analytics' },
        writable: true,
      })
      const mod = await import('../navigation')
      expect(mod.currentPage()).toBe('analytics')
    })

    it('returns "marketing" for /marketing path', async () => {
      Object.defineProperty(window, 'location', {
        value: { pathname: '/marketing' },
        writable: true,
      })
      const mod = await import('../navigation')
      expect(mod.currentPage()).toBe('marketing')
    })

    it('returns "voice" for unknown path', async () => {
      Object.defineProperty(window, 'location', {
        value: { pathname: '/unknown' },
        writable: true,
      })
      const mod = await import('../navigation')
      expect(mod.currentPage()).toBe('voice')
    })

    it('returns "voice" for non-matching prefix path like /administrator', async () => {
      Object.defineProperty(window, 'location', {
        value: { pathname: '/administrator' },
        writable: true,
      })
      const mod = await import('../navigation')
      expect(mod.currentPage()).toBe('voice')
    })

    it('returns "voice" for non-matching prefix path like /analytics-extra', async () => {
      Object.defineProperty(window, 'location', {
        value: { pathname: '/analytics-extra' },
        writable: true,
      })
      const mod = await import('../navigation')
      expect(mod.currentPage()).toBe('voice')
    })
  })

  describe('NAV_ITEMS', () => {
    it('has exactly 4 entries with correct pages', async () => {
      const { NAV_ITEMS } = await import('../navigation')
      expect(NAV_ITEMS).toHaveLength(4)
      expect(NAV_ITEMS.map(item => item.page)).toEqual(['voice', 'analytics', 'marketing', 'admin'])
    })

    it('each item has label and iconName', async () => {
      const { NAV_ITEMS } = await import('../navigation')
      for (const item of NAV_ITEMS) {
        expect(item.label).toBeTruthy()
        expect(item.iconName).toBeTruthy()
      }
    })
  })

  describe('navigateTo', () => {
    it('navigates to / for voice', async () => {
      const assignMock = vi.fn()
      Object.defineProperty(window, 'location', {
        value: { assign: assignMock, pathname: '/' },
        writable: true,
      })
      const { navigateTo } = await import('../navigation')
      navigateTo('voice')
      expect(assignMock).toHaveBeenCalledWith('/')
    })

    it('navigates to /admin for admin', async () => {
      const assignMock = vi.fn()
      Object.defineProperty(window, 'location', {
        value: { assign: assignMock, pathname: '/' },
        writable: true,
      })
      const { navigateTo } = await import('../navigation')
      navigateTo('admin')
      expect(assignMock).toHaveBeenCalledWith('/admin')
    })

    it('navigates to /marketing for marketing', async () => {
      const assignMock = vi.fn()
      Object.defineProperty(window, 'location', {
        value: { assign: assignMock, pathname: '/' },
        writable: true,
      })
      const { navigateTo } = await import('../navigation')
      navigateTo('marketing')
      expect(assignMock).toHaveBeenCalledWith('/marketing')
    })

    it('navigates to /analytics for analytics', async () => {
      const assignMock = vi.fn()
      Object.defineProperty(window, 'location', {
        value: { assign: assignMock, pathname: '/' },
        writable: true,
      })
      const { navigateTo } = await import('../navigation')
      navigateTo('analytics')
      expect(assignMock).toHaveBeenCalledWith('/analytics')
    })
  })
})
