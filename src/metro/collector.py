"""Poll METRO's TripUpdates feed on a fixed schedule and record every attempt."""

import gzip
import logging
import signal
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv
from google.protobuf.message import DecodeError

from metro.config import Settings, load_settings
from metro.db import PollRecord, insert_poll, record_trip_updates
from metro.trip_updates import parse_trip_updates

TRIP_UPDATES_URL = "https://api.ridemetro.org/GtfsRealtime/TripUpdates"
FEED = "trip_updates"

log = logging.getLogger(__name__)


class TripUpdatesCollector:
    def __init__(self, client: httpx.Client, conn: psycopg.Connection | None, raw_dir: Path):
        self.client = client
        self.conn = conn
        self.raw_dir = raw_dir
        self.last_header: datetime | None = None

    def poll_once(self) -> float:
        """Fetch one snapshot and record the outcome, whatever it is.

        Returns how long the API asked us to wait (Retry-After), or 0.
        Raises psycopg.OperationalError if the database connection is lost.
        """
        requested_at = datetime.now(UTC)
        started = time.monotonic()
        try:
            resp = self.client.get(TRIP_UPDATES_URL)
        except httpx.HTTPError as exc:
            self._log_failure(requested_at, started, error=_describe(exc))
            return 0.0

        if resp.status_code == 429:
            retry_after = _retry_after_seconds(resp)
            self._log_failure(
                requested_at, started, http_status=429, error=f"rate limited, retry after {retry_after:.0f}s"
            )
            return retry_after
        if resp.status_code != 200:
            self._log_failure(requested_at, started, http_status=resp.status_code, error=resp.text[:200])
            return 0.0

        raw = resp.content
        poll = PollRecord(
            feed=FEED,
            requested_at=requested_at,
            duration_ms=_ms_since(started),
            http_status=200,
            bytes=len(raw)
        )
        try:
            snapshot = parse_trip_updates(raw)
        except DecodeError as exc:
            insert_poll(self.conn, replace(poll, error=_describe(exc)))
            return 0.0

        if snapshot.header_timestamp == self.last_header:
            insert_poll(
                self.conn,
                replace(poll, header_timestamp=snapshot.header_timestamp, skipped_unchanged=True),
            )
            return 0.0

        self._save_raw(raw, snapshot.header_timestamp)
        try:
            record_trip_updates(self.conn, poll, snapshot)
        except psycopg.OperationalError:
            raise
        except psycopg.Error as exc:
            insert_poll(
                self.conn,
                replace(poll, header_timestamp=snapshot.header_timestamp, error=_describe(exc)),
            )
            return 0.0

        self.last_header = snapshot.header_timestamp
        log.info(
            "recorded %s: %d rows in %d ms",
            snapshot.header_timestamp.isoformat(),
            len(snapshot.predictions),
            _ms_since(started),
        )
        return 0.0

    def _log_failure(self, requested_at: datetime, started: float, **fields) -> None:
        poll = PollRecord(feed=FEED, requested_at=requested_at, duration_ms=_ms_since(started), **fields)
        insert_poll(self.conn, poll)
        log.warning("poll failed: %s", poll.error)

    def _save_raw(self, raw: bytes, header: datetime) -> None:
        path = self.raw_dir / FEED / f"{header:%Y-%m-%d}" / f"{header:%H%M%S}Z.pb.gz"
        tmp = path.with_suffix(".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(gzip.compress(raw))
            tmp.replace(path)
        except OSError:
            log.exception("could not save raw snapshot %s", path)


def next_tick(previous: float, interval: float, extra_wait: float, now: float) -> float:
    """Schedule the next poll on a fixed grid, pushed back if the API asked us to wait.

    If we've fallen behind (a slow poll, or the machine was asleep), resume from now
    instead of firing a burst of catch-up polls into the rate limit.
    """
    tick = previous + interval
    if extra_wait:
        tick = max(tick, now + extra_wait)
    return max(tick, now)


def run(settings: Settings) -> None:
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    headers = {"Ocp-Apim-Subscription-Key": settings.gtfsrt_key}
    with httpx.Client(headers=headers, timeout=settings.request_timeout_seconds) as client:
        collector = TripUpdatesCollector(client, conn=None, raw_dir=settings.raw_dir)
        tick = time.monotonic()
        log.info("polling every %.0f s", settings.poll_interval_seconds)

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
    load_dotenv()
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run(load_settings())


if __name__ == "__main__":
    main()