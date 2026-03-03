import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { VoicePanel } from '../layout/VoicePanel'

describe('VoicePanel', () => {
  it('renders timer value', () => {
    render(
      <VoicePanel
        title="Electronics Trade Desk"
        sessionId="demo-session"
        elapsedSeconds={65}
        isConnected={false}
        isStarting={false}
      />,
    )
    expect(screen.getByText('01:05')).toBeInTheDocument()
  })

  it('adds live pulse class when recording', () => {
    const { container } = render(
      <VoicePanel
        title="Electronics Trade Desk"
        sessionId="demo-session"
        elapsedSeconds={12}
        isConnected
        isStarting={false}
      />,
    )
    expect(container.querySelector('.voice-orb')?.className).toContain('is-live')
  })
})
