import { render, screen } from '@testing-library/react'
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { StepCatalog } from '../wizard/StepCatalog'

const fetchSpy = vi.spyOn(globalThis, 'fetch')

describe('StepCatalog', () => {
  beforeEach(() => {
    fetchSpy.mockReset()
  })

  afterAll(() => {
    fetchSpy.mockRestore()
  })

  it('shows the current connected catalog item count from export data', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({
        apiVersion: 'v1',
        counts: {
          products: 3,
          knowledge: 0,
          booking_slots: 0,
        },
      }),
    } as Response)

    render(
      <StepCatalog
        companyId="ekaette-electronics"
        tenantId="public"
        onNext={() => {}}
        onBack={() => {}}
      />,
    )

    expect(await screen.findByText(/3 products connected/i)).toBeInTheDocument()
  })
})
