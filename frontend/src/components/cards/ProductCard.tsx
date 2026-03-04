import { cva } from 'class-variance-authority'
import { formatNaira } from '../../lib/format'
import { cn } from '../../lib/utils'

export const availabilityBadgeVariants = cva('rounded-full border px-2 py-0.5 text-[0.65rem]', {
  variants: {
    availability: {
      available: 'border-primary/40 bg-primary/15 text-primary',
      unavailable: 'border-destructive/40 bg-destructive/15 text-destructive',
    },
  },
  defaultVariants: {
    availability: 'available',
  },
})

interface ProductCardProps {
  name: string
  price: number
  currency: string
  available: boolean
  description: string
}

export default function ProductCard({
  name,
  price,
  currency,
  available,
  description,
}: ProductCardProps) {
  const safePrice = Number.isFinite(price) ? price : 0
  const displayPrice =
    currency === 'NGN' || currency === '₦'
      ? formatNaira(safePrice)
      : `${currency} ${safePrice.toLocaleString()}`

  return (
    <article className="animate-slide-up rounded-2xl border border-border/70 bg-card/65 p-4">
      <div className="flex items-start justify-between gap-3">
        <h4 className="font-medium text-white">{name}</h4>
        <span
          className={cn(
            availabilityBadgeVariants({
              availability: available ? 'available' : 'unavailable',
            }),
          )}
        >
          {available ? 'Available' : 'Out'}
        </span>
      </div>
      <p className="mt-2 text-muted-foreground text-sm">{description}</p>
      <p className="mt-3 font-semibold text-base text-foreground">{displayPrice}</p>
    </article>
  )
}
