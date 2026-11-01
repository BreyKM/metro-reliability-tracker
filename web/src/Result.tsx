import type { DayType, LeaveBy } from './api'
import { late } from './format'

const DAY_NAMES: Record<DayType, string> = {
  weekday: 'weekdays',
  saturday: 'Saturdays',
  sunday: 'Sundays',
}

export function Result({ answer }: { answer: LeaveBy }) {
  const { origin, destination, recommended: best } = answer

  return (
    <section>
      <h2>
        Route {answer.route_id} on {DAY_NAMES[answer.day]}: {origin.name} to {destination.name},
        arriving by {answer.arrive_by}
      </h2>

      {best ? (
        <div className="recommendation">
          <p className="headline">Take the {best.leaves}</p>
          <p>
            Be at {origin.name} by <strong>{best.be_at_stop}</strong>. On 9 days out of 10 it
            reached {destination.name} by <strong>{best.arrives_p90}</strong>.
          </p>
          {best.low_data && (
            <p className="warning">
              Based on only {best.days} days of history, so this will change as more days come in.
            </p>
          )}
        </div>
      ) : (
        <p className="warning">
          {answer.trips.length === 0
            ? 'No scheduled trips arrive in the 90 minutes before that.'
            : `None of these reached ${destination.name} by ${answer.arrive_by} on 9 days out of 10.`}
        </p>
      )}

      {answer.trips.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Leaves</th>
              <th>Arrives</th>
              <th>Usually late</th>
              <th>Late 9 days in 10</th>
              <th>Arrives 9 days in 10</th>
              <th>Days</th>
            </tr>
          </thead>
          <tbody>
            {answer.trips.map((trip) => (
              <tr
                key={`${trip.leaves}-${trip.arrives}`}
                className={trip.leaves === best?.leaves ? 'best' : undefined}
              >
                <td>{trip.leaves}</td>
                <td>{trip.arrives}</td>
                <td>{late(trip.late_p50_min)}</td>
                <td>{late(trip.late_p90_min)}</td>
                <td>{trip.arrives_p90 ?? '-'}</td>
                <td>
                  {trip.days}
                  {trip.low_data && ' (low data)'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
