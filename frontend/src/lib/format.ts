const SECONDS_PER_MINUTE = 60

export function formatDuration(totalSeconds: number): string {
  const safe = !Number.isFinite(totalSeconds) || totalSeconds < 0 ? 0 : totalSeconds
  const minutes = Math.floor(safe / SECONDS_PER_MINUTE)
  const seconds = safe % SECONDS_PER_MINUTE
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

export function prettyAgentName(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase())
}

export function formatNaira(value: number): string {
  const safe = !Number.isFinite(value) ? 0 : value
  return new Intl.NumberFormat('en-NG', {
    style: 'currency',
    currency: 'NGN',
    maximumFractionDigits: 0,
  }).format(safe)
}

export function formatPercent(rate: number): string {
  const safe = !Number.isFinite(rate) ? 0 : rate
  return `${(safe * 100).toFixed(1)}%`
}

export function formatCompactNumber(value: number): string {
  if (!Number.isFinite(value)) return '0'
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return String(value)
}
