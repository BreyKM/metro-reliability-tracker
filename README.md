# Houston METRO Reliability Tracker

METRO's realtime feed tells you where a bus is right now. Nothing tells you whether that bus is usually late. This project records METRO's live feeds around the clock to build the history that doesn't exist anywhere else, and to answer a rider's question no transit app does: *what time should I leave to make it with 90% confidence?*

## Why

METRO's local buses carry about 195,000 boardings on an average weekday ([ridership reports](https://www.ridemetro.org/about/records-reports/ridership-reports), September 2026). METRO tracks on-time performance against a 74% goal for local buses, and a [Houston Landing analysis](https://www.houstonlanding.org/metro-looks-to-new-fleet-to-improve-on-time-performance-of-its-buses-will-it-be-enough/) of 2023 data found the 85 Antoine/Washington, one of the busiest routes, on time 67% of the time. LINK Houston's [Equity in Transit 2024](https://linkhouston.org/equity-in-transit-2024-2/) report recommends that METRO "ensure all on-time performance data is publicly available." This project builds that record independently, at the level of every stop, from METRO's own realtime feeds.

## Status

Running 24/7 on a VPS since October 16, 2026. As of October 19:

| | Arrival predictions (GTFS-Realtime) | Bus GPS positions |
|---|---|---|
| Recording since | 2026-10-16 | 2026-10-19 |
| Polls | 7,935 | 1,061 |
| Successful | 100% | 100% |
| Missed polls | 0 | 0 |

That's 1.17 million trip-stop predictions across 106 routes, 8,903 stops, and 17,342 trips, plus 375,748 GPS positions. A rush-hour snapshot has about 26,700 predictions and takes about 4 seconds to process.

The leave-by query works from the command line. Answers based on fewer than 20 days are flagged until there's more history.

## What it measures (and what it doesn't)

METRO's TripUpdates feed predicts when each bus will reach each upcoming stop, and drops a stop once the bus passes it. The collector keeps the last prediction it saw for every stop, so each trip-stop ends with METRO's final prediction before the bus arrived.

That's a **last prediction, not a measured arrival**. When METRO is tracking the bus, the stop drops out of the feed as the bus reaches it, so the last prediction is made within a minute or so of the arrival. But for about a quarter of stop visits it isn't, mostly where METRO wasn't tracking a bus: it kept predicting the stop for "now", then dropped a run of stops all at once with delays that never happened. Only visits whose last prediction, and every other stop dropped at the same time, was within 2 minutes of arrival count as trusted (74% so far), and the analysis uses only those. METRO doesn't publish a GTFS-Realtime vehicle positions feed, so the collector also records bus GPS positions from METRO's Transit Data API. Turning those into measured arrivals, and filling in the untrusted visits, is planned.

## How it works

- **Collector:** one Python process per feed, polling every 30 seconds on a fixed schedule. Every attempt is logged to a `poll` table, including failures and rate limits, so any gap in the data is visible and explained.
- **Storage:** PostgreSQL 18. Predictions are upserted at one row per (service date, trip, stop), and an upsert can only move forward in time, so reprocessing old data can't overwrite newer data. GPS positions are append-only.
- **Schedule:** METRO's static GTFS is loaded with `COPY`, one full version per schedule change. A daily job joins each finished day's predictions to the schedule that was in effect, corrects METRO's date label on after-midnight trips, and alerts me if more than 1% don't match.
- **Raw data:** every snapshot is saved gzipped for about a week, so the database can be rebuilt if a bug is found.
- **Operations:** Docker Compose for Postgres, systemd for the collectors, nightly backups to Backblaze B2 (restore tested), and external monitoring that emails me if new data stops arriving.
- **Code quality:** pytest against a real Postgres in CI, including tests on recorded METRO snapshots; ruff for linting and formatting.

Built with Python, protobuf (`gtfs-realtime-bindings`), psycopg 3 with hand-written SQL, PostgreSQL, Docker, systemd, and GitHub Actions.

## Related work

Projects in other cities compare realtime bus data with schedules, including [Ghost Buses](https://chihacknight.org/projects/2022/11/11/ghost-buses) in Chicago and [us-ghost-buses](https://github.com/dulcetberg/us-ghost-buses) across several US agencies. For Houston, the tools I found ([TransSee](https://www.transsee.ca/routelist?a=houston&freq=1), [wheres-my-bus](https://github.com/duckheap/wheres-my-bus)) show live arrivals only; I didn't find a public historical record of METRO's realtime feeds.

## Documentation

- [DECISIONS.md](DECISIONS.md): every significant choice, with the reasoning, tradeoffs, and what would change it
- [FEED_NOTES.md](FEED_NOTES.md): what METRO's APIs actually return, measured, including the traps (timezone-mislabeled fields, buses reporting from 0,0)
- [docs/DEPLOY.md](docs/DEPLOY.md): how the server is built, run, backed up, and restored

## Running it locally

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), PostgreSQL 18, and free API keys from [METRO's developer portal](https://api-portal.ridemetro.org/).

```bash
uv sync
cp .env.example .env          # fill in the API keys and database URLs
uv run metro-migrate
uv run metro-load-gtfs path/to/gtfs.zip --effective-from YYYY-MM-DD
uv run pytest
uv run metro-collector        # or: uv run metro-collector vehicles
uv run metro-leave-by --route 082 --from STOP_ID --to STOP_ID --arrive-by 08:30
```

## What's next

- Download new schedule versions automatically
- Serve the leave-by query through FastAPI
- Measured arrivals from GPS, compared against METRO's predictions
- A small React front end