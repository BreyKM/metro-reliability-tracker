"""Join finished service days of predictions to the schedule and record how well they matched."""

import argparse
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import httpx
import psycopg
from dotenv import load_dotenv

# A day with more unmatched predictions than this most likely ran on a schedule version
# that isn't loaded.
MAX_UNMATCHED_SHARE = 0.01

log = logging.getLogger(__name__)

# The newest service date that's finished: its after-midnight trips are stored under the
# next date (decision #26), so date S is complete once S + 1 is over.
LAST_FINISHED = "SELECT (now() AT TIME ZONE 'America/Chicago')::date - 2"

# Finished service dates that haven't been built yet.
DATES_TO_BUILD = """
    SELECT d::date
    FROM generate_series(
        coalesce(
            (SELECT max(service_date) + 1 FROM schedule_match),
            (SELECT min(service_date) FROM stop_prediction)
        ),
        (now() AT TIME ZONE 'America/Chicago')::date - 2,
        interval '1 day'
    ) AS d
"""

# Rows stored under the day that match its schedule, plus rows stored under the next day
# that match this day's schedule (METRO's next-day label, decision #26). Scheduled times
# must match to the second. A row is trusted only if every stop of its trip that dropped
# out of the feed in the same poll was predicted within 2 minutes of that poll (#28).
INSERT_OBSERVATIONS = """
    WITH candidate AS (
        SELECT trip_id, stop_sequence, scheduled_arrival, predicted_arrival, last_seen_at,
            service_date <> %(day)s AS date_shifted,
            bool_and(
                predicted_arrival - last_seen_at
                    BETWEEN interval '-2 minutes' AND interval '2 minutes'
            ) OVER (PARTITION BY service_date, trip_id, last_poll_id) AS trusted
        FROM stop_prediction
        WHERE service_date IN (%(day)s, %(day)s + 1)
    )
    INSERT INTO stop_observation (
        service_date, trip_id, stop_sequence, feed_id, route_id, direction_id, stop_id,
        scheduled_arrival, predicted_arrival, delay_seconds, lead_seconds, date_shifted,
        trusted
    )
    SELECT %(day)s, c.trip_id, c.stop_sequence, st.feed_id, t.route_id, t.direction_id,
        st.stop_id, c.scheduled_arrival, c.predicted_arrival,
        extract(epoch FROM c.predicted_arrival - c.scheduled_arrival)::integer,
        extract(epoch FROM c.predicted_arrival - c.last_seen_at)::integer,
        c.date_shifted, c.trusted
    FROM candidate c
    JOIN gtfs_stop_time st
      ON st.feed_id = gtfs_feed_for(%(day)s)
     AND st.trip_id = c.trip_id
     AND st.stop_sequence = c.stop_sequence
    JOIN gtfs_trip t ON t.feed_id = st.feed_id AND t.trip_id = st.trip_id
    WHERE c.scheduled_arrival
        = service_day_start(%(day)s) + st.arrival_seconds * interval '1 second'
"""

COUNT_OBSERVATIONS = """
    SELECT count(*) FILTER (WHERE NOT date_shifted),
           count(*) FILTER (WHERE date_shifted),
           count(*) FILTER (WHERE trusted)
    FROM stop_observation
    WHERE service_date = %(day)s
"""

# Rows stored under the day that match neither its schedule nor the day before's.
COUNT_UNMATCHED = """
    WITH d AS MATERIALIZED (
        SELECT gtfs_feed_for(day) AS feed_id, service_day_start(day) AS day_start
        FROM (VALUES (%(day)s::date), (%(day)s::date - 1)) AS v (day)
    )
    SELECT count(*)
    FROM stop_prediction sp
    WHERE sp.service_date = %(day)s
      AND NOT EXISTS (
          SELECT 1
          FROM d
          JOIN gtfs_stop_time st
            ON st.feed_id = d.feed_id
           AND st.trip_id = sp.trip_id
           AND st.stop_sequence = sp.stop_sequence
          WHERE sp.scheduled_arrival = d.day_start + st.arrival_seconds * interval '1 second'
      )
"""

