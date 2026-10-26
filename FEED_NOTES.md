# Feed Notes

What I've found by probing METRO's APIs directly, plus what continuous collection has shown since 2026-10-16. Snapshot numbers come from specific responses I saved; collection numbers come from the `poll` table. Each one says when it was measured. Houston times are CDT (UTC−5) until 2026-11-01, then CST (UTC−6).

## Sources

| API | Endpoint | Format | Used for |
|---|---|---|---|
| METRO GTFS Realtime | `https://api.ridemetro.org/GtfsRealtime/TripUpdates` | GTFS-RT protobuf | Main source of predictions |
| METRO Transit Data | `https://api.ridemetro.org/data/Vehicles` | JSON (OData v4) | Bus GPS positions |
| METRO Transit Data | `https://api.ridemetro.org/data/Routes('{id}')/Arrivals` | JSON (OData v4) | Spot checks, cancellations |

- Each product has its own subscription key, sent in the `Ocp-Apim-Subscription-Key` header.
- There's no GTFS-RT VehiclePositions feed. `GtfsRealtime/VehiclePositions` returns 404, and Transitland only lists TripUpdates and Alerts.
- The Transit Data responses reference `api.transitiq.com`, so METRO gets this data from a vendor, TransitIQ.

## Dataset

Continuous collection on the VPS started **2026-10-16 21:58:33 UTC** (Fri 4:58 PM Houston). Earlier rows in `poll` came from a foreground test run.

Vehicles collection started **2026-10-19 07:14:16 UTC** (Mon 2:14 AM Houston), as a separate service.

## Storage growth

| Measured | vehicle_position | stop_prediction | Raw files | Disk free |
|---|---|---|---|---|
| 2026-10-22 | 846 MB, 4.15M rows | 729 MB, 2.85M rows | 3.5 GB | 63 GB |
| 2026-10-26 | 1,506 MB, 7.38M rows | 1,139 MB, 4.43M rows | 4.9 GB | 59 GB |

Between the two: ~180 MB/day for `vehicle_position`, ~110 MB/day for `stop_prediction` (about 3.7 days, including a weekend; weekdays alone were higher). Raw files are capped by the ~8-day cleanup and level off around 5 GB.

## TripUpdates

### Snapshots

| Taken (UTC) | Houston time | Bytes | Trips | Stop time updates |
|---|---|---|---|---|
| 2026-09-25 03:39 | Thu 10:39 PM | 436,531 | 251 | 11,537 |
| 2026-09-25 23:59 | Fri 6:59 PM | 791,816 | 554 | not measured |

Evening volume is about twice late-night volume. From continuous collection, the largest snapshot so far had 26,692 usable predictions (first week, from `poll.row_count`).

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
- The 10 updates with no arrival also have no usable departure (time and delay), so the parser skips them. My first guess was that they were first stops with only a departure;
parsing the snapshot showed that's wrong (11,527 predictions, 10 skipped). The 244 with no departure are probably last stops. Arrival is used when present, with a fallback to departure; the fallback didn't trigger on this snapshot.
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

Continuous collection confirms it: over the first 7,935 polls at 30-second intervals, none returned an unchanged header.

### Rate limit

It isn't documented in the portal. What I observed:

| Pace | Result |
|---|---|
| Every ~5 s | `429 Too Many Requests` after 8 requests in ~37 s |
| Every ~10.7 s | 28 requests over 5 minutes, no 429s |

So the limit is somewhere between about 6 and 12 requests a minute, or it's a burst limit. I haven't pinned it down.

In production, polling each feed every 30 seconds (separate keys) has produced no 429s on either feed since collection started.

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

From continuous collection (first week): about 450 rows per poll at weekday midday, a peak of 718, and 3–4 rows without a GPS fix per poll (about 1%). No response has been paginated. Almost every poll stores new positions for nearly every bus, so buses report at least every ~30 seconds.

### Position age

`VehicleReportTime` compared with the response's `Date` header:

| min | median | max |
|---|---|---|
| 26 s | 29 s | 775 s |

- Positions are always at least ~26 s old when they reach me. That's either the buses' reporting interval or lag in the vendor's pipeline. At city bus speeds a bus moves a few hundred meters in that time, so working out arrivals from GPS will mean estimating between positions, not waiting for a point right at the stop.
- Some buses stay in the response long after they stop reporting (13 minutes in this snapshot). Arrival detection needs a staleness cutoff.

### Rows without a GPS fix

Every saved response (5 of 5) has 3–5 rows at exactly `0, 0` (in the Atlantic, so not a real position). Those rows also have report times 6–13 minutes older than the rest of the snapshot, and some have implausible delays (up to 4,062 s). The parser skips them and counts them as `no_fix_rows`. The 775 s max report age noted above came from one of these rows.

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

