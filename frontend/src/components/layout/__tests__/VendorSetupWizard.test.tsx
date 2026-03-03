import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

describe('VendorSetupWizard', () => {
  it('renders the wizard with step indicator', async () => {
    const { VendorSetupWizard } = await import('../VendorSetupWizard')
    render(<VendorSetupWizard onComplete={vi.fn()} />)
    // Step indicator should be present
    expect(screen.getByText(/vendor setup/i)).toBeInTheDocument()
  })

  it('renders the industry step by default', async () => {
    const { VendorSetupWizard } = await import('../VendorSetupWizard')
    render(<VendorSetupWizard onComplete={vi.fn()} />)
    // The first step should show industry selection (rendered by lazy StepIndustry)
    // Wait for lazy load
    const heading = await screen.findByText(/hardware trade desk/i)
    expect(heading).toBeInTheDocument()
  })
})
