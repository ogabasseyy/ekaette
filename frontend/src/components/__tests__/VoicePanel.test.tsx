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

  it('renders 00:00 for zero elapsed', () => {
    render(
      <VoicePanel
        title="Electronics Trade Desk"
        sessionId="demo-session"
        elapsedSeconds={0}
        isConnected={false}
        isStarting={false}
      />,
    )
    expect(screen.getByText('00:00')).toBeInTheDocument()
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

  it('adds is-warming and SYNC when isStarting and not connected', () => {
    const { container } = render(
      <VoicePanel
        title="Electronics Trade Desk"
        sessionId="demo-session"
        elapsedSeconds={0}
        isConnected={false}
        isStarting
      />,
    )
    expect(container.querySelector('.voice-orb')?.className).toContain('is-warming')
    expect(screen.getByText('SYNC')).toBeInTheDocument()
  })

  it('renders audioError status badge', () => {
    render(
      <VoicePanel
        title="Electronics Trade Desk"
        sessionId="demo-session"
        elapsedSeconds={0}
        isConnected={false}
        isStarting={false}
        audioError="Microphone access denied"
      />,
    )
    expect(screen.getByText(/Microphone access denied/)).toBeInTheDocument()
  })

  it('renders callError status badge', () => {
    render(
      <VoicePanel
        title="Electronics Trade Desk"
        sessionId="demo-session"
        elapsedSeconds={0}
        isConnected={false}
        isStarting={false}
        callError="WebSocket closed unexpectedly"
      />,
    )
    expect(screen.getByText(/WebSocket closed unexpectedly/)).toBeInTheDocument()
  })
})