## Static GTFS

Downloaded 2026-10-26 from `https://metro.resourcespace.com/pages/download.php?ref=4835&ext=zip` (12.7 MB zip). `feed_version` is `August2026IVOMS_20261008`; feed dates 2026-08-30 to 2027-01-23 (calendar runs to 2027-02-13). METRO publishes new versions at irregular intervals (several in summer and fall 2026 on Transitland).

| File | Rows |
|---|---|
| `stop_times.txt` | 1,414,850 (71 MB) |
| `shapes.txt` | 229,848 |
| `trips.txt` | 21,878 |
| `stops.txt` | 8,803 |
| `routes.txt` | 120 |
| `calendar.txt` / `calendar_dates.txt` | 6 / 48 |

- No blank arrival times. About 10% of stop times are timepoints (`timepoint = 1`); METRO fills in times for the rest.
- 32,620 stop times are past `24:00:00` (after-midnight trips belong to the previous service day).
- Non-standard extras: `direction_names_exceptions.txt` and a `trips.ots_trip_short_name` column.
- Files start with a byte-order mark; read them as `utf-8-sig`.

### Matching realtime to the schedule

Checked with `scripts/inspect_gtfs.py` against two TripUpdates snapshots:

| | 2025-09-25, 03:39 UTC | 2026-10-26, 18:30 UTC |
|---|---|---|
| trip_id in trips.txt | 251 / 251 | 481 / 481 |
| route_id in routes.txt | 53 / 53 | 84 / 84 |
| stop_id in stops.txt | 5,889 / 5,956 | 7,376 / 7,478 |
| `time − delay` equals GTFS arrival, joined on (trip_id, stop_sequence) | 88.8% | 100.0% (19,344 / 19,344) |

- **Transit Data IDs match after removing `Ho414_4620_`** (100% of trips and routes in both Vehicles samples). Confirmed.
- **Realtime stop IDs sometimes add a bay letter** (`100_A`, `84_A`, `13170_E`). The part before `_` is in `stops.txt` for 62 of 67 in the September sample. Strip it when looking up stop details; store the full ID.
- **Trip IDs survive schedule changes, but stop patterns don't.** In the September snapshot, 43 of 251 trips had stop sequences shifted by 1–2 from the current schedule, because that snapshot ran on the previous schedule version. Joined on (trip_id, base stop_id) instead, 97.4% matched exactly. Today's snapshot matches the current version exactly. So joining to the schedule needs the version that was in effect on the service date.
- **`scheduled_arrival` (`time − delay`) is reliable** when matched to the right schedule version.
- 270 trips visit one stop twice (loops on routes 309 and 310, the Gulfton Circulators, and 076 Evergreen), so (trip_id, stop_id) isn't unique; join on (trip_id, stop_sequence) within the schedule version.
- Checked against 10 days of collected data (2026-10-26): every (service_date, trip_id, stop_id) with more than one stop_sequence is on route 076, 309, or 310, so realtime numbering hasn't shifted between polls. The two rows are two real visits.


## IDs across APIs

| | TripUpdates | Transit Data |
|---|---|---|
| Trip | `12066756` | `Ho414_4620_12066756` |
| Route | `099` | `Ho414_4620_099` (`RouteName`: `099`) |
| Stop | not checked yet | `Ho414_4620_9829` |

It looks like stripping the `Ho414_4620_` prefix lines them up. Confirmed against static GTFS (2026-10-26): removing `Ho414_4620_` matches 100% of Transit Data trips and routes. Realtime stop IDs sometimes add a bay letter (`_A`, `_B`); see Static GTFS.

## Open questions

- Do canceled trips ever appear in TripUpdates, or only in Arrivals' `IsCanceled`?
- Why do some trips have zero stop time updates: not started yet, already finished, or lost tracking?
- Why didn't some trips advance over 30 minutes? My guess is they hadn't left their first stop.
- What's the exact rate limit? Is there also a daily or monthly quota? (30-second polling causes no 429s; the exact limit is still unknown.)
- Is the ~26 s minimum position age the reporting interval or pipeline lag? (Production shows new positions for almost every bus at each 30-second poll, so buses report at least that often. Whether the ~26 s is lag is still unknown.)
- Does Transit Data have its own rate limit, separate from GTFS Realtime's? (Each feed polls every 30 s on its own key with no 429s, so separate limits look likely.)
- Why were there 554 trips in TripUpdates at 6:59 PM but only 332 buses in Vehicles at 8:26 PM? The snapshots were 90 minutes apart, and TripUpdates may include trips that haven't started. Compare two snapshots taken at the same time.
- What should the staleness cutoff for GPS positions be?
- When METRO publishes a new schedule version, on what date does the realtime feed switch to it? (The current version ends 2027-01-23, so a change is likely in January.)