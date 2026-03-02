import { cn } from '../../lib/utils'

interface RateBarProps {
  rate: number
  colorClass?: string
  className?: string
}

export function RateBar({ rate, colorClass = 'bg-primary', className }: RateBarProps) {
  const clampedRate = Math.max(0, Math.min(1, rate))
  return (
    <div className={cn('rate-bar-track', className)}>
      <div
        className={cn('rate-bar-fill', colorClass)}
        style={{ width: `${clampedRate * 100}%` }}
      />
    </div>
  )
}
