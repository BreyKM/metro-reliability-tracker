import { render, screen } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { leaveBy, routesBetween, searchStops, type Route, type Stop } from './api'

vi.mock('./api')

const westheimer: Stop = { stop_id: '127', name: 'Westheimer Rd @ W Loop S' }
const congress: Stop = { stop_id: '9204', name: 'Congress St @ Smith St' }
const other: Stop = { stop_id: '1', name: 'Other St @ Test Ave' }
const route082: Route = { route_id: '082', short_name: '082', long_name: 'Westheimer' }

async function pick(user: UserEvent, label: string, stop: Stop) {
  await user.type(screen.getByLabelText(label), stop.name.slice(0, 5))
  await user.click(await screen.findByRole('button', { name: `${stop.name} #${stop.stop_id}` }))
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(searchStops).mockResolvedValue([westheimer, congress, other])
})

describe('App', () => {
  it('asks the API for the chosen trip and shows the answer', async () => {
    vi.mocked(routesBetween).mockResolvedValue([route082])
    vi.mocked(leaveBy).mockResolvedValue({
      route_id: '082',
      day: 'weekday',
      arrive_by: '08:30',
      origin: westheimer,
      destination: congress,
      trips: [],
      recommended: null,
    })
    const user = userEvent.setup()
    render(<App />)

    await pick(user, 'From', westheimer)
    await pick(user, 'To', congress)
    await user.selectOptions(await screen.findByLabelText('Day'), 'weekday')
    await user.click(screen.getByRole('button', { name: 'When should I leave?' }))

    expect(
      await screen.findByRole('heading', { name: /Route 082 on weekdays/ }),
    ).toBeInTheDocument()
    expect(leaveBy).toHaveBeenCalledWith({
      route: '082',
      from: '127',
      to: '9204',
      day: 'weekday',
      arrive_by: '08:30',
    })
  })

  it('explains when no route runs in that direction', async () => {
    vi.mocked(routesBetween).mockResolvedValue([])
    const user = userEvent.setup()
    render(<App />)

    await pick(user, 'From', congress)
    await pick(user, 'To', westheimer)

    expect(await screen.findByText(/No route runs from/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'When should I leave?' })).toBeDisabled()
  })

  it("doesn't show the old stops' routes while new ones load", async () => {
    vi.mocked(routesBetween)
      .mockResolvedValueOnce([route082])
      .mockReturnValueOnce(new Promise(() => {}))
    const user = userEvent.setup()
    render(<App />)

    await pick(user, 'From', westheimer)
    await pick(user, 'To', congress)
    expect(await screen.findByLabelText('Route')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Change To stop' }))
    await pick(user, 'To', other)

    expect(screen.getByText('Finding routes...')).toBeInTheDocument()
    expect(screen.queryByLabelText('Route')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'When should I leave?' })).toBeDisabled()
  })
})
