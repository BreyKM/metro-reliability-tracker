from datetime import UTC, date, datetime, timedelta

import pytest
from test_gtfs import FILES, make_zip

from metro.db import PollRecord, record_trip_updates
from metro.gtfs import load_feed
from metro.observations import MatchCounts, build_day, dates_to_build
from metro.trip_updates import ParsedSnapshot, StopPrediction

MONDAY = date(2026, 10, 19)
TUESDAY = MONDAY + timedelta(days=1)
DAY_START = datetime(2026, 10, 19, 5, tzinfo=UTC)
SEEN = DAY_START + timedelta(hours=23)

# T1 starts before midnight and runs past it; T2 starts after midnight.
TRIPS = FILES["trips"] + "076,1,T2,1,Downtown\n"
STOP_TIMES = FILES["stop_times"] + "T2,24:10:00,24:10:00,100,1,1\nT2,24:30:00,24:30:00,200,2,1\n"


@pytest.fixture
def feed_id(db):
    raw = make_zip(trips=TRIPS, stop_times=STOP_TIMES)
    return load_feed(db, raw, "test.zip", date(2026, 10, 8)).feed_id


def prediction(trip_id, stop_sequence, seconds, service_date=MONDAY, delay=60):
    scheduled = DAY_START + timedelta(seconds=seconds)
    return StopPrediction(
        service_date=service_date,
        trip_id=trip_id,
        stop_sequence=stop_sequence,
        stop_id="100_A",
        route_id="076",
        direction_id=0,
        vehicle_id="2115",
        scheduled_arrival=scheduled,
        predicted_arrival=scheduled + timedelta(seconds=delay),
        delay_seconds=delay,
    )


def record(db, *predictions):
    poll = PollRecord(feed="trip_updates", requested_at=SEEN, http_status=200)
    snapshot = ParsedSnapshot(
        header_timestamp=SEEN,
        entity_count=len(predictions),
        predictions=list(predictions),
        skipped_trips=0,
        skipped_stops=0,
        duplicate_stops=0,
    )
    record_trip_updates(db, poll, snapshot)


def count(db, table):
    return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_matched_predictions_become_observations(db, feed_id):
    record(db, prediction("T1", 1, 85_800), prediction("T1", 2, 86_730, delay=-30))

    counts = build_day(db, MONDAY)

    assert counts == MatchCounts(MONDAY, exact=2, shifted=0, unmatched=0)
    rows = db.execute(
        "SELECT stop_sequence, feed_id, stop_id, delay_seconds, lead_seconds, date_shifted"
        " FROM stop_observation ORDER BY stop_sequence"
    ).fetchall()
    assert rows == [
        (1, feed_id, "100", 60, 85_860 - 82_800, False),
        (2, feed_id, "200", -30, 86_700 - 82_800, False),
    ]


def test_after_midnight_trip_labeled_next_day_moves_back(db, feed_id):
    record(db, prediction("T2", 1, 87_000, service_date=TUESDAY))

    monday = build_day(db, MONDAY)
    tuesday = build_day(db, TUESDAY)

    assert monday == MatchCounts(MONDAY, exact=0, shifted=1, unmatched=0)
    assert tuesday.unmatched == 0
    row = db.execute("SELECT service_date, date_shifted FROM stop_observation").fetchone()
    assert row == (MONDAY, True)


def test_prediction_off_schedule_is_unmatched(db, feed_id):
    record(db, prediction("T1", 1, 85_800 + 120))

    counts = build_day(db, MONDAY)

    assert counts == MatchCounts(MONDAY, exact=0, shifted=0, unmatched=1)
    assert not counts.healthy
    assert count(db, "stop_observation") == 0


def test_rebuilding_a_day_gives_the_same_result(db, feed_id):
    record(db, prediction("T1", 1, 85_800))

    first = build_day(db, MONDAY)
    second = build_day(db, MONDAY)

    assert first == second
    assert count(db, "stop_observation") == 1
    assert count(db, "schedule_match") == 1


def test_builds_from_the_first_unbuilt_date(db, feed_id):
    assert dates_to_build(db) == []
    record(db, prediction("T1", 1, 85_800))

    assert dates_to_build(db)[0] == MONDAY
    build_day(db, MONDAY)
    assert dates_to_build(db)[0] == TUESDAY


def test_more_than_one_percent_unmatched_is_unhealthy():
    assert MatchCounts(MONDAY, exact=990, shifted=0, unmatched=10).healthy
    assert not MatchCounts(MONDAY, exact=989, shifted=0, unmatched=11).healthy
    assert not MatchCounts(MONDAY, exact=0, shifted=0, unmatched=0).healthy
