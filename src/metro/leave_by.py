"""Answer "when should I leave?" from the history of trusted stop visits (decisions #28, #29)."""

import argparse
import os
import sys
from dataclasses import dataclass

import psycopg
from dotenv import load_dotenv

# Fewer trusted days than this is flagged as low data, but still answered.
MIN_SAMPLE = 20
# How far before the deadline to look for trips.
WINDOW_SECONDS = 90 * 60
DAY_TYPES = ("weekday", "saturday", "sunday")

# Trips in the current schedule from one stop to the other, with the delay history at both
# ends. A trip's history is every trusted visit with the same route, stop, day type, and
# scheduled time of day, so it carries across schedule versions while the times don't change.
OPTIONS = """
    WITH day_type AS (
        SELECT feed_id, service_id,
            CASE WHEN monday OR tuesday OR wednesday OR thursday OR friday THEN 'weekday'
                 WHEN saturday THEN 'saturday'
                 WHEN sunday THEN 'sunday'
            END AS day_type
        FROM gtfs_calendar
    ),
    slot AS (
        SELECT DISTINCT o.arrival_seconds AS leave_from, d.arrival_seconds AS arrive_at
        FROM gtfs_trip t
        JOIN day_type dt ON dt.feed_id = t.feed_id AND dt.service_id = t.service_id
        JOIN gtfs_stop_time o ON o.feed_id = t.feed_id AND o.trip_id = t.trip_id
        JOIN gtfs_stop_time d ON d.feed_id = t.feed_id AND d.trip_id = t.trip_id
        WHERE t.feed_id = gtfs_feed_for((now() AT TIME ZONE 'America/Chicago')::date)
            AND t.route_id = %(route_id)s
            AND dt.day_type = %(day_type)s
            AND o.stop_id = %(from_stop)s
            AND d.stop_id = %(to_stop)s
            AND o.stop_sequence < d.stop_sequence
            AND d.arrival_seconds BETWEEN %(arrive_by)s - %(window)s AND %(arrive_by)s
    ),
    history AS (
        SELECT so.stop_id,
            extract(epoch FROM so.scheduled_arrival - service_day_start(so.service_date))
                ::integer AS seconds,
            so.delay_seconds
        FROM stop_observation so
        JOIN gtfs_trip t ON t.feed_id = so.feed_id AND t.trip_id = so.trip_id
        JOIN day_type dt ON dt.feed_id = t.feed_id AND dt.service_id = t.service_id
        WHERE so.trusted
            AND so.stop_id IN (%(from_stop)s, %(to_stop)s)
            AND so.route_id = %(route_id)s
            AND dt.day_type = %(day_type)s
    )
    SELECT s.leave_from, s.arrive_at, a.n, a.p50, a.p90, a.worst, b.n, b.p10
    FROM slot s
    CROSS JOIN LATERAL (
        SELECT count(*) AS n,
            percentile_disc(0.5) WITHIN GROUP (ORDER BY delay_seconds) AS p50,
            percentile_disc(0.9) WITHIN GROUP (ORDER BY delay_seconds) AS p90,
            max(delay_seconds) AS worst
        FROM history h
        WHERE h.stop_id = %(to_stop)s AND h.seconds = s.arrive_at
    ) a
    CROSS JOIN LATERAL (
        SELECT count(*) AS n,
            percentile_disc(0.1) WITHIN GROUP (ORDER BY delay_seconds) AS p10
        FROM history h
        WHERE h.stop_id = %(from_stop)s AND h.seconds = s.leave_from
    ) b
    ORDER BY s.leave_from
"""

STOP_NAMES = """
    SELECT stop_id, name
    FROM gtfs_stop
    WHERE feed_id = gtfs_feed_for((now() AT TIME ZONE 'America/Chicago')::date)
        AND stop_id IN (%(from_stop)s, %(to_stop)s)
"""