INSERT_MATCH = """
    INSERT INTO schedule_match (service_date, feed_id, exact, shifted, unmatched, trusted)
    VALUES (
        %(service_date)s, gtfs_feed_for(%(service_date)s),
        %(exact)s, %(shifted)s, %(unmatched)s, %(trusted)s
    )
"""


@dataclass(frozen=True)
class MatchCounts:
    service_date: date
    exact: int
    shifted: int
    unmatched: int
    trusted: int

    @property
    def trusted_share(self) -> float:
        observed = self.exact + self.shifted
        return self.trusted / observed if observed else 0.0

    @property
    def unmatched_share(self) -> float:
        total = self.exact + self.shifted + self.unmatched
        return self.unmatched / total if total else 1.0

    @property
    def healthy(self) -> bool:
        return self.unmatched_share <= MAX_UNMATCHED_SHARE


def dates_to_build(conn: psycopg.Connection) -> list[date]:
    return [row[0] for row in conn.execute(DATES_TO_BUILD)]


def build_day(conn: psycopg.Connection, day: date) -> MatchCounts:
    """Rebuild one service date's observations and match counts, in one transaction."""
    params = {"day": day}
    with conn.transaction():
        conn.execute("DELETE FROM stop_observation WHERE service_date = %(day)s", params)
        conn.execute("DELETE FROM schedule_match WHERE service_date = %(day)s", params)
        conn.execute(INSERT_OBSERVATIONS, params)
        exact, shifted, trusted = conn.execute(COUNT_OBSERVATIONS, params).fetchone()
        unmatched = conn.execute(COUNT_UNMATCHED, params).fetchone()[0]
        counts = MatchCounts(day, exact, shifted, unmatched, trusted)
        conn.execute(INSERT_MATCH, asdict(counts))
    return counts


def _ping(url: str, healthy: bool) -> None:
    """Report to the external monitor. /fail alerts right away instead of after the grace time."""
    try:
        httpx.get(url if healthy else f"{url}/fail", timeout=5.0)
    except httpx.HTTPError as exc:
        log.warning("health check ping failed: %s", exc)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Join finished service days to the schedule and check the match rate."
    )
    parser.add_argument(
        "--from",
        dest="start",
        type=date.fromisoformat,
        help="rebuild service dates starting here (YYYY-MM-DD) instead of building new ones",
    )
    parser.add_argument(
        "--to", dest="end", type=date.fromisoformat, help="last date to rebuild (default: --from)"
    )
    args = parser.parse_args(argv)
    if args.end and not args.start:
        parser.error("--to needs --from")
    if args.end and args.end < args.start:
        parser.error("--to is before --from")
    return args


def main() -> None:
    args = parse_args()

    load_dotenv()
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set; copy .env.example to .env and fill it in")
    healthcheck_url = os.environ.get("OBSERVATIONS_HEALTHCHECK_URL") or None

    healthy = True
    with psycopg.connect(url, autocommit=True) as conn:
        if args.start:
            end = args.end or args.start
            last_finished = conn.execute(LAST_FINISHED).fetchone()[0]
            if end > last_finished:
                sys.exit(
                    f"{end} isn't finished yet; "
                    f"the newest date that can be built is {last_finished}"
                )
            days = [args.start + timedelta(days=n) for n in range((end - args.start).days + 1)]
        else:
            days = dates_to_build(conn)
        if not days:
            log.info("nothing to build")
        for day in days:
            counts = build_day(conn, day)
            log.log(
                logging.INFO if counts.healthy else logging.WARNING,
                "built %s: %d exact, %d shifted, %d unmatched (%.2f%%), %d trusted (%.1f%%)",
                day,
                counts.exact,
                counts.shifted,
                counts.unmatched,
                100 * counts.unmatched_share,
                counts.trusted,
                100 * counts.trusted_share,
            )
            healthy = healthy and counts.healthy

    if healthcheck_url:
        _ping(healthcheck_url, healthy)
    if not healthy:
        sys.exit(f"over {MAX_UNMATCHED_SHARE:.0%} of predictions unmatched; is a schedule missing?")
