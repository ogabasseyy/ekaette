import { useMemo, useState, useTransition, type KeyboardEvent } from 'react'
import { cn } from '../../lib/utils'

interface TextInputProps {
  connected: boolean
  onSend: (text: string) => void
}

export function TextInput({ connected, onSend }: TextInputProps) {
  const [draft, setDraft] = useState('')
  const [isPending, startTransition] = useTransition()

  const canSend = useMemo(
    () => connected && draft.trim().length > 0 && !isPending,
    [connected, draft, isPending],
  )

  const handleSend = () => {
    const text = draft.trim()
    if (!text || !connected) return
    onSend(text)
    setDraft('')
  }

  const handleChange = (next: string) => {
    startTransition(() => {
      setDraft(next)
    })
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter') return
    event.preventDefault()
    handleSend()
  }

  return (
    <div className="flex min-w-0 flex-1 items-center gap-2 rounded-2xl border border-border/80 bg-black/35 px-2.5 py-2 sm:px-3">
      <input
        value={draft}
        onChange={event => handleChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={
          connected
            ? 'Type a message and press Enter...'
            : 'Connect call to send text'
        }
        className="w-full min-w-0 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
      />
      <button
        onClick={handleSend}
        disabled={!canSend}
        className={cn(
          'shrink-0 rounded-xl border border-primary/40 bg-primary/15 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.12em] text-primary transition hover:bg-primary/25',
          'disabled:cursor-not-allowed disabled:opacity-50',
        )}
      >
        Send
      </button>
    </div>
  )
}
