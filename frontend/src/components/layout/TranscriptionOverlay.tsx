import { useEffect, useRef } from 'react'
import { cn } from '../../lib/utils'
import type { TranscriptMessage } from '../../lib/transcript'

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
  }, [messages])

  return (
    <section className="panel-glass shrink-0 flex min-h-[12rem] max-h-[15rem] flex-col px-4 py-4 sm:min-h-[14rem] sm:max-h-[18rem] sm:px-5 lg:h-full lg:min-h-0 lg:max-h-none">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-display text-base text-white sm:text-xl">Live Transcript</h3>
        <span className="rounded-full border border-border/80 bg-card/40 px-2 py-1 text-[0.6rem] uppercase tracking-[0.14em] text-muted-foreground sm:text-[0.64rem] sm:tracking-[0.16em]">
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
            <p className="mt-1 text-[0.92rem] leading-relaxed text-foreground sm:text-sm">
              {message.text}
            </p>
          </article>
        ))}
        <div ref={bottomRef} />
      </div>
    </section>
  )
}
