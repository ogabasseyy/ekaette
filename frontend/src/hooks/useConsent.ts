import { useCallback, useState } from 'react'

const STORAGE_KEY = 'ekaette:privacy:consent'
const CONSENT_VERSION = '1.0'

interface ConsentRecord {
  accepted: boolean
  timestamp: string
  version: string
}

function readConsent(): boolean {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return false
    const parsed: ConsentRecord = JSON.parse(raw)
    return parsed.accepted === true
  } catch {
    return false
  }
}

export function useConsent() {
  const [hasConsented, setHasConsented] = useState(readConsent)

  const acceptConsent = useCallback(() => {
    const record: ConsentRecord = {
      accepted: true,
      timestamp: new Date().toISOString(),
      version: CONSENT_VERSION,
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(record))
    setHasConsented(true)
  }, [])

  const declineConsent = useCallback(() => {
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      // Ignore storage failures and still update in-memory consent state.
    }
    setHasConsented(false)
  }, [])

  return { hasConsented, acceptConsent, declineConsent } as const
}