@dataclass(frozen=True)
class TripOption:
    """One scheduled trip and its history. Times are seconds from the start of the service day."""

    leave_from: int
    arrive_at: int
    arrival_n: int
    p50_delay: int | None
    p90_delay: int | None
    worst_delay: int | None
    origin_n: int
    p10_origin_delay: int | None

    @property
    def p90_arrival(self) -> int | None:
        return None if self.p90_delay is None else self.arrive_at + self.p90_delay

    @property
    def be_at_stop(self) -> int:
        """The scheduled time, earlier if the bus left early on at least 10% of days."""
        return self.leave_from + min(self.p10_origin_delay or 0, 0)

    @property
    def low_data(self) -> bool:
        return self.arrival_n < MIN_SAMPLE


def trip_options(
    conn: psycopg.Connection,
    route_id: str,
    from_stop: str,
    to_stop: str,
    day_type: str,
    arrive_by: int,
    window: int = WINDOW_SECONDS,
) -> list[TripOption]:
    params = {
        "route_id": route_id,
        "from_stop": from_stop,
        "to_stop": to_stop,
        "day_type": day_type,
        "arrive_by": arrive_by,
        "window": window,
    }
    return [TripOption(*row) for row in conn.execute(OPTIONS, params)]


def recommend(options: list[TripOption], arrive_by: int) -> TripOption | None:
    """The latest trip that reached the destination by the deadline on 90% of days."""
    on_time = [o for o in options if o.p90_arrival is not None and o.p90_arrival <= arrive_by]
    return max(on_time, key=lambda o: o.leave_from, default=None)


def parse_clock(value: str) -> int:
    """HH:MM on the service-day clock, so 24:30 is half past midnight."""
    try:
        hours, minutes = (int(part) for part in value.split(":"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected HH:MM, got {value!r}") from None
    if not 0 <= minutes < 60:
        raise argparse.ArgumentTypeError(f"expected HH:MM, got {value!r}")
    return hours * 3600 + minutes * 60


def _clock(seconds: int, round_up: bool = False) -> str:
    minutes = -(-seconds // 60) if round_up else seconds // 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _late(delay: int | None) -> str:
    return "-" if delay is None else f"{delay / 60:+.0f} min"


def main() -> None:
    parser = argparse.ArgumentParser(description="When to leave to arrive on time, 9 days in 10.")
    parser.add_argument("--route", required=True, help="GTFS route_id, e.g. 082")
    parser.add_argument("--from", dest="from_stop", required=True, help="stop_id to board at")
    parser.add_argument("--to", dest="to_stop", required=True, help="stop_id to get off at")
    parser.add_argument("--day", choices=DAY_TYPES, default="weekday")
    parser.add_argument("--arrive-by", type=parse_clock, required=True, help="HH:MM")
    args = parser.parse_args()

    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set; copy .env.example to .env and fill it in")

    params = {"from_stop": args.from_stop, "to_stop": args.to_stop}
    with psycopg.connect(url, autocommit=True) as conn:
        names = dict(conn.execute(STOP_NAMES, params).fetchall())
        for stop in (args.from_stop, args.to_stop):
            if stop not in names:
                sys.exit(f"stop {stop} isn't in the current schedule")
        options = trip_options(
            conn, args.route, args.from_stop, args.to_stop, args.day, args.arrive_by
        )

    origin, destination = names[args.from_stop], names[args.to_stop]
    print(
        f"Route {args.route}, {args.day}, {origin} to {destination}, "
        f"arrive by {_clock(args.arrive_by)}\n"
    )
    if not options:
        sys.exit("No scheduled trips in the 90 minutes before that.")

    print("  Leaves  Arrives  Late p50  Late p90  Arrives p90  Days")
    for o in options:
        p90 = "-" if o.p90_arrival is None else _clock(o.p90_arrival, round_up=True)
        flag = " (low data)" if o.low_data else ""
        print(
            f"  {_clock(o.leave_from)}   {_clock(o.arrive_at)}    {_late(o.p50_delay):>8}"
            f"  {_late(o.p90_delay):>8}  {p90:<11}  {o.arrival_n}{flag}"
        )

    best = recommend(options, args.arrive_by)
    if best is None:
        print(f"\nNone of these reached {destination} by the deadline on 90% of days.")
        return
    print(
        f"\nTake the {_clock(best.leave_from)}: be at {origin} by {_clock(best.be_at_stop)}. "
        f"On 90% of days it reached {destination} by {_clock(best.p90_arrival, round_up=True)}."
    )
