# Decisions

Choices that shape this project, and why I made them. Evidence for data-related decisions is in [FEED_NOTES.md](FEED_NOTES.md). When a decision changes, I mark the old entry as superseded and add a new one instead of editing history.

## 1. TripUpdates is the main source of predictions

**Date:** 2026-09-24  
**Decision:** Use METRO's GTFS-Realtime TripUpdates feed as the main source of stop-level predictions. Use Transit Data's per-route Arrivals endpoint only for spot checks and cancellations, and Transit Data's Vehicles endpoint for GPS positions.  
**Why:** One TripUpdates request covers every active trip, and it has what I need: delay, predicted time, stop sequence, service date, and vehicle ID. Arrivals has similar data but only per route, which would mean 80+ requests per cycle, well past the rate limit I measured. GTFS-RT is also a standard format used by agencies everywhere, so the parser isn't tied to METRO's vendor.  
**Tradeoff:** Arrivals includes scheduled time and a cancellation flag directly. With TripUpdates I compute scheduled time as `time − delay`, and cancellations may not appear at all (not confirmed yet).  
**Revisit if:** TripUpdates turns out to leave out cancellations that Arrivals reports, or `time − delay` doesn't match the static schedule.

**Update 2026-10-26:** Checked against the static schedule: `time − delay` matched the scheduled arrival at 100% of stops in a current snapshot, joined on trip and stop sequence (FEED_NOTES).

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
**Update 2026-10-27:** The lead time wasn't always short. 26% of stop visits had a last prediction made more than 2 minutes from arrival, or dropped out together with one that was, mostly where METRO wasn't tracking a bus (FEED_NOTES, "Stale predictions dropped in bulk"). Those visits are flagged and left out of delay analysis (#28).

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

**Update 2026-10-16:** Chose OVHcloud VPS-2 (4 vCore, 8 GB RAM, 75 GB NVMe, US data center):
- Hetzner raised cloud prices two or three times in 2026 (shared AMD plans by over 100% for new orders) and had nothing in stock.  
- Oracle's Always Free tier reclaims instances that stay under 20% CPU, network, and memory for 7 days, which describes this collector.  
- DigitalOcean and Linode charge $12/mo for 1 vCPU and 2 GB RAM, a quarter of the memory.  
- OVH VPS-1 plus an extra disk was the backup plan, but additional disks aren't covered by OVH's included daily backup. VPS-2 keeps everything on one disk inside that backup.  
Chose a regular data-center VPS, not a Local Zone, because Local Zone VPSs don't support Docker.

## 14. Native Postgres locally, Docker on the VPS

**Date:** 2026-09-25  
**Decision:** Run PostgreSQL 18 natively on my Windows machine for development. Use Docker Engine with Compose on the VPS for deployment, and a Postgres service container in CI. Pin Postgres 18 everywhere.  
**Why:** Docker Desktop's current requirements list Windows 11 Enterprise, Pro, or Education; my machine runs Home. Running an unsupported setup while learning Docker would make every problem harder to diagnose. The VPS runs Linux, where Docker Engine runs natively, and that's also where the containers actually need to work.  
**Tradeoff:** My development database isn't set up the same way as production. Pinning the same major version everywhere keeps the differences small, and the SQL is identical.  
**Revisit if:** I move to Windows Pro or a Linux/macOS development machine.

## 15. Upserts only move forward in time

**Date:** 2026-09-25  
**Decision:** The `stop_prediction` upsert only updates an existing row when the incoming observation is newer: `ON CONFLICT ... DO UPDATE ... WHERE stop_prediction.last_seen_at < EXCLUDED.last_seen_at`. Applying the same snapshot twice changes nothing, including `update_count`.  
**Why:** Without the guard, reprocessing a snapshot would count the same observation twice, and processing snapshots out of order would let an older prediction overwrite a newer one. Reprocessing happens after a crash mid-batch or when rebuilding from raw files. The last prediction is the whole metric (#3), so it can never go backward. The original brief's test expected `update_count` to go up when the same fixture was applied twice. I think that's the wrong behavior, so the test will check that it stays the same.  
**Tradeoff:** Two observations with the exact same header timestamp can't both be applied. Only the first one counts. That's correct, since they're the same snapshot.  
**Revisit if:** I need a separate audit trail of every time a snapshot was processed. That would go in its own table, not this one.

## 16. "Seen at" means METRO's feed timestamp, not my request time

**Date:** 2026-09-25  
**Decision:** `first_seen_at` and `last_seen_at` in `stop_prediction` store the feed's `header.timestamp`. My own request time goes only in `poll.requested_at`.  
**Why:** The lead-time check (`predicted_arrival − last_seen_at`, #3) should measure from when METRO made the prediction, not from when my request happened to go out. The feed was 2 to 14 s old when fetched in my cadence probe. The header timestamp is also inside the raw file, so rebuilding rows from raw snapshots gives the same result no matter when I rerun it, which is what makes #15's forward-only check work during a rebuild.  
**Tradeoff:** If METRO's header clock were wrong, my timestamps would be wrong in the same way. `poll.requested_at` next to `header_timestamp` makes that visible.  
**Revisit if:** The header timestamp turns out to be unreliable, for example repeating while the content changes.

## 17. Vehicle positions are append-only, stored as reported

**Date:** 2026-09-26  
**Decision:** One row per `(vehicle_id, reported_at, trip_id)`, inserted with `ON CONFLICT DO NOTHING` and never updated. Both rows are kept when a bus is listed on two trips. IDs are stored exactly as the API sends them. Coordinates are `real`.  
**Why:** A reported position is a fixed fact, unlike a prediction, which changes (#4). Picking the "current" trip, or stripping the ID prefix, while storing would bake unverified rules into data that can't be collected again. The API's coordinates already have 4-byte float precision (`29.6662502288818` is a float32 value printed as a double), so `real` loses nothing and halves the size of those columns.  
**Tradeoff:** Buses listed on two trips store their position twice (~10% of rows in my sample). Queries have to choose the current trip themselves.  
**Revisit if:** The current-trip rule is confirmed and the duplicates start to matter for storage.

## 18. Migrations applied by a small runner; tests use a disposable database

**Date:** 2026-10-15  
**Decision:** `python -m metro.migrate` applies any `migrations/*.sql` file whose version isn't in `schema_migration`, in filename order. Each file keeps its own `BEGIN ... COMMIT` and records its own version; the runner checks that it did. Applied migrations are never edited; changes go in a new file. Tests reset a separate `*_test` database and apply every migration from scratch once per run.  
**Why:** There are now three databases (local, test, VPS), and applying files by hand doesn't scale. Rebuilding the test schema from nothing on every run proves the migrations produce a working schema on an empty database, which is what the VPS will start from. The `_test` name check keeps the reset from ever touching a database with real data.  
**Tradeoff:** No down-migrations. Undoing a change means writing a new migration that reverses it.  
**Revisit if:** Migrations need features this doesn't support, such as statements that can't run inside a transaction (e.g. `CREATE INDEX CONCURRENTLY`). At that point a tool like Alembic or sqitch is worth it.

## 19. How the collector handles failures and timing

**Date:** 2026-10-16  
**Decision:** Every fetch outcome gets a poll row: network error, 429, other HTTP errors, invalid protobuf, unchanged snapshot, rejected upsert, or success. Raw snapshots are saved before the database write. A lost database connection is logged and retried on the next tick. Polls stay on a fixed grid, are pushed back by `Retry-After`, and resume from now (no catch-up burst) after falling behind. SIGINT and SIGTERM finish the current poll and exit cleanly.  
**Why:** Gaps can't be recovered, so the collector must never stop, and every gap needs an explanation in the data. Saving raw first means a failed write can still be rebuilt. Catching up after a sleep would send a burst of requests into a rate limit I've already hit once.  
**Tradeoff:** While Postgres is unreachable, nothing is fetched or logged to the database, only to the process log. Postgres runs on the same machine, so this should be rare.  
**Revisit if:** Database outages turn out to be more than rare. Then fetch and save raw snapshots even without a connection, and log the polls once it's back.

## 20. Deployment layout on the VPS

**Date:** 2026-10-16  
**Decision:** Postgres 18 runs in Docker Compose (`compose.yaml` in the repo) with its data in a named volume, published only on `127.0.0.1:5432`. The app lives in `/opt/metro/app`, owned by a `metro` system user with no login and no sudo. Secrets are in `/opt/metro/app/.env`, mode 600, shared by Compose and the collector. Docker is installed from Docker's own apt repository, container logs are capped at 3 × 10 MB, and admin commands use `sudo docker` rather than the `docker` group.  
**Why:** Docker's published ports bypass ufw, so binding to `127.0.0.1` is what keeps the database off the internet; I confirmed it with a TCP test from outside. A volume keeps data across container upgrades. A separate system user limits what a bug in the collector could reach. Membership in the `docker` group is equivalent to root without a password.  
**Tradeoff:** `POSTGRES_USER` makes `metro_app` a superuser in the container, unlike my local setup where it's a plain owner role. Acceptable while the database is only reachable from the server itself.  
**Revisit if:** Anything besides the collector needs database access (for example the API), at which point it should get its own limited role.

## 21. Nightly database backups to Backblaze B2

**Date:** 2026-10-16  
**Decision:** A root systemd timer runs `pg_dump --format=custom` inside the Postgres container at 08:00 UTC, checks the dump with `pg_restore --list`, keeps 3 days locally, and uploads it to a private Backblaze B2 bucket. A bucket lifecycle rule deletes uploads after about 30 days. The B2 key is limited to that one bucket and stored in root-only `/etc/metro/`. The script runs from a root-owned copy in `/usr/local/sbin`, not from the repo checkout. I restored a downloaded backup into a scratch database and matched its row counts against the live database.  
**Why:** The collected data can't be re-collected, and OVH's included backup is stored with the same provider. B2 costs $6.95/TB/month with 10 GB free, and downloads are free up to 3× storage, which covers occasional restores. Cloudflare R2 has free downloads but charges about twice as much for storage. Running root-executed code from a folder the `metro` user can write would let a compromised collector become root.  
**Tradeoff:** Up to a day of data could be lost (the time since the last backup). The key can delete files in its bucket, so a compromised server could erase backups.  
**Revisit if:** The dataset becomes valuable enough to need less than a day of loss (then use continuous WAL archiving), or to justify an upload-only key so the server can't delete backups.  
**Update 2026-10-29:** Backups leave out the data in `stop_observation` and `schedule_match`. Both are built entirely from `stop_prediction` and the schedule, which are backed up, so a restore rebuilds them with one run of `metro-build-observations` (#27).

## 22. External monitoring with healthchecks.io

**Date:** 2026-10-17  
**Decision:** The collector pings a healthchecks.io check after recording a new snapshot, at most once a minute, from a separate HTTP client with no headers. The nightly backup pings a second check after a successful upload. healthchecks.io emails me when pings stop (collector: 1 minute period + 10 minute grace; backup: 1 day + 2 hours).  
**Why:** The failures that cost the most data are the ones nothing reports: a revoked API key (polls keep logging 401s), a frozen feed (every poll "unchanged"), or the server being down. Monitoring from outside catches all of them, because it only cares that new data keeps arriving. A separate client keeps the METRO API key from being sent to the monitor. A failed ping is only logged, so monitoring can never stop collection.  
**Tradeoff:** It depends on a third-party service, and the ping URLs are secrets to manage. A real outage shorter than 11 minutes won't alert.  
**Revisit if:** I need alerts on other signals (slow polls, disk filling up). healthchecks.io can receive those too, or a full metrics setup would.  
**Update 2026-10-29:** The nightly backup now sends the free disk space with its ping, and pings `/fail` under 10 GB. At about 0.5 GB/day that's around three weeks of warning.

## 23. One collector process per feed

**Date:** 2026-10-17  
**Decision:** The collector runs one feed per process, chosen by a command-line argument (`metro-collector vehicles`). Each feed is described by a `Feed` spec (URL, parser, change marker, record function), and the polling loop is shared. Each feed gets its own systemd service and its own health check.  
**Why:** The feeds come from separate API products with separate keys and probably separate rate limits. Separate processes mean a slow or failing Vehicles API can't delay TripUpdates polls, and an alert says which feed stopped. Sharing the loop keeps the failure handling and scheduling (#6, #19) in one tested place.  
**Tradeoff:** Two services to deploy and watch instead of one, and the polls of the two feeds aren't aligned in time.  
**Revisit if:** An analysis needs both feeds sampled at the same instant, which would favor one loop polling both.  

## 24. On time means 1 minute early to 5 minutes late

**Date:** 2026-10-19  
**Decision:** A bus counts as on time if its arrival at a stop is no more than 1 minute early and no more than 5 minutes late. The window is configurable.  
**Why:** It matches METRO's own standard for local buses (a six-minute window starting 59 seconds before the scheduled departure, as reported by [Houston Landing](https://www.houstonlanding.org/metro-looks-to-new-fleet-to-improve-on-time-performance-of-its-buses-will-it-be-enough/)), so results can be compared with METRO's 74% goal. It's also the most common window in the industry; there's no national standard ([TransitCenter](https://transitcenter.org/bus-time-even-mean/)).  
**Tradeoff:** METRO measures departures, most likely at timepoints; this project uses last arrival predictions at every stop. The numbers are comparable but not identical.  
**Revisit if:** METRO publishes a different definition, or an analysis needs a stricter window.  

## 25. Versioned static GTFS, chosen by effective date

**Date:** 2026-10-26  
**Decision:** Every static GTFS version is loaded in full and never changed, keyed by a `gtfs_feed` row (SHA-256 of the zip is unique, so the same file isn't loaded twice). Each version has an `effective_from` date chosen when it's loaded; `gtfs_feed_for(date)` returns the newest version with `effective_from` on or before that date. GTFS times are stored as integer seconds from the start of the service day, and `service_day_start(date)` applies the "noon minus 12 hours" rule in Houston time.  
**Why:** METRO keeps trip IDs across schedule versions but changes stop patterns: a September snapshot matched the current schedule on only 88.8% of stops, while a current one matches 100% (FEED_NOTES). So a prediction must be joined to the version in effect on its service date. Keeping whole versions is simpler and easier to audit than tracking per-row changes, and costs ~190 MB per version (186 MB measured, almost all stop_times), less than one day of realtime data. Seconds since the start of the service day represent times past 24:00:00 directly, at 4 bytes per value.  
**Tradeoff:** If METRO switches the realtime feed on a different day than `effective_from`, a day or two around a change can join to the wrong version. The daily match-rate check (#27) catches that, and fixing it means updating one date.  
**Revisit if:** Changeovers regularly mismatch, in which case choose the version per day from the data instead. 

## 26. Store `start_date` as reported; correct the service date when joining to the schedule

**Date:** 2026-10-26  
**Decision:** `stop_prediction.service_date` keeps METRO's `start_date` exactly as reported. Queries that join to the schedule assign each prediction to the reported date if its scheduled time matches there, otherwise to the day before if it matches there. A prediction matching neither is reported as unmatched, not guessed.  
**Why:** METRO labels trips that start after midnight with the next calendar day instead of their service date (FEED_NOTES), about 1.3–1.5% of rows each night. Correcting it in the collector would make the 24/7 process depend on the static schedule to know which trips start after midnight. Following #17, the stored data stays as the feed reported it, and the correction lives in one query where it can be checked: a scheduled time matching to the second can't happen by accident.  
**Tradeoff:** Every query that groups by service date or weekday has to use the corrected date, not the stored column. Uncorrected, these trips count toward the wrong day of the week.  
**Revisit if:** METRO fixes `start_date`, or a correction rule turns out to need more than "the day before."  

## 27. A daily table of observations, built from finished service days

**Date:** 2026-10-26  
**Decision:** A daily job (`metro-build-observations`, systemd timer at 10:00 UTC) joins each finished service date's predictions to the schedule and writes one row per stop visit to `stop_observation`, with the corrected service date (#26), GTFS route, direction, and stop, METRO's last predicted arrival, the delay, and how long before arrival that prediction was made. Date S is built on S + 2, once its after-midnight trips (stored under S + 1) have finished. Each date is rebuilt in one transaction (delete, then insert), so any range can be rebuilt after a fix. Counts of exact, date-shifted, and unmatched predictions go in `schedule_match`; if more than 1% are unmatched, or a day has no predictions, the job reports a failure to healthchecks.io, which emails me.  
**Why:** The leave-by query reads months of data; joining at query time would redo the same work on every request, and a materialized view can only be refreshed in full. Building each day once matches how the data grows. Keeping `lead_seconds` lets the cutoff for weak predictions be chosen from data later. The main failure this catches is a new schedule version METRO published that I haven't loaded: nothing errors, the match rate just drops.  
**Tradeoff:** Results are two days behind. One more job and health check to run. The 1% threshold is a first guess.  
**Revisit if:** The leave-by answers need to be fresher than two days, or normal days regularly come close to 1% unmatched.  

## 28. Only trust stop visits whose whole drop was near arrival

**Date:** 2026-10-27  
**Decision:** `stop_observation.trusted` is true only when every stop of the same trip that dropped out of the feed in the same poll had its last prediction within 2 minutes of its predicted arrival, either side. Delay analysis, starting with the leave-by query, uses trusted rows only. The daily build counts trusted rows in `schedule_match` and logs the share, with no alert.  
**Why:** When METRO isn't tracking a bus on a trip, it keeps predicting the next stop for about "now", so the delay grows with the clock, and then drops the whole run of stale stops in one poll once a bus appears (FEED_NOTES). Those rows record delays that didn't happen; their median is 54 minutes. A lead-time window misses the first stop of such a run, whose prediction was always about "now"; checking the whole drop catches it. With the rule, p90 delay is 10.4 minutes and p99 27 minutes, against 74 minutes and 2 hours unfiltered. Polls are 30 seconds apart, so a real bus passing two stops in one poll stays well inside ±2 minutes. A looser version that only excluded drops containing a stop more than 10 minutes ahead kept 18,700 more rows whose p90 was 24 minutes.  
**Tradeoff:** 26% of stop visits have no trusted arrival time, and they aren't random: they cluster on stretches of trips METRO wasn't tracking. Percentiles describe service when METRO was tracking the bus. Whether those stops were served on time is unknown until arrivals are measured from GPS. No alert on the trusted share yet, because 10 days isn't enough to know its normal range.  
**Revisit if:** GPS-measured arrivals exist (compare them with trusted rows), or the daily trusted share moves far from 74%.

## 29. How the leave-by query answers

**Date:** 2026-10-28  
**Decision:** `metro-leave-by` takes a route, a boarding stop, a destination stop, a day type, and an arrive-by time. It lists the current schedule's trips on that route arriving in the 90 minutes before the deadline, and recommends the latest one whose 90th-percentile arrival at the destination is at or before it. A trip's history is every trusted visit (#28) with the same route, stop, day type, and scheduled time of day. The day type comes from the trip's service calendar, so a holiday on a Sunday schedule counts as a Sunday. "Be at the stop by" is the scheduled time, moved earlier by the 10th-percentile delay at the boarding stop when that's negative. Every answer shows how many days it's based on, and fewer than 20 is flagged as low data instead of refused. Percentiles use `percentile_disc`, so every time shown actually happened; ddisplayed arrivals and delays round up, and boarding times round down.  
**Why:** A rider chooses a specific trip, and the 7:42 and the 7:12 run in different traffic, so hour-wide buckets would blur the answer. Keying on scheduled time instead of `trip_id` keeps history across schedule versions while the times stay the same. Early departures matter as much as late arrivals: arriving at the scheduled time misses a bus that leaves early. Refusing below 20 days would mean no answers for weeks, and Saturday and Sunday slots get only one day a week; showing the count lets the reader judge.  
**Tradeoff:** The boarding and arrival percentiles are computed separately, using every trusted visit at each stop, so "be at the stop" and "arrives by" each hold on 90% of days but not necessarily on the same days. When a new schedule changes a trip's times, its history starts over. With 20 days, the 90th percentile is set by the two or three worst ones, so it moves noticeably as data comes in. Single route only, no transfers. Canceled trips may not appear in TripUpdates at all (#1), so the answer is about trips that ran.  
**Revisit if:** Answers need transfers, a trip's history needs to survive small time changes, or cancellations become measurable.  

## 30. A read-only FastAPI service for the leave-by query

**Date:** 2026-10-29  
**Decision:** `metro.api` is a FastAPI app with four GET endpoints: `/health`, `/stops` (search by name), `/routes` (routes that run from one stop to another, in that direction), and `/leave-by`. It reuses the queries in `leave_by.py`. Times come back as `HH:MM`, already rounded by #29's rules, and delays as whole minutes rounded up. Endpoints are plain functions using a pool of 1–4 psycopg connections, and every connection is read-only with a 5-second statement timeout. An index on `gtfs_stop_time (feed_id, stop_id)` serves the stop lookups.  
**Why:** FastAPI validates parameters from type hints and documents every endpoint at `/docs`. Rounding is part of what an answer means (#29), so it stays in one place instead of being redone in the front end. psycopg is used synchronously everywhere in this project; FastAPI runs plain functions in a thread pool, so a slow query doesn't block other requests. The API shares the database with collectors that can't miss polls, so it can't write and can't run a long query. Without the index, finding the trips that serve one stop read all 1.4 million stop times in the schedule (703 ms).  
**Tradeoff:** A front end that wants different rounding or units needs an API change. Stop search is a plain substring match, and stops on opposite sides of a street share a name; `/routes` is what tells them apart. There's no authentication or rate limiting, so it isn't meant to be public.  
**Revisit if:** The API needs to be public (then authentication, rate limits, and CORS), or a client needs raw seconds.

## Not decided yet

- **Which Vehicles row is the current trip** when a bus has two rows.
- **GPS staleness cutoff** for arrival detection.
- **Poll interval for Vehicles.** Probably 30 s like TripUpdates, but its rate limit hasn't been measured.
- **Retention for `vehicle_position`.** Measured over a week (2026-10-19 to 2026-10-26, including a weekend): ~180 MB/day (~870K rows/day), about 60% of database growth; weekdays alone ran closer to 230 MB/day. Total disk growth is ~9–10 GB/month once raw snapshots level off at ~5 GB, with 59 GB free, so the disk fills around April 2027. Plan: partition by month on `reported_at`, archive old months to B2 as Parquet, then drop them from Postgres. Do it within 1–2 months, while the table is still a few GB.
- **Detecting new schedule versions.** Plan: download the GTFS zip daily, load it when its SHA-256 and `feed_version` are both new. How to set `effective_from` automatically is still open.