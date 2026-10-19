"""Write polls and stop predictions to Postgres."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime

import psycopg

from metro.trip_updates import ParsedSnapshot, StopPrediction
from metro.vehicles import ParsedVehicles, VehiclePosition


@dataclass(frozen=True)
class PollRecord:
    """One fetch attempt. Only feed and requested_at are always known;
    a failed fetch may stop before the rest exist."""

    feed: str
    requested_at: datetime
    duration_ms: int | None = None
    http_status: int | None = None
    header_timestamp: datetime | None = None
    entity_count: int | None = None
    bytes: int | None = None
    skipped_unchanged: bool = False
    error: str | None = None
    row_count: int | None = None
    skipped_trips: int | None = None
    skipped_rows: int | None = None
    duplicate_rows: int | None = None
    no_fix_rows: int | None = None


INSERT_POLL = """
    INSERT INTO poll (
        feed, requested_at, duration_ms, http_status, header_timestamp, entity_count, bytes,
        skipped_unchanged, error, row_count, skipped_trips, skipped_rows, duplicate_rows, 
        no_fix_rows
    ) VALUES (
        %(feed)s, %(requested_at)s, %(duration_ms)s, %(http_status)s, %(header_timestamp)s,
        %(entity_count)s, %(bytes)s, %(skipped_unchanged)s, %(error)s, %(row_count)s,
        %(skipped_trips)s, %(skipped_rows)s, %(duplicate_rows)s, %(no_fix_rows)s
    )
    RETURNING poll_id
"""

UPSERT_STOP_PREDICTION = """
    INSERT INTO stop_prediction (
        service_date, trip_id, stop_sequence, stop_id, route_id, direction_id, vehicle_id,
        scheduled_arrival, first_predicted_arrival, first_delay_seconds,
        predicted_arrival, delay_seconds,
        first_seen_at, last_seen_at, first_poll_id, last_poll_id
    ) VALUES (
        %(service_date)s, %(trip_id)s, %(stop_sequence)s, %(stop_id)s, %(route_id)s,
        %(direction_id)s, %(vehicle_id)s,
        %(scheduled_arrival)s, %(predicted_arrival)s, %(delay_seconds)s,
        %(predicted_arrival)s, %(delay_seconds)s,
        %(seen_at)s, %(seen_at)s, %(poll_id)s, %(poll_id)s
    )
    ON CONFLICT (service_date, trip_id, stop_sequence) DO UPDATE SET
        predicted_arrival = EXCLUDED.predicted_arrival,
        delay_seconds     = EXCLUDED.delay_seconds,
        vehicle_id        = COALESCE(EXCLUDED.vehicle_id, stop_prediction.vehicle_id),
        last_seen_at      = EXCLUDED.last_seen_at,
        last_poll_id      = EXCLUDED.last_poll_id,
        update_count      = stop_prediction.update_count + 1
    WHERE stop_prediction.last_seen_at < EXCLUDED.last_seen_at
"""


def insert_poll(conn: psycopg.Connection, poll: PollRecord) -> int:
    return conn.execute(INSERT_POLL, asdict(poll)).fetchone()[0]


def upsert_stop_predictions(
    conn: psycopg.Connection,
    poll_id: int,
    seen_at: datetime,
    predictions: list[StopPrediction],
) -> None:
    if not predictions:
        return
    params = [{**asdict(p), "seen_at": seen_at, "poll_id": poll_id} for p in predictions]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_STOP_PREDICTION, params)


def record_trip_updates(
    conn: psycopg.Connection, poll: PollRecord, snapshot: ParsedSnapshot
) -> int:
    """Log the poll and upsert its predictions in one transaction.

    If anything fails, neither is written and the exception propagates, so the
    caller can log a failed poll instead. row_count is the number of predictions
    sent, not the number that changed.
    """
    poll = replace(
        poll,
        header_timestamp=snapshot.header_timestamp,
        entity_count=snapshot.entity_count,
        row_count=len(snapshot.predictions),
        skipped_trips=snapshot.skipped_trips,
        skipped_rows=snapshot.skipped_stops,
        duplicate_rows=snapshot.duplicate_stops,
    )
    with conn.transaction():
        poll_id = insert_poll(conn, poll)
        upsert_stop_predictions(conn, poll_id, snapshot.header_timestamp, snapshot.predictions)
    return poll_id


INSERT_VEHICLE_POSITION = """
    INSERT INTO vehicle_position (
        vehicle_id, reported_at, trip_id, route_id,
        latitude, longitude, delay_seconds, is_monitored, poll_id
        ) VALUES (
            %(vehicle_id)s, %(reported_at)s, %(trip_id)s, %(route_id)s,
            %(latitude)s, %(longitude)s, %(delay_seconds)s, %(is_monitored)s, %(poll_id)s
        )
        ON CONFLICT DO NOTHING
"""


def insert_vehicle_positions(
    conn: psycopg.Connection, poll_id: int, positions: list[VehiclePosition]
) -> None:
    if not positions:
        return
    params = [{**asdict(p), "poll_id": poll_id} for p in positions]
    with conn.cursor() as cur:
        cur.executemany(INSERT_VEHICLE_POSITION, params)


def record_vehicles(conn: psycopg.Connection, poll: PollRecord, parsed: ParsedVehicles) -> int:
    """Log the poll and insert its positions in one transaction.

    Positions already stored (same vehicle, report time, and trip) are skipped, so
    vehicle_position.poll_id is the poll that first saw each position. A paginated
    response is recorded, with a warning in poll.error, because positions are missing.
    """
    poll = replace(
        poll,
        header_timestamp=parsed.newest_report,
        entity_count=parsed.entity_count,
        row_count=len(parsed.positions),
        skipped_rows=parsed.skipped_rows,
        duplicate_rows=parsed.duplicate_rows,
        no_fix_rows=parsed.no_fix_rows,
        error="paginated response; positions after the first page are missing"
        if parsed.truncated
        else None,
    )
    with conn.transaction():
        poll_id = insert_poll(conn, poll)
        insert_vehicle_positions(conn, poll_id, parsed.positions)
    return poll_id
