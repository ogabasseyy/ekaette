import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useConsent } from '../useConsent'

const STORAGE_KEY = 'ekaette:privacy:consent'

describe('useConsent', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('returns false when localStorage is empty', () => {
    const { result } = renderHook(() => useConsent())
    expect(result.current.hasConsented).toBe(false)
  })

  it('returns true when localStorage has valid consent JSON', () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ accepted: true, timestamp: '2026-03-01T00:00:00.000Z', version: '1.0' }),
    )
    const { result } = renderHook(() => useConsent())
    expect(result.current.hasConsented).toBe(true)
  })

  it('returns false and does not throw when localStorage has malformed JSON', () => {
    localStorage.setItem(STORAGE_KEY, '{not-valid-json')
    const { result } = renderHook(() => useConsent())
    expect(result.current.hasConsented).toBe(false)
  })

  it('acceptConsent sets localStorage JSON with accepted, timestamp, version and returns true', () => {
    const { result } = renderHook(() => useConsent())
    expect(result.current.hasConsented).toBe(false)

    act(() => {
      result.current.acceptConsent()
    })

    expect(result.current.hasConsented).toBe(true)

    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')
    expect(stored.accepted).toBe(true)
    expect(stored.version).toBe('1.0')
    expect(typeof stored.timestamp).toBe('string')
    // Verify it's a valid ISO 8601 timestamp
    expect(Number.isNaN(Date.parse(stored.timestamp))).toBe(false)
  })

  it('declineConsent does not set localStorage to accepted', () => {
    const { result } = renderHook(() => useConsent())

    act(() => {
      result.current.declineConsent()
    })

    expect(result.current.hasConsented).toBe(false)

    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const stored = JSON.parse(raw)
      expect(stored.accepted).not.toBe(true)
    }
  })

  it('gracefully falls back to false when localStorage.getItem throws (private browsing)', () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError')
    })
    const { result } = renderHook(() => useConsent())
    expect(result.current.hasConsented).toBe(false)
    spy.mockRestore()
  })

  it('still updates in-memory state when localStorage.setItem throws', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError')
    })
    const { result } = renderHook(() => useConsent())
    act(() => {
      result.current.acceptConsent()
    })
    // In-memory state should still be updated even if localStorage fails
    expect(result.current.hasConsented).toBe(true)
    spy.mockRestore()
  })
})
