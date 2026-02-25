import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { TranscriptMessage } from '../../lib/transcript'
import { TranscriptionOverlay } from '../layout/TranscriptionOverlay'

describe('TranscriptionOverlay', () => {
  it('renders user right-aligned and agent left-aligned messages', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'user', text: 'Hi', partial: false },
      { type: 'transcription', role: 'agent', text: 'Hello', partial: false },
    ]

    render(<TranscriptionOverlay messages={messages} />)

    const userArticle = screen.getByText('Hi').closest('article')
    const agentArticle = screen.getByText('Hello').closest('article')
    expect(userArticle).toHaveClass('message-user')
    expect(agentArticle).toHaveClass('message-agent')
  })

  it('renders partial messages with partial class', () => {
    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'agent', text: 'Listening', partial: true },
    ]
    render(<TranscriptionOverlay messages={messages} />)
    const article = screen.getByText('Listening').closest('article')
    expect(article).toHaveClass('message-partial')
  })

  it('auto-scrolls when messages update', () => {
    const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(() => {})

    const messages: TranscriptMessage[] = [
      { type: 'transcription', role: 'agent', text: 'One', partial: false },
    ]
    const { rerender } = render(<TranscriptionOverlay messages={messages} />)
    rerender(
      <TranscriptionOverlay
        messages={[
          ...messages,
          { type: 'transcription', role: 'agent', text: 'Two', partial: false },
        ]}
      />,
    )
    expect(scrollSpy).toHaveBeenCalled()
    scrollSpy.mockRestore()
  })
})
