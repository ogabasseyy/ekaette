import { type KeyboardEvent, useMemo, useState } from 'react'
import { cn } from '../../lib/utils'

interface TextInputProps {
  connected: boolean
  onSend: (text: string) => void
}

export function TextInput({ connected, onSend }: TextInputProps) {
  const [draft, setDraft] = useState('')

  const canSend = useMemo(() => connected && draft.trim().length > 0, [connected, draft])

  const handleSend = () => {
    const text = draft.trim()
    if (!text || !connected) return
    onSend(text)
    setDraft('')
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
        onChange={event => setDraft(event.target.value)}
        onKeyDown={handleKeyDown}
        disabled={!connected}
        aria-label="Message input"
        placeholder={connected ? 'Type a message and press Enter...' : 'Connect call to send text'}
        className="w-full min-w-0 bg-transparent text-foreground text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50"
      />
      <button
        type="button"
        onClick={handleSend}
        disabled={!canSend}
        className={cn(
          'shrink-0 rounded-xl border border-primary/40 bg-primary/15 px-3 py-1.5 font-semibold text-primary text-xs uppercase tracking-[0.12em] transition hover:bg-primary/25',
          'disabled:cursor-not-allowed disabled:opacity-50',
        )}
      >
        Send
      </button>
    </div>
  )
}
