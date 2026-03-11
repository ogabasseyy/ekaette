import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { StepLaunch } from '../wizard/StepLaunch'

const fetchSpy = vi.spyOn(globalThis, 'fetch')

describe('StepLaunch', () => {
  beforeEach(() => {
    fetchSpy.mockReset()
  })

  it('shows company display name from API and counts from wizard state', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({
        id: 'ekaette-electronics',
        displayName: 'Ogabassey Gadgets',
        connectors: {
          'mock-provider': { id: 'mock-provider', provider: 'mock', enabled: true },
        },
      }),
    } as Response)

    render(
      <StepLaunch
        templateId="electronics"
        companyId="ekaette-electronics"
        tenantId="public"
        templates={[
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
            status: 'active',
          },
        ]}
        counts={{ knowledge: 2, connectors: 1, products: 4 }}
        onBack={() => {}}
        onLaunch={() => {}}
      />,
    )

    // Counts render instantly from wizard state (no fetch needed)
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()

    // Display name loads from a lightweight company endpoint
    await waitFor(() => {
      expect(screen.getByText('Ogabassey Gadgets')).toBeInTheDocument()
    })
  })
})
