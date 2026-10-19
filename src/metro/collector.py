"""Poll a METRO feed on a fixed schedule and record every attempt."""

import argparse
import gzip
import logging
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
from dotenv import load_dotenv
from google.protobuf.message import DecodeError

from metro.config import Settings, load_settings
from metro.db import PollRecord, insert_poll, record_trip_updates, record_vehicles
from metro.trip_updates import parse_trip_updates
from metro.vehicles import parse_vehicles

PING_INTERVAL_SECONDS = 60

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Feed:
    """Everything that differs between the feeds the collector can poll."""

    name: str  # poll.feed value and raw folder name
    url: str
    raw_suffix: str
    parse: Callable[[bytes], Any]
    parse_errors: tuple[type[Exception], ...]
    marker: Callable[[Any], datetime | None]  # changes whenever the data does
    record: Callable[[psycopg.Connection, PollRecord, Any], int]
    row_count: Callable[[Any], int]


TRIP_UPDATES = Feed(
    name="trip_updates",
    url="https://api.ridemetro.org/GtfsRealtime/TripUpdates",
    raw_suffix=".pb.gz",
    parse=parse_trip_updates,
    parse_errors=(DecodeError,),
    marker=lambda snapshot: snapshot.header_timestamp,
    record=record_trip_updates,
    row_count=lambda snapshot: len(snapshot.predictions),
)

VEHICLES = Feed(
    name="vehicles",
    url="https://api.ridemetro.org/data/Vehicles",
    raw_suffix=".json.gz",
    parse=parse_vehicles,
    parse_errors=(ValueError,),
    marker=lambda parsed: parsed.newest_report,
    record=record_vehicles,
    row_count=lambda parsed: len(parsed.positions),
)

FEEDS = {feed.name: feed for feed in (TRIP_UPDATES, VEHICLES)}


class FeedCollector:
    def __init__(
        self,
        feed: Feed,
        client: httpx.Client,
        conn: psycopg.Connection | None,
        raw_dir: Path,
        ping_client: httpx.Client | None = None,
        healthcheck_url: str | None = None,
    ):
        self.feed = feed
        self.client = client
        self.conn = conn
        self.raw_dir = raw_dir
        self.ping_client = ping_client
        self.healthcheck_url = healthcheck_url
        self.last_marker: datetime | None = None
        self._last_ping: float | None = None

    def poll_once(self) -> float:
        """Fetch one response and record the outcome, whatever it is.

        Returns how long the API asked us to wait (Retry-After), or 0.
        Raises psycopg.OperationalError if the database connection is lost.
        """
        requested_at = datetime.now(UTC)
        started = time.monotonic()
        try:
            resp = self.client.get(self.feed.url)
        except httpx.HTTPError as exc:
            self._log_failure(requested_at, started, error=_describe(exc))
            return 0.0

        if resp.status_code == 429:
            retry_after = _retry_after_seconds(resp)
            self._log_failure(
                requested_at,
                started,
                http_status=429,
                error=f"rate limited, retry after {retry_after:.0f}s",
            )
            return retry_after
        if resp.status_code != 200:
            self._log_failure(
                requested_at, started, http_status=resp.status_code, error=resp.text[:200]
            )
            return 0.0

        raw = resp.content
        poll = PollRecord(
            feed=self.feed.name,
            requested_at=requested_at,
            duration_ms=_ms_since(started),
            http_status=200,
            bytes=len(raw),
        )
        try:
            parsed = self.feed.parse(raw)
        except self.feed.parse_errors as exc:
            insert_poll(self.conn, replace(poll, error=_describe(exc)))
            return 0.0

        marker = self.feed.marker(parsed)
        poll = replace(poll, header_timestamp=marker)
        if marker is not None and marker == self.last_marker:
            insert_poll(self.conn, replace(poll, skipped_unchanged=True))
            return 0.0

        self._save_raw(raw, marker or requested_at)
        try:
            self.feed.record(self.conn, poll, parsed)
        except psycopg.OperationalError:
            raise
        except psycopg.Error as exc:
            insert_poll(self.conn, replace(poll, error=_describe(exc)))
            return 0.0

        self.last_marker = marker
        self._ping()
        log.info(
            "recorded %s %s: %d rows in %d ms",
            self.feed.name,
            (marker or requested_at).isoformat(),
            self.feed.row_count(parsed),
            _ms_since(started),
        )
        return 0.0

    def _log_failure(self, requested_at: datetime, started: float, **fields) -> None:
        poll = PollRecord(
            feed=self.feed.name, requested_at=requested_at, duration_ms=_ms_since(started), **fields
        )
        insert_poll(self.conn, poll)
        log.warning("%s poll failed: %s", self.feed.name, poll.error)

    def _save_raw(self, raw: bytes, stamp: datetime) -> None:
        folder = self.raw_dir / self.feed.name / f"{stamp:%Y-%m-%d}"
        path = folder / f"{stamp:%H%M%S}Z{self.feed.raw_suffix}"
        tmp = path.with_suffix(".tmp")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(gzip.compress(raw))
            tmp.replace(path)
        except OSError:
            log.exception("could not save raw snapshot %s", path)

    def _ping(self) -> None:
        """Tell the external monitor a new snapshot was recorded. Never raises."""
        if self.ping_client is None or self.healthcheck_url is None:
            return
        now = time.monotonic()
        if self._last_ping is not None and now - self._last_ping < PING_INTERVAL_SECONDS:
            return
        try:
            self.ping_client.get(self.healthcheck_url)
        except httpx.HTTPError as exc:
            log.warning("health check ping failed: %s", exc)
            return
        self._last_ping = now


