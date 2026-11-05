import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { LeaveBy, Trip } from './api'
import { Result } from './Result'

function trip(overrides: Partial<Trip> = {}): Trip {
  return {
    leaves: '07:52',
    arrives: '08:23',
    be_at_stop: '07:50',
    late_p50_min: -1,
    late_p90_min: 0,
    late_worst_min: 0,
    arrives_p90: '08:23',
    days: 2,
    low_data: true,
    ...overrides,
  }
}

// From a real Saturday answer: the 07:38 leaves later, but on its bad day it arrived after 08:30.
const slow = trip({
  leaves: '07:38',
  arrives: '08:09',
  be_at_stop: '07:38',
  late_p50_min: 23,
  late_p90_min: 23,
  late_worst_min: 23,
  arrives_p90: '08:32',
  days: 1,
})
const best = trip()

function answer(overrides: Partial<LeaveBy> = {}): LeaveBy {
  return {
    route_id: '082',
    day: 'saturday',
    arrive_by: '08:30',
    origin: { stop_id: '127', name: 'Westheimer Rd @ W Loop S' },
    destination: { stop_id: '9204', name: 'Congress St @ Smith St' },
    trips: [slow, best],
    recommended: best,
    ...overrides,
  }
}

describe('Result', () => {
  it('recommends a trip and highlights its row', () => {
    render(<Result answer={answer()} />)
    expect(screen.getByText('Take the 07:52')).toBeInTheDocument()
    expect(screen.getByRole('row', { name: /^07:52/ })).toHaveClass('best')
    expect(screen.getByRole('row', { name: /^07:38/ })).not.toHaveClass('best')
  })

  it('warns when the answer is based on few days', () => {
    render(<Result answer={answer()} />)
    expect(screen.getByText(/Based on only 2 days/)).toBeInTheDocument()
  })

  it('leaves out the warning with enough history', () => {
    const enough = trip({ days: 25, low_data: false })
    render(<Result answer={answer({ trips: [enough], recommended: enough })} />)
    expect(screen.queryByText(/Based on only/)).not.toBeInTheDocument()
  })

  it('says so when no trip made it in time', () => {
    render(<Result answer={answer({ trips: [slow], recommended: null })} />)
    expect(
      screen.getByText(
        'None of these reached Congress St @ Smith St by 08:30 on 9 days out of 10.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('says so when nothing is scheduled', () => {
    render(<Result answer={answer({ trips: [], recommended: null })} />)
    expect(screen.getByText(/No scheduled trips/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})
