"""Write on-time counts for the Tableau dashboard as CSV files (decisions #24, #34)."""

import argparse
import csv
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# On time is no more than 1 minute early and no more than 5 minutes late (decision #24).
ON_TIME = {"early": -60, "late": 300, "very_late": 600}

# One row per stop visit, labeled the way the dashboard groups them. The day type comes from the
# trip's service calendar, like the leave-by query (#29), and the hour is counted from the start of
# the service day the way GTFS does, so a trip after midnight is at hour 24 or later. Only trusted
# visits (#28) count as early, on time, or late; the rest only count as visits, so the dashboard
# can show how much of the service is covered.
VISITS = """
    WITH day_type AS (
        SELECT feed_id, service_id,
            CASE WHEN monday OR tuesday OR wednesday OR thursday OR friday THEN 'weekday'
                 WHEN saturday THEN 'saturday'
                 WHEN sunday THEN 'sunday'
            END AS day_type
        FROM gtfs_calendar
    ),
    visit AS (
        SELECT so.service_date, dt.day_type, so.route_id, so.stop_id,
            floor(
                extract(epoch FROM so.scheduled_arrival - service_day_start(so.service_date))
                / 3600
            )::integer AS hour,
            so.trusted::integer AS trusted,
            (so.trusted AND so.delay_seconds < %(early)s)::integer AS early,
            (so.trusted AND so.delay_seconds BETWEEN %(early)s AND %(late)s)::integer
                AS on_time,
            (so.trusted AND so.delay_seconds > %(late)s)::integer AS late,
            (so.trusted AND so.delay_seconds > %(very_late)s)::integer AS very_late
        FROM stop_observation so
        JOIN gtfs_trip t ON t.feed_id = so.feed_id AND t.trip_id = so.trip_id
        JOIN day_type dt ON dt.feed_id = t.feed_id AND dt.service_id = t.service_id
    )
"""

# Counts, not percentages or percentiles, so Tableau can add them up under any filter.
ROUTE_HOUR = (
    VISITS
    + """
    SELECT service_date, day_type, route_id, hour,
        count(*) AS visits, sum(trusted) AS trusted, sum(early) AS early,
        sum(on_time) AS on_time, sum(late) AS late, sum(very_late) AS very_late
    FROM visit
    GROUP BY service_date, day_type, route_id, hour
    ORDER BY service_date, route_id, hour
"""
)

# For the map. All dates together: by date, this would grow by tens of thousands of rows a day.
STOP_ROUTE = (
    VISITS
    + """
    SELECT day_type, route_id, stop_id,
        count(*) AS visits, sum(trusted) AS trusted, sum(early) AS early,
        sum(on_time) AS on_time, sum(late) AS late, sum(very_late) AS very_late
    FROM visit
    GROUP BY day_type, route_id, stop_id
    ORDER BY route_id, stop_id, day_type
"""
)

# Names from the newest schedule version that has each route or stop, so ones METRO has since
# dropped still have names.
ROUTES = """
    SELECT DISTINCT ON (r.route_id) r.route_id, r.short_name, r.long_name, r.route_type
    FROM gtfs_route r
    JOIN gtfs_feed f ON f.feed_id = r.feed_id
    ORDER BY r.route_id, f.effective_from DESC, f.feed_id DESC
"""

STOPS = """
    SELECT DISTINCT ON (s.stop_id) s.stop_id, s.name, s.latitude, s.longitude
    FROM gtfs_stop s
    JOIN gtfs_feed f ON f.feed_id = s.feed_id
    ORDER BY s.stop_id, f.effective_from DESC, f.feed_id DESC
"""

FILES = {
    "routes.csv": ROUTES,
    "stops.csv": STOPS,
    "route_hour.csv": ROUTE_HOUR,
    "stop_route.csv": STOP_ROUTE,
}


def write_csv(conn: psycopg.Connection, query: str, path: Path) -> int:
    with conn.cursor() as cur, path.open("w", newline="", encoding="utf-8") as f:
        cur.execute(query, ON_TIME)
        writer = csv.writer(f)
        writer.writerow(column.name for column in cur.description)
        writer.writerows(cur)
        return cur.rowcount


def export(conn: psycopg.Connection, out_dir: Path) -> dict[str, int]:
    """Write every file from one snapshot of the database. Returns the rows in each."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with conn.transaction():
        # One snapshot, so a daily build finishing mid-export can't make the files disagree.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        # Reading every observation can take longer than the API role's 5-second limit (#30).
        conn.execute("SET LOCAL statement_timeout = '2min'")
        return {name: write_csv(conn, query, out_dir / name) for name, query in FILES.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Write on-time counts as CSV files for Tableau.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/tableau"),
        help="directory for the CSV files (default: data/tableau)",
    )
    args = parser.parse_args()

    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set; copy .env.example to .env and fill it in")

    with psycopg.connect(url, autocommit=True) as conn:
        rows = export(conn, args.out)
    for name, count in rows.items():
        print(f"{args.out / name}: {count:,} rows")
