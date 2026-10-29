import argparse
from datetime import date, timedelta

import pytest
from test_gtfs import FILES, make_zip

from metro.gtfs import load_feed
from metro.leave_by import parse_clock, recommend, trip_options

# Weekday trips A (7:00 -> 7:20) and B (7:30 -> 7:50), and Saturday trip C at B's times.
TRIPS = (
    "route_id,service_id,trip_id,direction_id,trip_headsign\n076,1,A,0,\n076,1,B,0,\n076,2,C,0,\n"
)
STOP_TIMES = (
    "trip_id,arrival_time,departure_time,stop_id,stop_sequence,timepoint\n"
    "A,07:00:00,07:00:00,100,1,1\nA,07:20:00,07:20:00,200,2,1\n"
    "B,07:30:00,07:30:00,100,1,1\nB,07:50:00,07:50:00,200,2,1\n"
    "C,07:30:00,07:30:00,100,1,1\nC,07:50:00,07:50:00,200,2,1\n"
)
CALENDAR = FILES["calendar"] + "2,0,0,0,0,0,1,0,20260830,20270123\n"

A_LEAVES, B_LEAVES = 25_200, 27_000
ARRIVE_BY = 28_800  # 08:00
WEEKDAYS = [
    date(2026, 10, 19) + timedelta(days=n)
    for n in range(12)
    if (date(2026, 10, 19) + timedelta(days=n)).weekday() < 5
]
SATURDAYS = [date(2026, 10, 24), date(2026, 10, 31)]

OBSERVE = """
    INSERT INTO stop_observation (
        service_date, trip_id, stop_sequence, feed_id, route_id, direction_id, stop_id,
        scheduled_arrival, predicted_arrival, delay_seconds, lead_seconds, date_shifted,
        trusted
    )
    SELECT %(day)s, st.trip_id, st.stop_sequence, st.feed_id, t.route_id, t.direction_id,
        st.stop_id,
        service_day_start(%(day)s) + st.arrival_seconds * interval '1 second',
        service_day_start(%(day)s) + (st.arrival_seconds + %(delay)s) * interval '1 second',
        %(delay)s, 30, false, %(trusted)s
    FROM gtfs_stop_time st
    JOIN gtfs_trip t ON t.feed_id = st.feed_id AND t.trip_id = st.trip_id
    WHERE st.trip_id = %(trip_id)s AND st.stop_sequence = %(stop_sequence)s
"""


@pytest.fixture
def feed(db):
    raw = make_zip(trips=TRIPS, stop_times=STOP_TIMES, calendar=CALENDAR)
    load_feed(db, raw, "test.zip", date(2026, 10, 8))


def observe(db, day, trip_id, stop_sequence, delay, trusted=True):
    params = {
        "day": day,
        "trip_id": trip_id,
        "stop_sequence": stop_sequence,
        "delay": delay,
        "trusted": trusted,
    }
    db.execute(OBSERVE, params)


def options(db, day_type="weekday", arrive_by=ARRIVE_BY):
    return trip_options(db, "076", "100", "200", day_type, arrive_by)


def by_departure(opts):
    return {o.leave_from: o for o in opts}


def test_percentiles_come_from_the_trip_history(db, feed):
    for day, delay in zip(WEEKDAYS, range(60, 660, 60), strict=True):
        observe(db, day, "B", 2, delay)

    b = by_departure(options(db))[B_LEAVES]

    assert (b.arrival_n, b.p50_delay, b.p90_delay, b.worst_delay) == (10, 300, 540, 600)
    assert b.p90_arrival == 28_200 + 540
    assert b.low_data


def test_recommends_the_latest_trip_that_arrives_on_time(db, feed):
    for day in WEEKDAYS:
        observe(db, day, "A", 2, 120)
        observe(db, day, "B", 2, 900)

    best = recommend(options(db), ARRIVE_BY)

    assert best.leave_from == A_LEAVES


def test_untrusted_visits_are_ignored(db, feed):
    for i, day in enumerate(WEEKDAYS):
        observe(db, day, "B", 2, 120 if i % 2 else 3_000, trusted=bool(i % 2))

    b = by_departure(options(db))[B_LEAVES]

    assert (b.arrival_n, b.p90_delay) == (5, 120)


def test_be_at_the_stop_early_only_if_the_bus_leaves_early(db, feed):
    for i, day in enumerate(WEEKDAYS):
        observe(db, day, "A", 1, 60)
        observe(db, day, "B", 1, -90 if i == 0 else 0)

    by_time = by_departure(options(db))

    assert by_time[A_LEAVES].be_at_stop == A_LEAVES
    assert by_time[B_LEAVES].be_at_stop == B_LEAVES - 90


def test_day_types_keep_separate_history(db, feed):
    for day in WEEKDAYS:
        observe(db, day, "B", 2, 120)
    for day in SATURDAYS:
        observe(db, day, "C", 2, 1_200)

    weekday = by_departure(options(db))
    saturday = by_departure(options(db, day_type="saturday"))

    assert sorted(weekday) == [A_LEAVES, B_LEAVES]
    assert weekday[B_LEAVES].p90_delay == 120
    assert list(saturday) == [B_LEAVES]
    assert (saturday[B_LEAVES].arrival_n, saturday[B_LEAVES].p90_delay) == (2, 1_200)


def test_no_history_means_no_recommendation(db, feed):
    opts = options(db, arrive_by=27_600)  # 07:40, so only A arrives in time

    assert [o.leave_from for o in opts] == [A_LEAVES]
    assert opts[0].arrival_n == 0
    assert recommend(opts, 27_600) is None


def test_parses_service_day_clock_times():
    assert parse_clock("08:30") == 30_600
    assert parse_clock("24:30") == 88_200
    with pytest.raises(argparse.ArgumentTypeError):
        parse_clock("8:75")
