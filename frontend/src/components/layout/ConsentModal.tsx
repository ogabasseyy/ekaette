import { useEffect, useRef } from 'react'

interface ConsentModalProps {
  onAccept: () => void
  onDecline: () => void
}

export function ConsentModal({ onAccept, onDecline }: ConsentModalProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    dialogRef.current?.showModal()
    return () => dialogRef.current?.close()
  }, [])

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="consent-title"
      onClose={onDecline}
      className="panel-glass mx-4 w-full max-w-lg animate-slide-up bg-transparent px-6 py-6 backdrop:bg-black/60 backdrop:backdrop-blur-sm sm:px-8 sm:py-8"
    >
      <h2 id="consent-title" className="font-display text-lg text-white tracking-tight sm:text-xl">
        Data &amp; AI Usage Consent
      </h2>

      <ul className="mt-4 space-y-2 text-muted-foreground text-xs leading-relaxed sm:text-sm">
        <li className="flex gap-2">
          <span className="shrink-0 text-accent">&#x2022;</span>
          <span>
            We collect conversation data to improve service quality and AI model performance.
          </span>
        </li>
        <li className="flex gap-2">
          <span className="shrink-0 text-accent">&#x2022;</span>
          <span>
            Your interactions are processed by AI models. Responses are generated automatically and
            may not always be accurate.
          </span>
        </li>
        <li className="flex gap-2">
          <span className="shrink-0 text-accent">&#x2022;</span>
          <span>
            We do not share your data with third parties except as required to provide this service.
          </span>
        </li>
      </ul>

      <p className="mt-4 text-[0.65rem] text-muted sm:text-xs">
        By accepting, you agree to our{' '}
        <a
          href="/privacy.html"
          target="_blank"
          rel="noopener noreferrer"
          className="text-accent underline underline-offset-2 transition hover:text-accent-foreground"
        >
          Privacy Policy
        </a>
        .
      </p>

      <div className="mt-6 flex gap-3">
        <button
          type="button"
          onClick={onDecline}
          className="flex-1 rounded-full border border-border px-4 py-2 font-semibold text-muted-foreground text-xs uppercase tracking-widest transition hover:border-foreground hover:text-foreground sm:text-sm"
        >
          Decline
        </button>
        <button
          type="button"
          onClick={onAccept}
          className="flex-1 rounded-full border border-accent px-4 py-2 font-semibold text-accent text-xs uppercase tracking-widest transition hover:bg-accent/10 hover:text-accent-foreground sm:text-sm"
        >
          Accept
        </button>
      </div>
    </dialog>
  )
}
