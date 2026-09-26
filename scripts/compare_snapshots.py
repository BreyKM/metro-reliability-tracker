import sys
from datetime import UTC, datetime
from pathlib import Path

from google.transit import gtfs_realtime_pb2


def load(path: Path) -> gtfs_realtime_pb2.FeedMessage:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(path.read_bytes())
    return feed


def stops_by_trip(feed: gtfs_realtime_pb2.FeedMessage) -> dict[str, list[int]]:
    return {
        e.trip_update.trip.trip_id: [stu.stop_sequence for stu in e.trip_update.stop_time_update]
        for e in feed.entity
        if e.HasField("trip_update")
    }


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("usage: compare_snapshots.py OLDER.pb NEWER.pb")
    older = load(Path(sys.argv[1]))
    newer = load(Path(sys.argv[2]))

    for label, feed in (("older", older), ("newer", newer)):
        print(f"{label} header:  {datetime.fromtimestamp(feed.header.timestamp, UTC).isoformat()}")

    a, b = stops_by_trip(older), stops_by_trip(newer)
    both = a.keys() & b.keys()
    print(f"trips:         {len(a)} older, {len(b)} newer, {len(both)} in both")
    print(f"ended:         {len(a.keys() - b.keys())} (only in older)")
    print(f"started:       {len(b.keys() - a.keys())} (only in newer)")
    print(f"no stops       {sum(1 for s in b.values() if not s)} trips in newer have zero updates")

    moved = [t for t in both if a[t] and b[t] and min(b[t]) > min(a[t])]
    print(f"stops dropped: {len(moved)} of {len(both)} trips lost their earliest stop(s)")

    if moved:
        trip_id = moved[0]
        print(f"\nexample trip {trip_id}:")
        print(f"  older first stops: {a[trip_id][:5]}")
        print(f"  newer first stops: {b[trip_id][:5]}")


if __name__ == "__main__":
    main()