import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MicButton } from '../ui/MicButton'

describe('MicButton', () => {
  it('renders idle variant with start label', () => {
    render(<MicButton status="idle" onClick={() => {}} />)
    const button = screen.getByRole('button', { name: /start call/i })
    expect(button).toHaveClass('bg-[color:var(--industry-accent)]')
  })

  it('renders recording variant with end label', () => {
    render(<MicButton status="recording" onClick={() => {}} />)
    const button = screen.getByRole('button', { name: /end call/i })
    expect(button).toHaveClass('bg-destructive/90')
  })

  it('renders processing variant classes', () => {
    render(<MicButton status="processing" onClick={() => {}} />)
    const button = screen.getByRole('button', { name: /processing/i })
    expect(button).toHaveClass('bg-warning/90')
    expect(button).toBeDisabled()
  })

  it('fires onClick', async () => {
    const onClick = vi.fn()
    render(<MicButton status="idle" onClick={onClick} />)
    await userEvent.click(screen.getByRole('button', { name: /start call/i }))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('applies size variant classes', () => {
    render(<MicButton status="idle" size="compact" onClick={() => {}} />)
    const button = screen.getByRole('button', { name: /start call/i })
    expect(button).toHaveClass('w-auto')
    expect(button).toHaveClass('px-3')
  })

  it('does not fire onClick when disabled', async () => {
    const onClick = vi.fn()
    render(<MicButton status="idle" onClick={onClick} disabled />)
    const button = screen.getByRole('button', { name: /start call/i })
    expect(button).toBeDisabled()
    await userEvent.click(button)
    expect(onClick).not.toHaveBeenCalled()
  })
})
