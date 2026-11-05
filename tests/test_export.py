import csv
from datetime import date

import pytest
from test_gtfs import FILES, make_zip
from test_leave_by import CALENDAR, STOP_TIMES, TRIPS, WEEKDAYS, observe

from metro.export import export
from metro.gtfs import load_feed

COUNTS = ("visits", "trusted", "early", "on_time", "late", "very_late")


@pytest.fixture
def feed(db):
    raw = make_zip(trips=TRIPS, stop_times=STOP_TIMES, calendar=CALENDAR)
    load_feed(db, raw, "test.zip", date(2026, 10, 8))


def read(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def totals(rows):
    return {column: sum(int(row[column]) for row in rows) for column in COUNTS}


def trips(path):
    return [
        (r["service_date"], r["day_type"], r["route_id"], r["scheduled"], r["seen"])
        for r in read(path / "trips.csv")
    ]


def test_counts_follow_the_on_time_window(db, feed, tmp_path):
    # Either side of each edge: 1 minute early, 5 minutes late, and 10 minutes late.
    for day, delay in zip(WEEKDAYS[:5], [-61, -60, 300, 301, 601], strict=True):
        observe(db, day, "A", 2, delay)
    observe(db, WEEKDAYS[5], "A", 2, 3_000, trusted=False)

    export(db, tmp_path)
    rows = read(tmp_path / "route_hour.csv")

    assert {(r["route_id"], r["day_type"], r["hour"]) for r in rows} == {("076", "weekday", "7")}
    assert totals(rows) == {
        "visits": 6,
        "trusted": 5,
        "early": 1,
        "on_time": 2,
        "late": 2,
        "very_late": 1,
    }


def test_day_type_comes_from_the_service_calendar(db, feed, tmp_path):
    # Trip C runs on the Saturday schedule, so it counts as Saturday even on a weekday date, the way
    # a holiday would.
    observe(db, WEEKDAYS[0], "B", 2, 0)
    observe(db, WEEKDAYS[0], "C", 2, 0)

    export(db, tmp_path)
    rows = read(tmp_path / "stop_route.csv")

    assert sorted((r["day_type"], r["stop_id"], r["visits"]) for r in rows) == [
        ("saturday", "200", "1"),
        ("weekday", "200", "1"),
    ]


def test_after_midnight_visits_stay_with_their_service_day(db, tmp_path):
    night_trips = TRIPS + "076,1,D,0,\n"
    stop_times = STOP_TIMES + "D,24:10:00,24:10:00,100,1,1\nD,24:30:00,24:30:00,200,2,1\n"
    raw = make_zip(trips=night_trips, stop_times=stop_times, calendar=CALENDAR)
    load_feed(db, raw, "test.zip", date(2026, 10, 8))
    observe(db, WEEKDAYS[0], "D", 2, 0)

    export(db, tmp_path)
    rows = read(tmp_path / "route_hour.csv")

    assert [(r["service_date"], r["hour"]) for r in rows] == [(str(WEEKDAYS[0]), "24")]


def test_names_come_from_the_newest_schedule(db, feed, tmp_path):
    renamed = FILES["routes"].replace("Evergreen", "Evergreen Express")
    load_feed(db, make_zip(routes=renamed), "new.zip", date(2026, 11, 1))

    export(db, tmp_path)

    assert read(tmp_path / "routes.csv") == [
        {
            "route_id": "076",
            "short_name": "076",
            "long_name": "Evergreen Express",
            "route_type": "3",
        }
    ]


def test_counts_scheduled_trips_and_the_ones_seen(db, feed, tmp_path):
    # Trip A is seen both days, at two stops on the second; trip B never appears.
    observe(db, WEEKDAYS[0], "A", 2, 0)
    observe(db, WEEKDAYS[1], "A", 1, 0)
    observe(db, WEEKDAYS[1], "A", 2, 0)

    export(db, tmp_path)

    assert trips(tmp_path) == [
        (str(WEEKDAYS[0]), "weekday", "076", "2", "1"),
        (str(WEEKDAYS[1]), "weekday", "076", "2", "1"),
    ]


def test_calendar_exceptions_change_what_is_scheduled(db, tmp_path):
    # Thanksgiving: the weekday service is removed and the Saturday service is added.
    exceptions = "service_id,date,exception_type\n1,20261126,2\n2,20261126,1\n"
    raw = make_zip(trips=TRIPS, stop_times=STOP_TIMES, calendar=CALENDAR, calendar_dates=exceptions)
    load_feed(db, raw, "test.zip", date(2026, 10, 8))
    observe(db, date(2026, 11, 26), "C", 2, 0)

    export(db, tmp_path)

    assert trips(tmp_path) == [("2026-11-26", "saturday", "076", "1", "1")]


def test_rail_trips_are_not_counted(db, tmp_path):
    routes = FILES["routes"] + "700,700,Red Line,0\n"
    rail_trips = TRIPS + "700,1,R1,0,\n"
    stop_times = STOP_TIMES + "R1,08:00:00,08:00:00,100,1,1\n"
    raw = make_zip(routes=routes, trips=rail_trips, stop_times=stop_times, calendar=CALENDAR)
    load_feed(db, raw, "test.zip", date(2026, 10, 8))
    observe(db, WEEKDAYS[0], "A", 2, 0)

    export(db, tmp_path)

    assert trips(tmp_path) == [(str(WEEKDAYS[0]), "weekday", "076", "2", "1")]


def test_each_date_uses_the_schedule_in_effect(db, feed, tmp_path):
    # From Oct 20, a new version replaces trips A and B with a single trip D.
    newer_trips = "route_id,service_id,trip_id,direction_id,trip_headsign\n076,1,D,0,\n"
    newer_times = (
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence,timepoint\n"
        "D,07:00:00,07:00:00,100,1,1\nD,07:20:00,07:20:00,200,2,1\n"
    )
    raw = make_zip(trips=newer_trips, stop_times=newer_times, calendar=CALENDAR)
    load_feed(db, raw, "new.zip", date(2026, 10, 20))
    observe(db, WEEKDAYS[0], "B", 2, 0)
    observe(db, WEEKDAYS[1], "D", 2, 0)

    export(db, tmp_path)

    assert trips(tmp_path) == [
        (str(WEEKDAYS[0]), "weekday", "076", "2", "1"),
        (str(WEEKDAYS[1]), "weekday", "076", "1", "1"),
    ]


def test_writes_every_file_even_without_observations(db, feed, tmp_path):
    rows = export(db, tmp_path)

    assert rows == {
        "routes.csv": 1,
        "stops.csv": 2,
        "route_hour.csv": 0,
        "stop_route.csv": 0,
        "trips.csv": 0,
    }
    header = (tmp_path / "route_hour.csv").read_text().splitlines()[0]
    assert header == "service_date,day_type,route_id,hour," + ",".join(COUNTS)