def next_tick(previous: float, interval: float, extra_wait: float, now: float) -> float:
    """Schedule the next poll on a fixed grid, pushed back if the API asked us to wait.

    If we've fallen behind (a slow poll, or the machine was asleep), resume from now
    instead of firing a burst of catch-up polls into the rate limit.
    """
    tick = previous + interval
    if extra_wait:
        tick = max(tick, now + extra_wait)
    return max(tick, now)


def run(feed: Feed, settings: Settings) -> None:
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    headers = {"Ocp-Apim-Subscription-Key": settings.api_key}
    with (
        httpx.Client(headers=headers, timeout=settings.request_timeout_seconds) as client,
        httpx.Client(timeout=5.0) as ping_client,
    ):
        collector = FeedCollector(
            feed,
            client,
            conn=None,
            raw_dir=settings.raw_dir,
            ping_client=ping_client,
            healthcheck_url=settings.healthcheck_url,
        )
        tick = time.monotonic()
        log.info("polling %s every %.0f s", feed.name, settings.poll_interval_seconds)
        log.info("health check pings %s", "on" if settings.healthcheck_url else "off")

        while not stop.is_set():
            extra_wait = 0.0
            try:
                if collector.conn is None or collector.conn.closed:
                    collector.conn = psycopg.connect(settings.database_url, autocommit=True)
                    log.info("connected to database")
                extra_wait = collector.poll_once()
            except psycopg.OperationalError as exc:
                log.error("database unavailable: %s", exc)
                if collector.conn is not None:
                    collector.conn.close()
                collector.conn = None
            except Exception:
                log.exception("unexpected error during poll")

            tick = next_tick(tick, settings.poll_interval_seconds, extra_wait, time.monotonic())
            stop.wait(max(0.0, tick - time.monotonic()))

        if collector.conn is not None:
            collector.conn.close()
    log.info("stopped")


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


def _retry_after_seconds(resp: httpx.Response) -> float:
    try:
        return max(float(resp.headers.get("retry-after", "60")), 0.0)
    except ValueError:
        return 60.0


def _ms_since(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll a METRO feed and record every attempt.")
    parser.add_argument("feed", nargs="?", default="trip_updates", choices=sorted(FEEDS))
    args = parser.parse_args()

    load_dotenv()
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run(FEEDS[args.feed], load_settings(args.feed))


if __name__ == "__main__":
    main()
