import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2

FEED_URL = "https://api.ridemetro.org/GtfsRealtime/TripUpdates"

def fetch(api_key: str) -> bytes:
    resp = httpx.get(
        FEED_URL,
        headers={"Ocp-Apim-Subscription-Key": api_key},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.content


def describe(raw: bytes) -> None:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)

    print(f"bytes:            {len(raw):,}")
    print(f"gtfs-rt version:  {feed.header.gtfs_realtime_version}")
    print(f"header timestamp: {datetime.fromtimestamp(feed.header.timestamp, UTC).isoformat()}")
    print(f"entities:         {len(feed.entity):,}")

    kinds = Counter()
    present = Counter()
    trip_relationships = Counter()
    stu_count = 0

    for entity in feed.entity:
        for kind in ("trip_update", "vehicle", "alert"):
            if entity.HasField(kind):
                kinds[kind] += 1
        if not entity.HasField("trip_update"):
            continue

        tu = entity.trip_update
        trip_relationships[gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship.Name(
            tu.trip.schedule_relationship
        )] += 1
        for field in ("trip_id", "route_id", "start_date", "start_time"):
            if tu.trip.HasField(field):
                present[f"trip.{field}"] += 1
        if tu.HasField("vehicle") and tu.vehicle.HasField("id"):
            present["vehicle.id"] += 1

        for stu in tu.stop_time_update:
            stu_count += 1
            for field in ("stop_sequence", "stop_id"):
                if stu.HasField(field):
                    present[f"stu.{field}"] += 1
            for event in ("arrival", "departure"):
                if stu.HasField(event):
                    ev = getattr(stu, event)
                    if ev.HasField("time"):
                        present[f"stu.{event}.time"] += 1
                    if ev.HasField("delay"):
                        present[f"stu.{event}.delay"] += 1

    print(f"entity kinds:     {dict(kinds)}")
    print(f"trip relationships: {dict(trip_relationships)}")
    print(f"stop_time_updates: {stu_count:,}")
    print(f"fields present (count):")
    for name, n in sorted(present.items()):
        print(f"  {name:<22} {n:,}")

    first_trip = next((e for e in feed.entity if e.HasField("trip_update")), None)
    if first_trip is not None:
        print("\nsample entity:")
        print(first_trip)


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("METRO_GTFSRT_KEY")
    if not api_key:
        sys.exit("METRO_GTFSRT_KEY is not set; copy .env.example to .env and fill it in")

    raw = fetch(api_key)
    out = Path("data/raw") / f"tripupdates_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.pb"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    print(f"saved {out}\n")
    describe(raw)


if __name__ == "__main__":
    main()