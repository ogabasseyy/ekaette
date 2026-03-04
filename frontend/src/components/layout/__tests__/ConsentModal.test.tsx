import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ConsentModal } from '../ConsentModal'

describe('ConsentModal', () => {
  it('renders modal with consent disclosure text', () => {
    render(<ConsentModal onAccept={() => {}} onDecline={() => {}} />)
    expect(screen.getByText('Data & AI Usage Consent')).toBeInTheDocument()
    expect(screen.getByText(/collect conversation data/i)).toBeInTheDocument()
  })

  it('renders Accept and Decline buttons', () => {
    render(<ConsentModal onAccept={() => {}} onDecline={() => {}} />)
    expect(screen.getByRole('button', { name: /accept/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /decline/i })).toBeInTheDocument()
  })

  it('calls onAccept when Accept is clicked', async () => {
    const user = userEvent.setup()
    const onAccept = vi.fn()
    render(<ConsentModal onAccept={onAccept} onDecline={() => {}} />)

    await user.click(screen.getByRole('button', { name: /accept/i }))
    expect(onAccept).toHaveBeenCalledTimes(1)
  })

  it('calls onDecline when Decline is clicked', async () => {
    const user = userEvent.setup()
    const onDecline = vi.fn()
    render(<ConsentModal onAccept={() => {}} onDecline={onDecline} />)

    await user.click(screen.getByRole('button', { name: /decline/i }))
    expect(onDecline).toHaveBeenCalledTimes(1)
  })

  it('contains link to privacy policy', () => {
    render(<ConsentModal onAccept={() => {}} onDecline={() => {}} />)
    const link = screen.getByRole('link', { name: /privacy policy/i })
    expect(link).toHaveAttribute('href', '/privacy.html')
    expect(link).toHaveAttribute('target', '_blank')
  })

  it('has correct accessibility attributes', () => {
    render(<ConsentModal onAccept={() => {}} onDecline={() => {}} />)
    const dialog = screen.getByRole('dialog', { name: /data & ai usage consent/i })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAttribute('aria-labelledby', 'consent-title')
  })
})
