import io
import zipfile
from datetime import UTC, date, datetime

import psycopg
import pytest

from metro.gtfs import AlreadyLoaded, load_feed, parse_gtfs_time

BOM = "\ufeff"

FILES = {
    "feed_info": (
        "feed_publisher_name,feed_publisher_url,feed_lang,feed_start_date,feed_end_date,"
        "feed_version\nMETRO,http://example,en,20260830,20270123,TEST_1\n"
    ),
    "routes": "route_id,route_short_name,route_long_name,route_type\n076,076,Evergreen,3\n",
    "stops": (
        "stop_id,stop_name,stop_lat,stop_lon\n"
        "100,Transit Center,29.70,-95.40\n200,Main St,29.71,-95.41\n"
    ),
    "trips": "route_id,service_id,trip_id,direction_id,trip_headsign\n076,1,T1,0,\n",
    "stop_times": BOM
    + (
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence,timepoint\n"
        "T1,23:50:00,23:50:00,100,1,1\n"
        "T1,24:05:30,24:05:30,200,2,0\n"
        "T1,24:20:00,24:20:00,100,3,1\n"
    ),
    "calendar": (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
        "start_date,end_date\n1,1,1,1,1,1,0,0,20260830,20270123\n"
    ),
    "calendar_dates": "service_id,date,exception_type\n1,20261126,2\n",
}


def make_zip(**overrides):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in {**FILES, **overrides}.items():
            zf.writestr(f"{name}.txt", text)
    return buf.getvalue()


def test_parses_gtfs_times_past_midnight():
    assert parse_gtfs_time("00:00:00") == 0
    assert parse_gtfs_time("24:05:30") == 86_730


def test_loads_a_feed(db):
    result = load_feed(db, make_zip(), "test.zip", date(2026, 10, 8))

    assert result.counts == {
        "routes": 1,
        "stops": 2,
        "trips": 1,
        "stop_times": 3,
        "calendar": 1,
        "calendar_dates": 1,
    }
    feed = db.execute(
        "SELECT feed_version, effective_from, feed_end_date FROM gtfs_feed"
    ).fetchone()
    assert feed == ("TEST_1", date(2026, 10, 8), date(2027, 1, 23))
    times = db.execute(
        "SELECT stop_id, arrival_seconds FROM gtfs_stop_time ORDER BY stop_sequence"
    ).fetchall()
    assert times == [("100", 85_800), ("200", 86_730), ("100", 87_600)]


def test_blank_fields_are_null(db):
    load_feed(db, make_zip(), "test.zip", date(2026, 10, 8))

    assert db.execute("SELECT headsign FROM gtfs_trip").fetchone()[0] is None


def test_same_zip_is_not_loaded_twice(db):
    raw = make_zip()
    first = load_feed(db, raw, "test.zip", date(2026, 10, 8))

    with pytest.raises(AlreadyLoaded) as caught:
        load_feed(db, raw, "again.zip", date(2026, 10, 8))

    assert caught.value.feed_id == first.feed_id
    assert db.execute("SELECT count(*) FROM gtfs_feed").fetchone()[0] == 1


def test_failed_load_leaves_nothing(db):
    broken = make_zip(stop_times=FILES["stop_times"] + "T1,24:30:00,24:30:00,999,4,0\n")

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        load_feed(db, broken, "broken.zip", date(2026, 10, 8))

    assert db.execute("SELECT count(*) FROM gtfs_feed").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM gtfs_stop").fetchone()[0] == 0


def test_service_day_starts_at_midnight_on_a_normal_day(db):
    start = db.execute("SELECT service_day_start('2026-10-26')").fetchone()[0]

    assert start == datetime(2026, 10, 26, 5, 0, tzinfo=UTC)  # midnight CDT


def test_service_day_follows_noon_minus_12_hours_when_clocks_change(db):
    start = db.execute("SELECT service_day_start('2026-11-01')").fetchone()[0]

    assert start == datetime(2026, 11, 1, 6, 0, tzinfo=UTC)  # 1:00 AM CDT, not midnight


def test_picks_the_schedule_in_effect_on_a_date(db):
    first = load_feed(db, make_zip(), "a.zip", date(2026, 10, 8)).feed_id
    second = load_feed(
        db,
        make_zip(feed_info=FILES["feed_info"].replace("TEST_1", "TEST_2")),
        "b.zip",
        date(2027, 1, 24),
    ).feed_id

    def feed_for(day):
        return db.execute("SELECT gtfs_feed_for(%s)", (day,)).fetchone()[0]

    assert feed_for(date(2026, 1, 1)) is None
    assert feed_for(date(2026, 12, 1)) == first
    assert feed_for(date(2027, 1, 24)) == second
