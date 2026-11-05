import { useEffect, useState } from 'react'
import { searchStops, type Stop } from './api'

interface Props {
  label: string
  value: Stop | null
  onChange: (stop: Stop | null) => void
}

export function StopPicker({ label, value, onChange }: Props) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Stop[]>([])
  const [error, setError] = useState<string | null>(null)

  const searching = query.trim().length >= 2

  useEffect(() => {
    if (!searching) return
    // Search once typing pauses, and ignore answers to searches that have been replaced.
    let current = true
    const timer = setTimeout(() => {
      searchStops(query.trim())
        .then((stops) => {
          if (current) {
            setResults(stops)
            setError(null)
          }
        })
        .catch((e: Error) => {
          if (current) setError(e.message)
        })
    }, 250)
    return () => {
      current = false
      clearTimeout(timer)
    }
  }, [query, searching])

  if (value) {
    return (
      <div className="field">
        <span className="label">{label}</span>
        <span>
          {value.name} <small>#{value.stop_id}</small>
        </span>
        <button type="button" aria-label={`Change ${label} stop`} onClick={() => onChange(null)}>
          Change
        </button>
      </div>
    )
  }

  return (
    <div className="field">
      <label>
        <span className="label">{label}</span>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search stops, e.g. Westheimer"
        />
      </label>
      {error && <p className="error">{error}</p>}
      <ul className="results">
        {(searching ? results : []).map((stop) => (
          <li key={stop.stop_id}>
            <button
              type="button"
              onClick={() => {
                onChange(stop)
                setQuery('')
              }}
            >
              {stop.name} <small>#{stop.stop_id}</small>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
