from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from metro.db import PollRecord, insert_poll, record_trip_updates, record_vehicles
from metro.trip_updates import ParsedSnapshot, StopPrediction, parse_trip_updates
from metro.vehicles import ParsedVehicles, VehiclePosition, parse_vehicles

FIXTURES = Path(__file__).parent / "fixtures"
SEEN = datetime(2026, 9, 25, 3, 39, tzinfo=UTC)


def make_prediction(delay=130, **overrides):
    scheduled = datetime(2026, 9, 25, 4, 0, tzinfo=UTC)
    values = dict(
        service_date=date(2026, 9, 24),
        trip_id="100",
        stop_sequence=12,
        stop_id="1234",
        route_id="099",
        direction_id=1,
        vehicle_id="2115",
        scheduled_arrival=scheduled,
        predicted_arrival=scheduled + timedelta(seconds=delay),
        delay_seconds=delay,
    )
    values.update(overrides)
    return StopPrediction(**values)


def make_snapshot(predictions, header=SEEN):
    return ParsedSnapshot(
        header_timestamp=header,
        entity_count=1,
        predictions=predictions,
        skipped_trips=0,
        skipped_stops=0,
        duplicate_stops=0,
    )


def make_poll(requested_at=SEEN + timedelta(seconds=5)):
    return PollRecord(feed="trip_updates", requested_at=requested_at, http_status=200)


def prediction_row(db):
    return db.cursor(row_factory=dict_row).execute("SELECT * FROM stop_prediction").fetchone()


def count(db, table):
    query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
    return db.execute(query).fetchone()[0]


def test_insert_poll_stores_a_failed_fetch(db):
    poll = PollRecord(feed="trip_updates", requested_at=SEEN, http_status=429, error="rate limited")

    poll_id = insert_poll(db, poll)

    row = db.execute(
        "SELECT feed, http_status, error, row_count FROM poll WHERE poll_id = %s", (poll_id,)
    ).fetchone()
    assert row == ("trip_updates", 429, "rate limited", None)


def test_first_snapshot_inserts_prediction(db):
    poll_id = record_trip_updates(db, make_poll(), make_snapshot([make_prediction()]))

    row = prediction_row(db)

    assert row["update_count"] == 1
    assert row["first_seen_at"] == row["last_seen_at"] == SEEN
    assert row["first_poll_id"] == row["last_poll_id"] == poll_id
    assert row["first_delay_seconds"] == row["delay_seconds"] == 130


def test_newer_snapshot_updates_latest_and_keeps_first(db):
    later = SEEN + timedelta(seconds=30)
    first_poll = record_trip_updates(db, make_poll(), make_snapshot([make_prediction(130)]))
    second_poll = record_trip_updates(
        db, make_poll(later), make_snapshot([make_prediction(210)], header=later)
    )

    row = prediction_row(db)

    assert row["first_delay_seconds"] == 130
    assert row["delay_seconds"] == 210
    assert row["update_count"] == 2
    assert row["first_poll_id"] == first_poll
    assert row["last_poll_id"] == second_poll
    assert row["last_seen_at"] == later


def test_same_snapshot_twice_changes_nothing(db):
    snapshot = make_snapshot([make_prediction()])

    record_trip_updates(db, make_poll(), snapshot)
    record_trip_updates(db, make_poll(), snapshot)

    assert prediction_row(db)["update_count"] == 1
    assert count(db, "stop_prediction") == 1
    assert count(db, "poll") == 2


def test_older_snapshot_does_not_overwrite_newer(db):
    later = SEEN + timedelta(seconds=30)
    record_trip_updates(db, make_poll(later), make_snapshot([make_prediction(210)], header=later))

    record_trip_updates(db, make_poll(), make_snapshot([make_prediction(130)]))

    row = prediction_row(db)
    assert row["delay_seconds"] == 210
    assert row["update_count"] == 1


def test_failed_upsert_writes_nothing(db):
    bad = make_prediction(stop_id=None)

    with pytest.raises(psycopg.errors.NotNullViolation):
        record_trip_updates(db, make_poll(), make_snapshot([bad]))

    assert count(db, "poll") == 0
    assert count(db, "stop_prediction") == 0


def test_missing_vehicle_keeps_last_known(db):
    later = SEEN + timedelta(seconds=30)
    record_trip_updates(db, make_poll(), make_snapshot([make_prediction()]))

    record_trip_updates(
        db, make_poll(later), make_snapshot([make_prediction(vehicle_id=None)], header=later)
    )

    assert prediction_row(db)["vehicle_id"] == "2115"


def test_records_real_snapshot(db):
    snapshot = parse_trip_updates((FIXTURES / "tripupdates_night.pb").read_bytes())

    poll_id = record_trip_updates(db, make_poll(), snapshot)

    assert count(db, "stop_prediction") == 11_527
    poll = db.execute(
        "SELECT row_count, skipped_rows, entity_count, header_timestamp FROM poll"
        " WHERE poll_id = %s",
        (poll_id,),
    ).fetchone()
    assert poll == (11_527, 10, 251, SEEN)


def make_position(**overrides):
    values = dict(
        vehicle_id="2450",
        reported_at=SEEN,
        trip_id="Ho414_4620_12062917",
        route_id="Ho414_4620_073",
        latitude=29.66625,
        longitude=95.32095,
        delay_seconds=-18,
        is_monitored=True,
    )
    values.update(overrides)
    return VehiclePosition(**values)


def make_vehicles(positions, no_fix=0, truncated=False):
    return ParsedVehicles(
        entity_count=len(positions) + no_fix,
        positions=positions,
        newest_report=max((p.reported_at for p in positions), default=None),
        skipped_rows=0,
        no_fix_rows=no_fix,
        duplicate_rows=0,
        truncated=truncated,
    )


def make_vehicles_poll():
    return PollRecord(feed="vehicles", requested_at=SEEN + timedelta(seconds=30), http_status=200)


def test_records_vehicle_positions(db):
    poll_id = record_vehicles(db, make_vehicles_poll(), make_vehicles([make_position()], no_fix=2))

    assert count(db, "vehicle_position") == 1
    row = db.execute(
        "SELECT row_count, no_fix_rows, header_timestamp, error FROM poll WHERE poll_id = %s",
        (poll_id,),
    ).fetchone()
    assert row == (1, 2, SEEN, None)


def test_same_position_twice_is_stored_once(db):
    parsed = make_vehicles([make_position()])

    first_poll = record_vehicles(db, make_vehicles_poll(), parsed)
    record_vehicles(db, make_vehicles_poll(), parsed)

    assert count(db, "vehicle_position") == 1
    assert db.execute("SELECT poll_id FROM vehicle_position").fetchone()[0] == first_poll
    assert count(db, "poll") == 2


def test_paginated_response_is_recorded_with_a_warning(db):
    poll_id = record_vehicles(
        db, make_vehicles_poll(), make_vehicles([make_position()], truncated=True)
    )

    error = db.execute("SELECT error FROM poll WHERE poll_id = %s", (poll_id,)).fetchone()[0]
    assert "paginated" in error
    assert count(db, "vehicle_position") == 1


def test_records_real_vehicles_snapshot(db):
    parsed = parse_vehicles((FIXTURES / "vehicles_evening.json").read_bytes())

    record_vehicles(db, make_vehicles_poll(), parsed)

    assert count(db, "vehicle_position") == 361
