import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import BookingConfirmationCard from '../cards/BookingConfirmationCard'

describe('BookingConfirmationCard', () => {
  it('renders confirmation fields', () => {
    render(
      <BookingConfirmationCard
        confirmationId="EKA-2026-001"
        date="2026-03-14"
        time="10:00 AM"
        location="Lekki Phase 1"
        service="Doorstep pickup"
      />,
    )

    expect(screen.getByText(/EKA-2026-001/i)).toBeInTheDocument()
    expect(screen.getByText('2026-03-14')).toBeInTheDocument()
    expect(screen.getByText('10:00 AM')).toBeInTheDocument()
    expect(screen.getByText('Lekki Phase 1')).toBeInTheDocument()
  })
})
