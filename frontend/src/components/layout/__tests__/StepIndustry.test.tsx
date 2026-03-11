import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { StepIndustry } from '../wizard/StepIndustry'

const templates = [
  {
    id: 'electronics',
    label: 'Electronics & Gadgets',
    category: 'retail',
    description: 'Trade-ins, valuation, negotiation, pickup booking.',
    defaultVoice: 'Aoede',
    theme: {
      accent: 'oklch(74% 0.21 158)',
      accentSoft: 'oklch(62% 0.14 172)',
      title: 'Electronics Trade Desk',
      hint: 'Inspect. Value. Negotiate. Book pickup.',
    },
    capabilities: [],
    status: 'active' as const,
  },
]

const companies = [
  {
    id: 'ekaette-electronics',
    templateId: 'electronics',
    displayName: 'Ogabassey Gadgets',
  },
]

describe('StepIndustry', () => {
  it('shows company display name while preserving internal company id', async () => {
    const user = userEvent.setup()
    const onNext = vi.fn()

    render(
      <StepIndustry
        templates={templates}
        companies={companies}
        defaultTemplateId="electronics"
        defaultCompanyId="ekaette-electronics"
        onNext={onNext}
      />,
    )

    expect(screen.getByLabelText(/company name/i)).toHaveValue('Ogabassey Gadgets')

    await user.click(screen.getByRole('button', { name: /next/i }))

    expect(onNext).toHaveBeenCalledWith({
      templateId: 'electronics',
      companyId: 'ekaette-electronics',
    })
  })
})
