# Feed Notes

What I've found by probing METRO's APIs directly. Numbers come from specific snapshots I saved, not long-run averages, and each one says when it was taken. Houston times are CDT (UTC−5).

## Sources

| API | Endpoint | Format | Used for |
|---|---|---|---|
| METRO GTFS Realtime | `https://api.ridemetro.org/GtfsRealtime/TripUpdates` | GTFS-RT protobuf | Main source of predictions |
| METRO Transit Data | `https://api.ridemetro.org/data/Vehicles` | JSON (OData v4) | Bus GPS positions |
| METRO Transit Data | `https://api.ridemetro.org/data/Routes('{id}')/Arrivals` | JSON (OData v4) | Spot checks, cancellations |

- Each product has its own subscription key, sent in the `Ocp-Apim-Subscription-Key` header.
- There's no GTFS-RT VehiclePositions feed. `GtfsRealtime/VehiclePositions` returns 404, and Transitland only lists TripUpdates and Alerts.
- The Transit Data responses reference `api.transitiq.com`, so METRO gets this data from a vendor, TransitIQ.

## TripUpdates

### Snapshots

| Taken (UTC) | Houston time | Bytes | Trips | Stop time updates |
|---|---|---|---|---|
| 2026-09-25 03:39 | Thu 10:39 PM | 436,531 | 251 | 11,537 |
| 2026-09-25 23:59 | Fri 6:59 PM | 791,816 | 554 | not measured |

Evening volume is about twice late-night volume. I haven't looked at a weekday morning rush yet.

### Fields

From the 03:39 UTC snapshot:

| Field | Present on |
|---|---|
| `trip.trip_id`, `trip.route_id`, `trip.start_date`, `trip.start_time` | 251 / 251 trips |
| `trip_update.vehicle.id` | 251 / 251 trips |
| `stop_time_update.stop_sequence`, `stop_id` | 11,537 / 11,537 |
| `arrival.time`, `arrival.delay` | 11,527 / 11,537 |
| `departure.time`, `departure.delay` | 11,293 / 11,537 |

- Delay and predicted time are both given, so scheduled time = `time − delay`. I haven't checked this against static GTFS yet.
- The 10 updates with no arrival look like first stops (departure only), and the 244 with no departure look like last stops. So use arrival, and fall back to departure when there's no arrival.
- `start_date` is the Houston service date as `YYYYMMDD`. At 03:39 UTC it read `20260924`, which is correct for 10:39 PM local.
- Every entity was a `trip_update`, and every trip was `SCHEDULED`. None were `CANCELED`, but that snapshot was late at night, so it doesn't show whether cancellations ever appear here.
- Some trips are in the feed with zero stop time updates: 9 of 237 at night and 18 of 554 in the evening, about 3–4%.

### How trips change between polls

Passed stops drop out of a trip's update. That's what makes "last prediction before the stop disappears" a usable measure of arrival.

| Gap between snapshots | Trips in both | Trips that dropped their earliest stop(s) |
|---|---|---|
| ~30 min (night) | 156 | 135 |
| ~70 s (evening) | 548 | 279 |

About half of trips pass at least one stop per minute. Example: trip 12067968 went from stop 3 to stop 43 in 30 minutes.

What I record is METRO's **last prediction** before the bus passed a stop, not a measured arrival. The two are close when the last poll was just before the bus arrived.

### Update cadence

I polled every ~10 s for 5 minutes (2026-09-26, about 00:32 UTC):

- `header.timestamp` changes about every 10 seconds.
- The feed was usually 2–9 seconds old when fetched.
- Some cycles are skipped. I saw one gap of 16 seconds and a few polls that returned the same header twice.

My first guess, from two night snapshots whose headers happened to land on :00 and :30, was a 30-second cycle. That was wrong.

### Rate limit

It isn't documented in the portal. What I observed:

| Pace | Result |
|---|---|
| Every ~5 s | `429 Too Many Requests` after 8 requests in ~37 s |
| Every ~10.7 s | 28 requests over 5 minutes, no 429s |

So the limit is somewhere between about 6 and 12 requests a minute, or it's a burst limit. I haven't pinned it down.

### Compression

`791,816 → 277,459` bytes with gzip (35% of the original, about 2.9× smaller). Protobuf is already compact, so gzip has less to remove than it would from JSON.

