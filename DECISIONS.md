# Decisions

Choices that shape this project, and why I made them. Evidence for data-related decisions is in [FEED_NOTES.md](FEED_NOTES.md). When a decision changes, I mark the old entry as superseded and add a new one instead of editing history.

## 1. TripUpdates is the main source of predictions

**Date:** 2026-09-24  
**Decision:** Use METRO's GTFS-Realtime TripUpdates feed as the main source of stop-level predictions. Use Transit Data's per-route Arrivals endpoint only for spot checks and cancellations, and Transit Data's Vehicles endpoint for GPS positions.  
**Why:** One TripUpdates request covers every active trip, and it has what I need: delay, predicted time, stop sequence, service date, and vehicle ID. Arrivals has similar data but only per route, which would mean 80+ requests per cycle, well past the rate limit I measured. GTFS-RT is also a standard format used by agencies everywhere, so the parser isn't tied to METRO's vendor.  
**Tradeoff:** Arrivals includes scheduled time and a cancellation flag directly. With TripUpdates I compute scheduled time as `time − delay`, and cancellations may not appear at all (not confirmed yet).  
**Revisit if:** TripUpdates turns out to leave out cancellations that Arrivals reports, or `time − delay` doesn't match the static schedule.

## 2. Record Vehicles from the start

**Date:** 2026-09-24  
**Decision:** Collect the Vehicles endpoint from week 1, even though nothing uses GPS data currently.  
**Why:** Live feeds can't be backfilled. Every week I'm not recording is a week I can never get back. Recording is cheap; it has its own API key, and one request returns every bus.  
**Tradeoff:** Storage and one more thing in the collector that can fail, for data I won't use for a while.  
**Revisit if:** Vehicles storage turns out to be much larger than expected, or its rate limit conflicts with TripUpdates.

## 3. What gets measured: the last prediction, not the arrival

**Date:** 2026-09-24  
**Decision:** A stop's recorded arrival is METRO's last prediction for that stop before it dropped out of the trip's update.  
**Why:** TripUpdates removes stops once a bus passes them, so the last prediction seen is the closest thing to an arrival time in that feed. METRO has no GTFS-RT VehiclePositions feed to measure against.  
**Tradeoff:** It's an estimate. Its accuracy depends on how close to the real arrival the last poll was. I store `last_seen_at` so the lead time (`predicted_time − last_seen_at`) can be checked and weak observations filtered out.  
**Revisit if:** GPS-based arrival detection from Vehicles is built. Then the two can be compared directly, which also measures how accurate METRO's own predictions are.

## 4. One row per trip stop, updated in place

**Date:** 2026-09-24  
**Decision:** Store predictions at the grain of one row per `(service_date, trip_id, stop_sequence)` and update that row on every poll (`INSERT ... ON CONFLICT DO UPDATE`), instead of inserting every prediction from every poll.  
**Why:** Each poll repeats the remaining stops of every active trip. Evening snapshots have about 550 trips and more than 11,000 stop updates, so inserting everything would add tens of millions of mostly duplicate rows a day. `stop_sequence` and `start_date` are present on every update, so the key is always available.  
**Tradeoff:** Only the first and last prediction for each stop are kept in the table, not how the prediction changed in between. The raw snapshots (decision 7) cover that for a week.  
**Revisit if:** I want to study how predictions evolve over time, which would need a separate table or sampling.

## 5. Poll every 30 seconds, configurable

