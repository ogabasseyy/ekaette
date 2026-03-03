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
    if (parsed.version !== CONSENT_VERSION) return false
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
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(record))
    } catch {
      // localStorage may throw in private browsing or when quota is exceeded.
    }
    setHasConsented(true)
  }, [])

  const declineConsent = useCallback(() => {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          accepted: false,
          timestamp: new Date().toISOString(),
          version: CONSENT_VERSION,
        }),
      )
    } catch {
      // localStorage may throw in private browsing or when quota is exceeded.
    }
    setHasConsented(false)
  }, [])

  return { hasConsented, acceptConsent, declineConsent } as const
}
