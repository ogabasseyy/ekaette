import { cva } from 'class-variance-authority'
import { useMemo, useState } from 'react'
import { formatNaira } from '../../lib/format'
import { cn } from '../../lib/utils'

const conditionBadgeVariants = cva(
  'inline-flex rounded-full border px-2.5 py-1 font-semibold text-[0.65rem] uppercase tracking-[0.12em]',
  {
    variants: {
      condition: {
        Excellent: 'border-primary/40 bg-primary/15 text-primary',
        Good: 'border-info/35 bg-info/12 text-info',
        Fair: 'border-warning/45 bg-warning/16 text-warning',
        Poor: 'border-destructive/40 bg-destructive/14 text-destructive',
      },
    },
  },
)

interface ValuationCardProps {
  deviceName: string
  condition: 'Excellent' | 'Good' | 'Fair' | 'Poor'
  price: number
  currency: string
  details: string
  onAccept: () => void
  onDecline: () => void
  onCounterOffer: (value: number) => void
}

export default function ValuationCard({
  deviceName,
  condition,
  price,
  currency,
  details,
  onAccept,
  onDecline,
  onCounterOffer,
}: ValuationCardProps) {
  const [counterOffer, setCounterOffer] = useState(price)

  const formattedPrice = useMemo(() => {
    if (currency === 'NGN' || currency === '₦') {
      return formatNaira(price)
    }
    return `${currency} ${price.toLocaleString()}`
  }, [currency, price])

  return (
    <article className="animate-slide-up rounded-2xl border border-border/70 bg-card/65 p-4">
      <div className="flex items-center justify-between gap-3">
        <h4 className="font-display text-lg text-white">{deviceName}</h4>
        <span className={cn(conditionBadgeVariants({ condition }))}>{condition}</span>
      </div>
      <p className="mt-2 text-muted-foreground text-sm">{details}</p>
      <p className="mt-3 font-semibold text-2xl text-white">{formattedPrice}</p>

      <div className="mt-3 flex items-center gap-2">
        <label htmlFor="counter-offer" className="text-muted-foreground text-xs">
          Counter
        </label>
        <input
          id="counter-offer"
          type="number"
          value={counterOffer}
          onChange={event => setCounterOffer(Number(event.target.value || 0))}
          className="w-full rounded-xl border border-border/80 bg-black/35 px-3 py-2 text-foreground text-sm outline-none focus:border-primary/60"
        />
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onAccept}
          className="rounded-xl border border-primary/40 bg-primary/15 px-3 py-2 font-semibold text-primary text-xs uppercase tracking-[0.12em]"
        >
          Accept
        </button>
        <button
          type="button"
          onClick={() => onCounterOffer(counterOffer)}
          className="rounded-xl border border-warning/40 bg-warning/12 px-3 py-2 font-semibold text-warning text-xs uppercase tracking-[0.12em]"
        >
          Counter
        </button>
        <button
          type="button"
          onClick={onDecline}
          className="rounded-xl border border-destructive/40 bg-destructive/12 px-3 py-2 font-semibold text-destructive text-xs uppercase tracking-[0.12em]"
        >
          Decline
        </button>
      </div>
    </article>
  )
}
