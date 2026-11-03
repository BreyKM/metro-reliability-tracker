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


def test_writes_every_file_even_without_observations(db, feed, tmp_path):
    rows = export(db, tmp_path)

    assert rows == {"routes.csv": 1, "stops.csv": 2, "route_hour.csv": 0, "stop_route.csv": 0}
    header = (tmp_path / "route_hour.csv").read_text().splitlines()[0]
    assert header == "service_date,day_type,route_id,hour," + ",".join(COUNTS)
