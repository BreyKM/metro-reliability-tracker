// Shapes of the API's responses (src/metro/api.py). Times are "HH:MM", already rounded by
// the API (decision #29), so the page only displays them.

export type DayType = 'weekday' | 'saturday' | 'sunday'

export interface Stop {
  stop_id: string
  name: string
}

export interface Route {
  route_id: string
  short_name: string | null
  long_name: string | null
}

export interface Trip {
  leaves: string
  arrives: string
  be_at_stop: string
  late_p50_min: number | null
  late_p90_min: number | null
  late_worst_min: number | null
  arrives_p90: string | null
  days: number
  low_data: boolean
}

export interface LeaveBy {
  route_id: string
  day: DayType
  arrive_by: string
  origin: Stop
  destination: Stop
  trips: Trip[]
  recommended: Trip | null
}

async function get<T>(path: string, params: Record<string, string>): Promise<T> {
  const response = await fetch(`/api${path}?${new URLSearchParams(params)}`)
  if (!response.ok) {
    // FastAPI puts its message in "detail": a string for our 404s, a list for 422s.
    const body = await response.json().catch(() => null)
    const detail = typeof body?.detail === 'string' ? body.detail : null
    throw new Error(detail ?? `request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const searchStops = (q: string) => get<Stop[]>('/stops', { q })

export const routesBetween = (from: string, to: string) => get<Route[]>('/routes', { from, to })

export const leaveBy = (params: {
  route: string
  from: string
  to: string
  day: DayType
  arrive_by: string
}) => get<LeaveBy>('/leave-by', params)