**Date:** 2026-09-25  
**Decision:** Poll TripUpdates every 30 seconds. The interval is a config value, not a constant in code.  
**Why:** The feed regenerates about every 10 s, but the API has an undocumented rate limit somewhere between ~6 and ~12 requests a minute (8 requests at 5 s intervals triggered a 429; 28 requests at ~10.7 s intervals didn't). 30 s is about a third of the rate I know is safe, which leaves room for retries and manual checks. The worst-case gap between my last poll and the real arrival (~30 s plus up to ~10 s of feed age) is small next to a −1/+5 minute on-time window.  
**Tradeoff:** Every recorded prediction carries up to ~40 s of staleness. A 15 s interval would halve that but double requests and raw storage, and sit closer to the limit.  
**Revisit if:** METRO documents a higher limit, or lead-time analysis shows the staleness is changing on-time results.

## 6. A long-running collector on fixed ticks

**Date:** 2026-09-25  
**Decision:** The collector is one long-running process that schedules polls on fixed ticks (`next_tick += interval`, timed with `time.monotonic()`), not a new process started by cron every interval and not `sleep(interval)` after each poll.  
**Why:** In my cadence probe, `sleep(10)` plus ~0.7 s of request time made each cycle ~10.7 s, and the timing drifted against the feed. Over a day at 30 s that drift adds up to lost polls. A single process also keeps one HTTP connection open and can remember the last header timestamp to skip unchanged snapshots.  
**Tradeoff:** A crashed process means a gap until it restarts. The service manager restarts it automatically, and the poll table (decision 8) makes any gap visible.  
**Revisit if:** It needs to run on a platform that only offers scheduled jobs.

## 7. Raw snapshots gzipped, kept 7 days

**Date:** 2026-09-25  
**Decision:** Save every raw snapshot to disk, gzipped, and delete them after 7 days. Retention is configurable.  
**Why:** If a parsing or upsert bug corrupts derived rows, raw snapshots let me rebuild them. Gzip gets TripUpdates to ~35% of original size (792 KB → 277 KB at evening peak). At 2,880 polls a day that's ~630 MB/day, ~4.5 GB/week. Bugs usually show up within days, so a week is enough insurance.  
**Tradeoff:** Anything older than 7 days can't be reprocessed. 14 days would be ~9 GB, too much of a small VPS disk that Postgres also needs.  
**Revisit if:** I move raw storage to cheap object storage, which would make long-term raw history affordable.

## 8. Log every poll attempt, including failures

**Date:** 2026-09-25  
**Decision:** Write a row to a `poll` table for every fetch attempt, successful or not: request time, header timestamp, HTTP status, entity count, bytes, error. On a 429, follow `Retry-After` and keep going; never exit because one response was bad.  
**Why:** Gaps in live data can't be recovered, so I need to prove where they are and why they happened. I already hit a 429 while probing, so it's a normal event, not an edge case.  
**Tradeoff:** One extra small row per poll, about 2,880 a day.  
**Revisit if:** no reasonable condition; this is what makes the data trustworthy.

## 9. Plain PostgreSQL

**Date:** 2026-09-24  
**Decision:** Plain PostgreSQL, not TimescaleDB.  
**Why:** With decision 4, volume is a few hundred thousand rows a day, which plain Postgres handles fine with the right indexes. `percentile_cont` covers the main query. Learning one database well is a better use of my time than learning an extension on top of it.  
**Tradeoff:** No automatic time partitioning or compression.  
**Revisit if:** Queries slow down after months of data and indexes don't fix it.

## 10. Raw SQL with psycopg, no ORM

**Date:** 2026-09-24  
**Decision:** Write SQL by hand and run it with psycopg 3. Schema changes are plain `.sql` migration files.  
**Why:** The core of this project is SQL: upserts, percentiles, window functions, index tuning. An ORM would hide exactly what I want to learn.  
**Tradeoff:** More code to write for simple reads and writes, and no automatic migration generation.  
**Revisit if:** The API layer grows enough that repeated query boilerplate becomes a real problem.

## 11. Time handling

**Date:** 2026-09-24  
**Decision:** Store instants as `timestamptz` in UTC and service dates as `date`. Never parse Transit Data's `*Local` fields; use only the `*UTC` fields. Treat Transit Data's `ServiceDate` as a date string, not a timestamp.  
**Why:** Transit Data marks Houston times with a `Z` (the UTC suffix), so a normal parser silently shifts them by 5–6 hours. Its `ServiceDate` is a Houston date written as UTC midnight, so converting it to local time gives the previous day. TripUpdates' `start_date` is already a plain `YYYYMMDD` Houston date.  
**Tradeoff:** None worth mentioning.  
**Revisit if:** METRO or its vendor fixes the formats.

## 12. Docker for Postgres first, collector later

**Date:** 2026-09-24  
**Decision:** Run Postgres with Docker Compose from the start. Run the collector as a plain Python process (a systemd service on the VPS) at first, and containerize it once I'm comfortable with Docker.  
**Why:** I'm new to both Docker and Postgres. A Compose file for just Postgres is a small first step, and it gives me the same database locally and on the VPS without installing Postgres on Windows. Containerizing everything at once would mean debugging three unfamiliar things together.  
**Tradeoff:** For a while, the collector is deployed differently from the database.  
**Revisit if:** Once the collector is stable and I understand Docker. That's planned, not optional.  
**Superseded by #14.**

## 13. Hosted on an always-on VPS

**Date:** 2026-09-24  
**Decision:** Run the collector on a small paid VPS.  
**Why:** Gaps in collection are permanent. Free tiers that sleep when idle would create gaps by design, and my computer sleeps and restarts.  
**Tradeoff:** A monthly cost, and I'm responsible for server upkeep (updates, SSH, firewall).  
**Revisit if:** A free always-on option turns out to be reliable enough.

## 14. Native Postgres locally, Docker on the VPS

**Date:** 2026-09-25  
**Decision:** Run PostgreSQL 18 natively on my Windows machine for development. Use Docker Engine with Compose on the VPS for deployment, and a Postgres service container in CI. Pin Postgres 18DE everywhere.  
**Why:** Docker Desktop's current requirements list Windows 11 Enterprise, Pro, or Education; my machine runs Home. Running an unsupported setup while learning Docker would make every problem harder to diagnose. The VPS runs Linux, where Docker Engine runs natively, and that's also where the containers actually need to work.  
**Tradeoff:** My development database isn't set up the same way as production. Pinning the same major version everywhere keeps the differences small, and the SQL is identical.  
**Revisit if:** I move to Windows Pro or a Linux/macOS development machine.

## Not decided yet

- **On-time definition.** Plan: 1 minute early to 5 minutes late, configurable. Need to find and cite the source of that convention before using it.
- **Minimum sample size.** Plan: don't answer a percentile query with fewer than ~20 observations, and return the count with every answer. Need to decide the exact number once there's real data.
- **Which Vehicles row is the current trip** when a bus has two rows.
- **GPS staleness cutoff** for arrival detection.
- **Poll interval for Vehicles.** Probably 30 s like TripUpdates, but its rate limit hasn't been measured.