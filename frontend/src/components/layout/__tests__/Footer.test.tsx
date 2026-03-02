import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Footer } from '../Footer'

describe('Footer', () => {
  const defaultProps = {
    connectionState: 'disconnected' as const,
    isStarting: false,
    onToggleCall: () => {},
    onSendText: () => {},
    onImageSelected: () => {},
  }

  it('renders privacy policy link with correct href', () => {
    render(<Footer {...defaultProps} />)
    const link = screen.getByRole('link', { name: /privacy policy/i })
    expect(link).toHaveAttribute('href', '/privacy.html')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
  })
})
