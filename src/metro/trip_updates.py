from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from google.transit import gtfs_realtime_pb2


@dataclass(frozen=True)
class StopPrediction:
    service_date: date
    trip_id: str
    stop_sequence: int
    stop_id: str
    route_id: str
    direction_id: int | None
    vehicle_id: str | None
    scheduled_arrival: datetime
    predicted_arrival: datetime
    delay_seconds: int


@dataclass(frozen=True)
class ParsedSnapshot:
    header_timestamp: datetime
    entity_count: int
    predictions: list[StopPrediction]
    skipped_trips: int
    skipped_stops: int
    duplicate_stops: int


def parse_trip_updates(raw: bytes) -> ParsedSnapshot:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)

    by_key: dict[tuple[date, str, int], StopPrediction] = {}
    skipped_trips = 0
    skipped_stops = 0
    duplicate_stops = 0

    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip = tu.trip
        if not (
            trip.HasField("trip_id") and trip.HasField("start_date") and trip.HasField("route_id")
        ):
            skipped_trips += 1
            continue

        service_date = datetime.strptime(trip.start_date, "%Y%m%d").date()
        direction_id = trip.direction_id if trip.HasField("direction_id") else None
        vehicle_id = tu.vehicle.id if tu.vehicle.HasField("id") else None

        for stu in tu.stop_time_update:
            event = _usable_event(stu)
            if event is None or not (stu.HasField("stop_sequence") and stu.HasField("stop_id")):
                skipped_stops += 1
                continue

            predicted = datetime.fromtimestamp(event.time, UTC)
            key = (service_date, trip.trip_id, stu.stop_sequence)
            if key in by_key:
                duplicate_stops += 1
            by_key[key] = StopPrediction(
                service_date=service_date,
                trip_id=trip.trip_id,
                stop_sequence=stu.stop_sequence,
                stop_id=stu.stop_id,
                route_id=trip.route_id,
                direction_id=direction_id,
                vehicle_id=vehicle_id,
                scheduled_arrival=predicted - timedelta(seconds=event.delay),
                predicted_arrival=predicted,
                delay_seconds=event.delay,
            )

    return ParsedSnapshot(
        header_timestamp=datetime.fromtimestamp(feed.header.timestamp, UTC),
        entity_count=len(feed.entity),
        predictions=list(by_key.values()),
        skipped_trips=skipped_trips,
        skipped_stops=skipped_stops,
        duplicate_stops=duplicate_stops,
    )


def _usable_event(
    stu: gtfs_realtime_pb2.TripUpdate.StopTimeUpdate,
) -> gtfs_realtime_pb2.TripUpdate.StopTimeEvent | None:

    for name in ("arrival", "departure"):
        if stu.HasField(name):
            event = getattr(stu, name)
            if event.HasField("time") and event.HasField("delay"):
                return event
    return None
