import { useEffect, useState } from 'react'
import { leaveBy, routesBetween, type DayType, type LeaveBy, type Route, type Stop } from './api'
import { Result } from './Result'
import { StopPicker } from './StopPicker'
import './App.css'

function todaysDayType(): DayType {
  const day = new Date().getDay()
  return day === 0 ? 'sunday' : day === 6 ? 'saturday' : 'weekday'
}

export default function App() {
  const [from, setFrom] = useState<Stop | null>(null)
  const [to, setTo] = useState<Stop | null>(null)
  const [routes, setRoutes] = useState<{ pair: string; list: Route[] } | null>(null)
  const [route, setRoute] = useState<string | null>(null)
  const [day, setDay] = useState<DayType>(todaysDayType)
  const [arriveBy, setArriveBy] = useState('08:30')
  const [answer, setAnswer] = useState<LeaveBy | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const pair = from && to ? `${from.stop_id}>${to.stop_id}` : null

  useEffect(() => {
    if (!from || !to) return
    let current = true
    routesBetween(from.stop_id, to.stop_id)
      .then((list) => {
        if (current) {
          setRoutes({ pair: `${from.stop_id}>${to.stop_id}`, list })
          setError(null)
        } 
      })
      .catch((e: Error) => {
        if (current) setError(e.message)
      })
    return () => {
      current = false
    }
  }, [from, to])

  // Routes fetched for a different pair of stops don't count; null means they're still loading.
  const routeList = routes && routes.pair === pair ? routes.list : null
  // Keep the chosen route if it serves these stops, otherwise use the first one that does.
  const routeId = routeList?.some((r) => r.route_id === route)
    ? route
    : (routeList?.[0]?.route_id ?? null)

  async function submit() {
    if (!from || !to || !routeId) return
    setLoading(true)
    setError(null)
    try {
      setAnswer(
        await leaveBy({
          route: routeId,
          from: from.stop_id,
          to: to.stop_id,
          day,
          arrive_by: arriveBy,
        }),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <main>
      <h1>When should I leave?</h1>
      <p className="hint">
        The latest bus that got there on time on 9 days out of 10, from METRO's own realtime data.
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <StopPicker label="From" value={from} onChange={setFrom} />
        <StopPicker label="To" value={to} onChange={setTo} />

        {pair && routeList === null && !error && <p className="hint">Finding routes...</p>}
        {routeList?.length === 0 && (
          <p className="error">
            No route runs from {from?.name} to {to?.name} in that direction. Stops on opposite sides
            of a street share a name, so try the other one.
          </p>
        )}
        {routeList && routeList.length > 0 && (
          <div className="row">
            <label className="field">
              <span className="label">Route</span>
              <select value={routeId ?? ''} onChange={(e) => setRoute(e.target.value)}>
                {routeList.map((r) => (
                  <option key={r.route_id} value={r.route_id}>
                    {r.short_name ?? r.route_id} {r.long_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="label">Day</span>
              <select value={day} onChange={(e) => setDay(e.target.value as DayType)}>
                <option value="weekday">Weekday</option>
                <option value="saturday">Saturday</option>
                <option value="sunday">Sunday</option>
              </select>
            </label>
            <label className="field">
              <span className="label">Arrive by</span>
              <input
                type="time"
                value={arriveBy}
                onChange={(e) => setArriveBy(e.target.value)}
                required
              />
            </label>
          </div>
        )}

        <button className="primary" type="submit" disabled={!routeId || loading}>
          {loading ? 'Checking...' : 'When should I leave?'}
        </button>
      </form>

      {error && <p className="error">{error}</p>}
      {answer && <Result answer={answer} />}
    </main>
  )
}
