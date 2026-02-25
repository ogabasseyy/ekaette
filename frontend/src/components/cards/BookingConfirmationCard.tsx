interface BookingConfirmationCardProps {
  confirmationId: string
  date: string
  time: string
  location: string
  service: string
}

export default function BookingConfirmationCard({
  confirmationId,
  date,
  time,
  location,
  service,
}: BookingConfirmationCardProps) {
  return (
    <article className="animate-slide-up rounded-2xl border border-border/70 bg-card/65 p-4">
      <p className="text-[0.64rem] text-primary uppercase tracking-[0.16em]">Booking Confirmed</p>
      <h4 className="mt-1 font-display text-lg text-white">#{confirmationId}</h4>

      <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
        <div>
          <dt className="text-muted-foreground">Date</dt>
          <dd className="text-foreground">{date}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Time</dt>
          <dd className="text-foreground">{time}</dd>
        </div>
        <div className="col-span-2">
          <dt className="text-muted-foreground">Location</dt>
          <dd className="text-foreground">{location}</dd>
        </div>
        <div className="col-span-2">
          <dt className="text-muted-foreground">Service</dt>
          <dd className="text-foreground">{service}</dd>
        </div>
      </dl>
    </article>
  )
}
