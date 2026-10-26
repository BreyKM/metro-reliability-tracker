"""Load a static GTFS zip into Postgres as a new, never-modified schedule version."""

import argparse
import csv
import hashlib
import io
import os
import sys
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

INSERT_FEED = """
    INSERT INTO gtfs_feed (
        feed_version, sha256, source, effective_from, feed_start_date, feed_end_date
    ) VALUES (
        %(feed_version)s, %(sha256)s, %(source)s, %(effective_from)s,
        %(feed_start_date)s, %(feed_end_date)s
    )
    RETURNING feed_id
"""

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class AlreadyLoaded(Exception):
    def __init__(self, feed_id: int):
        super().__init__(f"this GTFS zip is already loaded as feed {feed_id}")
        self.feed_id = feed_id


@dataclass(frozen=True)
class LoadResult:
    feed_id: int
    counts: dict[str, int]


def parse_gtfs_time(value: str) -> int:
    """Seconds since the start of the service day. GTFS times can pass 24:00:00."""
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def load_feed(
    conn: psycopg.Connection, raw: bytes, source: str, effective_from: date
) -> LoadResult:
    """Load a GTFS zip as a new schedule version, all in one transaction.

    Raises AlreadyLoaded if a zip with the same SHA-256 has been loaded before.
    """
    sha256 = hashlib.sha256(raw).hexdigest()
    existing = conn.execute("SELECT feed_id FROM gtfs_feed WHERE sha256 = %s", (sha256,)).fetchone()
    if existing:
        raise AlreadyLoaded(existing[0])

    with zipfile.ZipFile(io.BytesIO(raw)) as zf, conn.transaction():
        names = set(zf.namelist())
        info = next(_rows(zf, "feed_info.txt"), {}) if "feed_info.txt" in names else {}
        feed_id = conn.execute(
            INSERT_FEED,
            {
                "feed_version": _text(info.get("feed_version")),
                "sha256": sha256,
                "source": source,
                "effective_from": effective_from,
                "feed_start_date": _date(info.get("feed_start_date")),
                "feed_end_date": _date(info.get("feed_end_date")),
            },
        ).fetchone()[0]

        counts = {
            "routes": _copy(
                conn,
                "gtfs_route",
                ("feed_id", "route_id", "short_name", "long_name", "route_type"),
                (
                    (
                        feed_id,
                        r["route_id"],
                        _text(r.get("route_short_name")),
                        _text(r.get("route_long_name")),
                        int(r["route_type"]),
                    )
                    for r in _rows(zf, "routes.txt")
                ),
            ),
            "stops": _copy(
                conn,
                "gtfs_stop",
                ("feed_id", "stop_id", "name", "latitude", "longitude"),
                (
                    (
                        feed_id,
                        r["stop_id"],
                        _text(r.get("stop_name")),
                        _float(r.get("stop_lat")),
                        _float(r.get("stop_lon")),
                    )
                    for r in _rows(zf, "stops.txt")
                ),
            ),
            "trips": _copy(
                conn,
                "gtfs_trip",
                (
                    "feed_id",
                    "trip_id",
                    "route_id",
                    "service_id",
                    "direction_id",
                    "headsign",
                    "shape_id",
                    "block_id",
                ),
                (
                    (
                        feed_id,
                        r["trip_id"],
                        r["route_id"],
                        r["service_id"],
                        _int(r.get("direction_id")),
                        _text(r.get("trip_headsign")),
                        _text(r.get("shape_id")),
                        _text(r.get("block_id")),
                    )
                    for r in _rows(zf, "trips.txt")
                ),
            ),
            "stop_times": _copy(
                conn,
                "gtfs_stop_time",
                (
                    "feed_id",
                    "trip_id",
                    "stop_sequence",
                    "stop_id",
                    "arrival_seconds",
                    "departure_seconds",
                    "timepoint",
                ),
                (
                    (
                        feed_id,
                        r["trip_id"],
                        int(r["stop_sequence"]),
                        r["stop_id"],
                        parse_gtfs_time(r["arrival_time"]),
                        parse_gtfs_time(r["departure_time"]),
                        _bool(r.get("timepoint")),
                    )
                    for r in _rows(zf, "stop_times.txt")
                ),
            ),
        }
        if "calendar.txt" in names:
            counts["calendar"] = _copy(
                conn,
                "gtfs_calendar",
                ("feed_id", "service_id", *DAYS, "start_date", "end_date"),
                (
                    (
                        feed_id,
                        r["service_id"],
                        *(r[day] == "1" for day in DAYS),
                        _date(r["start_date"]),
                        _date(r["end_date"]),
                    )
                    for r in _rows(zf, "calendar.txt")
                ),
            )
        if "calendar_dates.txt" in names:
            counts["calendar_dates"] = _copy(
                conn,
                "gtfs_calendar_date",
                ("feed_id", "service_id", "date", "exception_type"),
                (
                    (feed_id, r["service_id"], _date(r["date"]), int(r["exception_type"]))
                    for r in _rows(zf, "calendar_dates.txt")
                ),
            )
    return LoadResult(feed_id=feed_id, counts=counts)


def _rows(zf: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    with zf.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig", newline=""))


def _copy(
    conn: psycopg.Connection, table: str, columns: tuple[str, ...], rows: Iterable[tuple]
) -> int:
    query = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns))
    )
    count = 0
    with conn.cursor() as cur, cur.copy(query) as copy:
        for row in rows:
            copy.write_row(row)
            count += 1
    return count


def _text(value: str | None) -> str | None:
    return value or None


def _int(value: str | None) -> int | None:
    return int(value) if value else None


def _float(value: str | None) -> float | None:
    return float(value) if value else None


def _bool(value: str | None) -> bool | None:
    return value == "1" if value else None


def _date(value: str | None) -> date | None:
    return datetime.strptime(value, "%Y%m%d").date() if value else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Load a GTFS zip as a new schedule version.")
    parser.add_argument("zip", type=Path, help="path to the GTFS zip")
    parser.add_argument(
        "--effective-from",
        type=date.fromisoformat,
        required=True,
        help="first service date (YYYY-MM-DD) this version applies to",
    )
    args = parser.parse_args()

    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set; copy .env.example to .env and fill it in")

    with psycopg.connect(url, autocommit=True) as conn:
        try:
            result = load_feed(conn, args.zip.read_bytes(), str(args.zip), args.effective_from)
        except AlreadyLoaded as exc:
            print(exc)
            return
    print(f"loaded feed {result.feed_id}, effective from {args.effective_from}")
    for name, count in result.counts.items():
        print(f"  {name:<15}{count:>12,}")
