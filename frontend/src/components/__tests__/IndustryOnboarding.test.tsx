import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { IndustryOnboarding } from '../layout/IndustryOnboarding'

describe('IndustryOnboarding', () => {
  it('renders all industry options', () => {
    render(<IndustryOnboarding onComplete={() => {}} />)
    expect(screen.getByRole('radio', { name: /electronics/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /hotel/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /automotive/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /fashion/i })).toBeInTheDocument()
  })

  it('submits selected industry', async () => {
    const user = userEvent.setup()
    const onComplete = vi.fn()
    render(<IndustryOnboarding onComplete={onComplete} />)

    await user.click(screen.getByRole('radio', { name: /hotel/i }))
    await user.click(screen.getByRole('button', { name: /continue/i }))

    expect(onComplete).toHaveBeenCalledWith('hotel')
  })
})
