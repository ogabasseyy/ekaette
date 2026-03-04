export function formatDuration(totalSeconds: number): string {
  const safeSeconds =
    Number.isFinite(totalSeconds) && totalSeconds >= 0 ? Math.floor(totalSeconds) : 0
  const minutes = Math.floor(safeSeconds / 60)
  const seconds = safeSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

export function prettyAgentName(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase())
}

function toFiniteOrZero(value: number): number {
  return Number.isFinite(value) ? value : 0
}

export function formatNaira(value: number): string {
  const safeValue = toFiniteOrZero(value)
  return new Intl.NumberFormat('en-NG', {
    style: 'currency',
    currency: 'NGN',
    maximumFractionDigits: 0,
  }).format(safeValue)
}

export function formatPercent(rate: number): string {
  const safeRate = toFiniteOrZero(rate)
  return `${(safeRate * 100).toFixed(1)}%`
}

export function formatCompactNumber(value: number): string {
  const safeValue = toFiniteOrZero(value)
  if (safeValue >= 1_000_000) return `${(safeValue / 1_000_000).toFixed(1)}M`
  if (safeValue >= 1_000) return `${(safeValue / 1_000).toFixed(1)}K`
  return String(safeValue)
}
