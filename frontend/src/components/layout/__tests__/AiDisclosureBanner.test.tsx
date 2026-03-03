import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AiDisclosureBanner } from '../AiDisclosureBanner'

describe('AiDisclosureBanner', () => {
  it('renders the AI disclosure message including "AI assistant"', () => {
    render(<AiDisclosureBanner onDismiss={() => {}} />)
    expect(screen.getByText(/AI assistant/i)).toBeInTheDocument()
  })

  it('renders text about human escalation', () => {
    render(<AiDisclosureBanner onDismiss={() => {}} />)
    expect(screen.getByText(/speak with a human/i)).toBeInTheDocument()
  })

  it('calls onDismiss when dismiss button is clicked', async () => {
    const user = userEvent.setup()
    const onDismiss = vi.fn()
    render(<AiDisclosureBanner onDismiss={onDismiss} />)

    await user.click(screen.getByRole('button', { name: /dismiss/i }))
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('has correct accessibility role', () => {
    render(<AiDisclosureBanner onDismiss={() => {}} />)
    expect(screen.getByRole('complementary')).toBeInTheDocument()
  })
})
