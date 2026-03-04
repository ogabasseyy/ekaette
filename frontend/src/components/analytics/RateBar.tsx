import { cn } from '../../lib/utils'

interface RateBarProps {
  rate: number
  colorClass?: string
  className?: string
}

export function RateBar({ rate, colorClass = 'bg-primary', className }: RateBarProps) {
  const safeRate = Number.isFinite(rate) ? rate : 0
  const clampedRate = Math.max(0, Math.min(1, safeRate))
  return (
    <div className={cn('rate-bar-track', className)}>
      <div className={cn('rate-bar-fill', colorClass)} style={{ width: `${clampedRate * 100}%` }} />
    </div>
  )
}