## Transit Data: Vehicles

### Snapshot

| Taken (UTC) | Houston time | Bytes | Rows | Unique vehicles | Unique trips |
|---|---|---|---|---|---|
| 2026-09-26 01:26 | Fri 8:26 PM | 183,719 | 366 | 332 | 366 |

- **One request returns everything.** There was no `@odata.nextLink`, so no pagination.
- **Each row is a (vehicle, trip) pair, not a vehicle.** 298 vehicles had one row and 34 had two. In my first sample, bus 2450's two rows were its current trip and its next trip, with the same position and report time. Before a GPS point can be tied to a trip, I need a rule for which row is the current trip.
- **All 366 rows were `IsMonitored: true`.** Unmonitored buses may be left out rather than flagged.
- **All trip IDs share the prefix `Ho414_4620`.**
- **340 of 366 rows had a nonzero `Delayseconds`.** It's one delay per trip, not per stop.

### Position age

`VehicleReportTime` compared with the response's `Date` header:

| min | median | max |
|---|---|---|
| 26 s | 29 s | 775 s |

- Positions are always at least ~26 s old when they reach me. That's either the buses' reporting interval or lag in the vendor's pipeline. At city bus speeds a bus moves a few hundred meters in that time, so working out arrivals from GPS will mean estimating between positions, not waiting for a point right at the stop.
- Some buses stay in the response long after they stop reporting (13 minutes in this snapshot). Arrival detection needs a staleness cutoff.

### Field quirks

- **The `*Local` time fields end in `Z` but are Houston time.** `TripStartTimeLocal` was `21:24:00Z` while `TripStartTimeUTC` was `02:24:00Z`. Only use the `*UTC` fields.
- There's no stop information (current or next stop). Working out arrivals from GPS means matching positions to stop coordinates.
- `Block` was null in the rows I looked at.

## Transit Data: Arrivals

From one sample for route 002 (2026-09-25 02:15 UTC):

- One row per (trip, stop), with `ScheduledTime`, `ArrivalTime`, `DelaySeconds`, `StopSequence`, `ServiceDate`, and `IsCanceled`.
- `DelaySeconds` matched: 02:16:31 − 02:13:42 = 169 s.
- **`ServiceDate` is a Houston date written as UTC midnight** (`2026-09-24T00:00:00Z`). Converting it to Houston time gives the wrong day. Read only the date part.
- **Don't use `ArrivalId` as a key.** It contains locale-formatted dates, a narrow no-break space (`\u202f`), and the predicted time, so it changes whenever the prediction does.
- `VehicleID` was null.
- `DelaySeconds` is a float (`169.0`).
- It's per route only; `data/Arrivals` for the whole system returns 404. Polling every route would be ~80+ requests per cycle, which the rate limit rules out.

## IDs across APIs

| | TripUpdates | Transit Data |
|---|---|---|
| Trip | `12066756` | `Ho414_4620_12066756` |
| Route | `099` | `Ho414_4620_099` (`RouteName`: `099`) |
| Stop | not checked yet | `Ho414_4620_9829` |

It looks like stripping the `Ho414_4620_` prefix lines them up. **This is unconfirmed.** I need to check it across a full snapshot and against static GTFS `trips.txt` before relying on it.

## Open questions

- Do trip IDs match between TripUpdates, Transit Data, and static GTFS?
- Does `time − delay` equal the static GTFS scheduled time?
- Do canceled trips ever appear in TripUpdates, or only in Arrivals' `IsCanceled`?
- Why do some trips have zero stop time updates: not started yet, already finished, or lost tracking?
- Why didn't some trips advance over 30 minutes? My guess is they hadn't left their first stop.
- What's the exact rate limit? Is there also a daily or monthly quota?
- How large are snapshots at weekday morning rush?
- Is the ~26 s minimum position age the reporting interval or pipeline lag? Polling Vehicles every ~10 s for a few minutes and watching when each bus's `VehicleReportTime` changes would tell.
- Does Transit Data have its own rate limit, separate from GTFS Realtime's?
- Why were there 554 trips in TripUpdates at 6:59 PM but only 332 buses in Vehicles at 8:26 PM? The snapshots were 90 minutes apart, and TripUpdates may include trips that haven't started. Compare two snapshots taken at the same time.
- What should the staleness cutoff for GPS positions be?