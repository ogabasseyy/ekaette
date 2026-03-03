import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import ValuationCard from '../cards/ValuationCard'

describe('ValuationCard', () => {
  it('renders condition badge and naira price', () => {
    render(
      <ValuationCard
        deviceName="iPhone 14 Pro"
        condition="Good"
        price={185000}
        currency="NGN"
        details="Minor frame wear."
        onAccept={() => {}}
        onDecline={() => {}}
        onCounterOffer={() => {}}
      />,
    )

    expect(screen.getByText('Good')).toBeInTheDocument()
    expect(screen.getByText(/₦/)).toBeInTheDocument()
    expect(screen.getByText(/185,000/)).toBeInTheDocument()
  })

  it('updates counter-offer input and emits value', async () => {
    const onCounterOffer = vi.fn()
    render(
      <ValuationCard
        deviceName="iPhone 14 Pro"
        condition="Good"
        price={185000}
        currency="NGN"
        details="Minor frame wear."
        onAccept={() => {}}
        onDecline={() => {}}
        onCounterOffer={onCounterOffer}
      />,
    )

    const input = screen.getByLabelText(/counter/i)
    fireEvent.change(input, { target: { value: '195000' } })
    await userEvent.click(screen.getByRole('button', { name: /counter/i }))
    expect(onCounterOffer).toHaveBeenCalledWith(195000)
  })

  it('fires accept and decline callbacks', async () => {
    const onAccept = vi.fn()
    const onDecline = vi.fn()
    render(
      <ValuationCard
        deviceName="iPhone 14 Pro"
        condition="Good"
        price={185000}
        currency="NGN"
        details="Minor frame wear."
        onAccept={onAccept}
        onDecline={onDecline}
        onCounterOffer={() => {}}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: /accept/i }))
    await userEvent.click(screen.getByRole('button', { name: /decline/i }))
    expect(onAccept).toHaveBeenCalledTimes(1)
    expect(onDecline).toHaveBeenCalledTimes(1)
  })

  it('renders Excellent condition badge', () => {
    render(
      <ValuationCard
        deviceName="Galaxy S24"
        condition="Excellent"
        price={300000}
        currency="NGN"
        details="Pristine."
        onAccept={() => {}}
        onDecline={() => {}}
        onCounterOffer={() => {}}
      />,
    )
    expect(screen.getByText('Excellent')).toBeInTheDocument()
  })
})
