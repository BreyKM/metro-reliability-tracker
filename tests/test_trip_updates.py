from datetime import UTC, date, datetime
from pathlib import Path

from google.transit import gtfs_realtime_pb2

from metro.trip_updates import parse_trip_updates

FIXTURES = Path(__file__).parent / "fixtures"
HEADER_TS = 1790307540  # 2026-09-25 03:39:00 UTC


def make_trip(
    trip_id="100",
    start_date="20260924",
    route_id="099",
    vehicle_id="2115",
    stops=(),
):
    """Build a TripUpdate. Each stop is (stop_sequence, stop_id, arrival, departure),
    where arrival/departure are (time, delay) tuples or None."""
    tu = gtfs_realtime_pb2.TripUpdate()
    tu.trip.trip_id = trip_id
    if start_date is not None:
        tu.trip.start_date = start_date
    tu.trip.route_id = route_id
    if vehicle_id is not None:
        tu.vehicle.id = vehicle_id
    for seq, stop_id, arrival, departure in stops:
        stu = tu.stop_time_update.add(stop_sequence=seq, stop_id=stop_id)
        if arrival is not None:
            stu.arrival.time, stu.arrival.delay = arrival
        if departure is not None:
            stu.departure.time, stu.departure.delay = departure
    return tu


def make_feed(*trip_updates):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = HEADER_TS
    for i, tu in enumerate(trip_updates):
        feed.entity.add(id=str(i)).trip_update.CopyFrom(tu)
    return feed.SerializeToString()


def test_parses_arrival_and_computes_scheduled_time():
    raw = make_feed(make_trip(stops=[(12, "1234", (1790308930, 130), None)]))

    snapshot = parse_trip_updates(raw)

    [p] = snapshot.predictions
    assert p.service_date == date(2026, 9, 24)
    assert p.trip_id == "100"
    assert p.stop_sequence == 12
    assert p.predicted_arrival == datetime(2026, 9, 25, 4, 2, 10, tzinfo=UTC)
    assert p.scheduled_arrival == datetime(2026, 9, 25, 4, 0, 0, tzinfo=UTC)
    assert p.delay_seconds == 130
    assert snapshot.header_timestamp == datetime(2026, 9, 25, 3, 39, tzinfo=UTC)


def test_falls_back_to_departure_when_arrival_missing():
    raw = make_feed(make_trip(stops=[(1, "1234", None, (1790308800, 60))]))

    [p] = parse_trip_updates(raw).predictions

    assert p.delay_seconds == 60
    assert p.predicted_arrival == datetime(2026, 9, 25, 4, 0, tzinfo=UTC)


def test_skips_stop_without_delay():
    tu = make_trip(stops=[(1, "1234", None, None)])
    tu.stop_time_update[0].arrival.time = 1790308800  # time set, delay missing

    snapshot = parse_trip_updates(make_feed(tu))

    assert snapshot.predictions == []
    assert snapshot.skipped_stops == 1


def test_skips_stop_marked_skipped():
    tu = make_trip(stops=[(1, "1234", (1790308800, 0), None)])
    tu.stop_time_update[0].schedule_relationship = (
        gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.SKIPPED
    )

    snapshot = parse_trip_updates(make_feed(tu))

    assert snapshot.predictions == []
    assert snapshot.skipped_stops == 1


def test_skips_trip_without_start_date():
    raw = make_feed(make_trip(start_date=None, stops=[(1, "1234", (1790308800, 0), None)]))

    snapshot = parse_trip_updates(raw)

    assert snapshot.predictions == []
    assert snapshot.skipped_trips == 1


def test_skips_trip_with_malformed_start_date():
    raw = make_feed(make_trip(start_date="2026-09-24", stops=[(1, "1234", (1790308800, 0), None)]))

    snapshot = parse_trip_updates(raw)

    assert snapshot.predictions == []
    assert snapshot.skipped_trips == 1


def test_missing_vehicle_is_none():
    raw = make_feed(make_trip(vehicle_id=None, stops=[(1, "1234", (1790308800, 0), None)]))

    [p] = parse_trip_updates(raw).predictions

    assert p.vehicle_id is None


def test_duplicate_trip_keeps_last_copy():
    first = make_trip(stops=[(1, "1234", (1790308800, 0), None)])
    second = make_trip(stops=[(1, "1234", (1790308860, 60), None)])

    snapshot = parse_trip_updates(make_feed(first, second))

    [p] = snapshot.predictions
    assert p.delay_seconds == 60
    assert snapshot.duplicate_stops == 1


def test_parse_real_snapshot():
    raw = (FIXTURES / "tripupdates_night.pb").read_bytes()

    snapshot = parse_trip_updates(raw)

    assert snapshot.header_timestamp == datetime(2026, 9, 25, 3, 39, tzinfo=UTC)
    assert snapshot.entity_count == 251
    assert len(snapshot.predictions) == 11_527
    assert snapshot.skipped_trips == 0
    assert snapshot.skipped_stops == 10
    assert snapshot.duplicate_stops == 0
