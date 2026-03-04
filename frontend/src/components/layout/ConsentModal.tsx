interface ConsentModalProps {
  onAccept: () => void
  onDecline: () => void
}

export function ConsentModal({ onAccept, onDecline }: ConsentModalProps) {
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="consent-title"
        className="panel-glass max-w-lg w-full mx-4 px-6 py-6 sm:px-8 sm:py-8 animate-slide-up"
      >
        <h2
          id="consent-title"
          className="font-display text-lg text-white tracking-tight sm:text-xl"
        >
          Data &amp; AI Usage Consent
        </h2>

        <ul className="mt-4 space-y-2 text-xs leading-relaxed text-muted-foreground sm:text-sm">
          <li className="flex gap-2">
            <span className="shrink-0 text-accent">&#x2022;</span>
            <span>
              We collect conversation data to improve service quality and AI model performance.
            </span>
          </li>
          <li className="flex gap-2">
            <span className="shrink-0 text-accent">&#x2022;</span>
            <span>
              Your interactions are processed by AI models. Responses are generated automatically
              and may not always be accurate.
            </span>
          </li>
          <li className="flex gap-2">
            <span className="shrink-0 text-accent">&#x2022;</span>
            <span>
              We do not share your data with third parties except as required to provide this
              service.
            </span>
          </li>
        </ul>

        <p className="mt-4 text-[0.65rem] text-muted sm:text-xs">
          By accepting, you agree to our{' '}
          <a
            href="/privacy.html"
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent underline underline-offset-2 hover:text-accent-foreground transition"
          >
            Privacy Policy
          </a>
          .
        </p>

        <div className="mt-6 flex gap-3">
          <button
            type="button"
            onClick={onDecline}
            className="flex-1 rounded-full border border-border px-4 py-2 text-xs font-semibold uppercase tracking-widest text-muted-foreground transition hover:border-foreground hover:text-foreground sm:text-sm"
          >
            Decline
          </button>
          <button
            type="button"
            onClick={onAccept}
            className="flex-1 rounded-full border border-accent px-4 py-2 text-xs font-semibold uppercase tracking-widest text-accent transition hover:bg-accent/10 hover:text-accent-foreground sm:text-sm"
          >
            Accept
          </button>
        </div>
      </div>
    </div>
  )
}
