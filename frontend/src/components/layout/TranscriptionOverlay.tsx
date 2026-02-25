import { useEffect, useRef } from 'react'
import type { TranscriptMessage } from '../../lib/transcript'
import { cn } from '../../lib/utils'

interface TranscriptionOverlayProps {
  messages: TranscriptMessage[]
}

export function TranscriptionOverlay({ messages }: TranscriptionOverlayProps) {
  const transcriptRef = useRef<HTMLDivElement | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (bottomRef.current) {
      bottomRef.current.scrollIntoView({ block: 'end' })
    } else if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight
    }
  }, [])

  return (
    <section className="panel-glass flex max-h-[15rem] min-h-[12rem] shrink-0 flex-col px-4 py-4 sm:max-h-[18rem] sm:min-h-[14rem] sm:px-5 lg:h-full lg:max-h-none lg:min-h-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-display text-base text-white sm:text-xl">Live Transcript</h3>
        <span className="rounded-full border border-border/80 bg-card/40 px-2 py-1 text-[0.6rem] text-muted-foreground uppercase tracking-[0.14em] sm:text-[0.64rem] sm:tracking-[0.16em]">
          {messages.length} messages
        </span>
      </div>

      <div
        ref={transcriptRef}
        className="transcript-scroll mt-3 min-h-0 flex-1 space-y-3 overflow-y-auto pr-1 sm:mt-4"
      >
        {messages.length === 0 && (
          <div className="empty-transcript">
            <p className="text-base text-white">No live transcript yet.</p>
            <p className="mt-1 text-[0.95rem] text-muted-foreground sm:text-sm">
              Start a call and your conversation will stream here in real time.
            </p>
          </div>
        )}

        {messages.map((message, index) => (
          <article
            key={`${index}-${message.role}-${message.text.slice(0, 12)}`}
            className={cn(
              'message-bubble',
              message.role === 'user' ? 'message-user ml-auto' : 'message-agent mr-auto',
              message.partial && 'message-partial',
            )}
          >
            <p className="message-meta">
              {message.role === 'user' ? 'You' : 'Ekaette'}
              {message.partial ? ' • listening' : ''}
            </p>
            <p className="mt-1 text-[0.92rem] text-foreground leading-relaxed sm:text-sm">
              {message.text}
            </p>
          </article>
        ))}
        <div ref={bottomRef} />
      </div>
    </section>
  )
}
