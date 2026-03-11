import { render, screen, waitFor } from '@testing-library/react'
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { StepKnowledge } from '../wizard/StepKnowledge'

const fetchSpy = vi.spyOn(globalThis, 'fetch')

describe('StepKnowledge', () => {
  beforeEach(() => {
    fetchSpy.mockReset()
  })

  afterAll(() => {
    fetchSpy.mockRestore()
  })

  it('deduplicates repeated knowledge artifacts in the onboarding list', async () => {
    fetchSpy.mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({
        apiVersion: 'v1',
        entries: [
          { id: 'kb-1', title: 'Store Hours', text: 'We open at 9am', source: 'wizard' },
          { id: 'kb-1', title: 'Store Hours', text: 'We open at 9am', source: 'wizard' },
        ],
      }),
    } as Response)

    render(
      <StepKnowledge
        companyId="ekaette-electronics"
        tenantId="public"
        onNext={() => {}}
        onBack={() => {}}
      />,
    )

    expect(screen.getByText(/Loading knowledge entries/i)).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText(/Existing entries \(1\)/i)).toBeInTheDocument()
    })
    expect(screen.getAllByText('Store Hours')).toHaveLength(1)
  })
})
