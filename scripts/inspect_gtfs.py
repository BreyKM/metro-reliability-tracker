"""One-off check: download METRO's static GTFS, describe it, and check realtime IDs against it."""

import csv
import io
import json
import sys
import zipfile
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from google.transit import gtfs_realtime_pb2

GTFS_URL = "https://metro.resourcespace.com/pages/download.php?ref=4835&ext=zip"
HOUSTON = ZoneInfo("America/Chicago")
TRANSIT_DATA_PREFIX = "Ho414_4620_"
DEFAULT_TRIP_UPDATES = Path("tests/fixtures/tripupdates_night.pb")
DEFAULT_VEHICLES = Path("tests/fixtures/vehicles_evening.json")


def download() -> Path:
    resp = httpx.get(GTFS_URL, follow_redirects=True, timeout=60.0)
    resp.raise_for_status()
    out = Path("data/gtfs") / f"metro_gtfs_{datetime.now(UTC):%Y%m%d}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(resp.content)
    print(f"saved {out} ({len(resp.content):,} bytes)\n")
    return out


def rows(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig", newline=""))


def describe_files(zf: zipfile.ZipFile) -> None:
    print(f"{'file':<24}{'bytes':>14}{'rows':>12}  columns")
    for info in zf.infolist():
        with zf.open(info) as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8-sig", newline=""))
            header = next(reader, [])
            count = sum(1 for _ in reader)
        print(f"{info.filename:<24}{info.file_size:>14,}{count:>12,}  {', '.join(header)}")
    print()


def describe_service(zf: zipfile.ZipFile) -> None:
    names = set(zf.namelist())
    if "feed_info.txt" in names:
        for row in rows(zf, "feed_info.txt"):
            print("feed_info:", row)
    if "calendar.txt" in names:
        calendar = list(rows(zf, "calendar.txt"))
        first = min(r["start_date"] for r in calendar)
        last = max(r["end_date"] for r in calendar)
        print(f"calendar: {len(calendar)} service_ids, {first} to {last}")

    blank = late = 0
    for r in rows(zf, "stop_times.txt"):
        if not r["arrival_time"]:
            blank += 1
        elif int(r["arrival_time"].split(":")[0]) >= 24:
            late += 1
    print(f"stop_times with a blank arrival_time: {blank:,}")
    print(f"stop_times with arrival_time past 24:00:00: {late:,}")

    for name in ("routes.txt", "trips.txt", "stops.txt"):
        print(f"first row of {name}: {next(rows(zf, name))}")
    print()


def column(zf: zipfile.ZipFile, name: str, col: str) -> set[str]:
    return {r[col] for r in rows(zf, name)}


def report(label: str, found: set[str], known: set[str]) -> None:
    if not found:
        print(f"  {label}: nothing to check")
        return
    hits = len(found & known)
    print(f"  {label:<26}{hits:>8,} of {len(found):>8,}  ({100 * hits / len(found):.1f}%)")
    missing = sorted(found - known)[:5]
    if missing:
        print(f"    not in GTFS, e.g.: {missing}")


def check_ids(
    zf: zipfile.ZipFile, tu_path: Path, vehicles_path: Path
) -> gtfs_realtime_pb2.FeedMessage:
    trips = column(zf, "trips.txt", "trip_id")
    routes = column(zf, "routes.txt", "route_id")
    stops = column(zf, "stops.txt", "stop_id")

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(tu_path.read_bytes())
    updates = [e.trip_update for e in feed.entity if e.HasField("trip_update")]
    print(f"TripUpdates ({tu_path}):")
    report("trip_id in trips.txt", {tu.trip.trip_id for tu in updates}, trips)
    report("route_id in routes.txt", {tu.trip.route_id for tu in updates}, routes)
    report(
        "stop_id in stops.txt",
        {stu.stop_id for tu in updates for stu in tu.stop_time_update},
        stops,
    )

    vehicles = json.loads(vehicles_path.read_bytes())["value"]
    print(f"Vehicles ({vehicles_path}), prefix {TRANSIT_DATA_PREFIX!r} removed:")
    report(
        "TripId in trips.txt",
        {r["TripId"].removeprefix(TRANSIT_DATA_PREFIX) for r in vehicles if r.get("TripId")},
        trips,
    )
    report(
        "RouteId in routes.txt",
        {r["RouteId"].removeprefix(TRANSIT_DATA_PREFIX) for r in vehicles if r.get("RouteId")},
        routes,
    )
    print()
    return feed


def gtfs_time(service_date: date, hhmmss: str) -> datetime:
    """GTFS times count from "noon minus 12 hours" on the service date and can pass 24:00:00."""
    hours, minutes, seconds = map(int, hhmmss.split(":"))
    noon = datetime.combine(service_date, time(12), tzinfo=HOUSTON).astimezone(UTC)
    return noon - timedelta(hours=12) + timedelta(hours=hours, minutes=minutes, seconds=seconds)


def check_scheduled_times(zf: zipfile.ZipFile, feed: gtfs_realtime_pb2.FeedMessage) -> None:
    wanted = {e.trip_update.trip.trip_id for e in feed.entity if e.HasField("trip_update")}
    scheduled = {}
    for r in rows(zf, "stop_times.txt"):
        if r["trip_id"] in wanted and r["arrival_time"]:
            scheduled[(r["trip_id"], int(r["stop_sequence"]))] = r["arrival_time"]

    diffs = Counter()
    missing = 0
    for entity in feed.entity:
        tu = entity.trip_update
        if not tu.trip.start_date:
            continue
        service_date = datetime.strptime(tu.trip.start_date, "%Y%m%d").date()
        for stu in tu.stop_time_update:
            if not (stu.arrival.HasField("time") and stu.arrival.HasField("delay")):
                continue
            gtfs = scheduled.get((tu.trip.trip_id, stu.stop_sequence))
            if gtfs is None:
                missing += 1
                continue
            feed_scheduled = stu.arrival.time - stu.arrival.delay
            diffs[feed_scheduled - int(gtfs_time(service_date, gtfs).timestamp())] += 1

    total = sum(diffs.values())
    print("scheduled time: (time - delay) minus GTFS arrival_time, in seconds")
    for diff, n in diffs.most_common(5):
        print(f"  {diff:+7d} s: {n:,} ({100 * n / total:.1f}%)")
    print(f"  stop updates not found in stop_times: {missing:,}")


def main() -> None:
    args = sys.argv[1:]
    tu_path = Path(args[0]) if len(args) > 0 else DEFAULT_TRIP_UPDATES
    vehicles_path = Path(args[1]) if len(args) > 1 else DEFAULT_VEHICLES
    with zipfile.ZipFile(download()) as zf:
        describe_files(zf)
        describe_service(zf)
        feed = check_ids(zf, tu_path, vehicles_path)
        check_scheduled_times(zf, feed)


if __name__ == "__main__":
    main()
