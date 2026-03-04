import { X } from 'lucide-react'

interface AiDisclosureBannerProps {
  onDismiss: () => void
}

export function AiDisclosureBanner({ onDismiss }: AiDisclosureBannerProps) {
  return (
    <output
      aria-live="polite"
      className="flex items-center gap-2 rounded-xl border border-info/20 bg-info/5 px-3 py-2 text-info text-xs sm:text-sm"
    >
      <p className="flex-1">
        You are interacting with an AI assistant. Ask to speak with a human at any time.
      </p>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss AI disclosure"
        className="shrink-0 rounded-full p-1 transition hover:bg-info/10"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </output>
  )
}
