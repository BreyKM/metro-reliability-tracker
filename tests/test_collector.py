import gzip
from pathlib import Path

import httpx

from metro.collector import TRIP_UPDATES, VEHICLES, FeedCollector, next_tick

FIXTURE = (Path(__file__).parent / "fixtures" / "tripupdates_night.pb").read_bytes()


def make_collector(db, tmp_path, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return FeedCollector(TRIP_UPDATES, client, db, tmp_path)


def polls(db):
    return db.execute(
        "SELECT http_status, skipped_unchanged, row_count, error FROM poll ORDER BY poll_id"
    ).fetchall()


def test_new_snapshot_is_recorded_and_saved(db, tmp_path):
    collector = make_collector(db, tmp_path, lambda request: httpx.Response(200, content=FIXTURE))

    assert collector.poll_once() == 0

    assert polls(db) == [(200, False, 11_527, None)]
    [saved] = tmp_path.rglob("*.pb.gz")
    assert gzip.decompress(saved.read_bytes()) == FIXTURE


def test_unchanged_snapshot_is_logged_but_skipped(db, tmp_path):
    collector = make_collector(db, tmp_path, lambda request: httpx.Response(200, content=FIXTURE))

    collector.poll_once()
    collector.poll_once()

    assert polls(db) == [(200, False, 11_527, None), (200, True, None, None)]
    assert len(list(tmp_path.rglob("*.pb.gz"))) == 1


def test_rate_limit_is_logged_and_backs_off(db, tmp_path):
    collector = make_collector(
        db, tmp_path, lambda request: httpx.Response(429, headers={"Retry-After": "45"})
    )

    assert collector.poll_once() == 45

    [(status, _, _, error)] = polls(db)
    assert status == 429
    assert "45" in error


def test_network_error_is_logged(db, tmp_path):
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    collector = make_collector(db, tmp_path, handler)

    assert collector.poll_once() == 0

    [(status, _, _, error)] = polls(db)
    assert status is None
    assert error.startswith("ConnectTimeout")


def test_invalid_protobuf_is_logged(db, tmp_path):
    collector = make_collector(
        db, tmp_path, lambda request: httpx.Response(200, content=b"not protobuf")
    )

    collector.poll_once()

    [(status, _, row_count, error)] = polls(db)
    assert status == 200
    assert row_count is None
    assert error.startswith("DecodeError")


def test_next_tick_stays_on_grid():
    assert next_tick(previous=100.0, interval=30, extra_wait=0, now=101.5) == 130.0


def test_next_tick_honors_retry_after():
    assert next_tick(previous=100.0, interval=30, extra_wait=45, now=101.0) == 146.0


def test_next_tick_does_not_burst_after_falling_behind():
    assert next_tick(previous=100.0, interval=30, extra_wait=0, now=500.0) == 500.0


class PingRecorder:
    """A fake health check endpoint that remembers every request."""

    def __init__(self):
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(200)


def make_monitored_collector(db, tmp_path, feed_handler, ping_handler):
    client = httpx.Client(
        transport=httpx.MockTransport(feed_handler),
        headers={"Ocp-Apim=Subscription-Key": "metro-secret"},
    )
    ping_client = httpx.Client(transport=httpx.MockTransport(ping_handler))
    return FeedCollector(
        TRIP_UPDATES,
        client,
        db,
        tmp_path,
        ping_client=ping_client,
        healthcheck_url="https://hc.example/ping",
    )


def test_pings_after_recording_a_new_snapshot(db, tmp_path):
    pings = PingRecorder()
    collector = make_monitored_collector(
        db, tmp_path, lambda request: httpx.Response(200, content=FIXTURE), pings
    )

    collector.poll_once()

    [ping] = pings.requests
    assert str(ping.url) == "https://hc.example/ping"
    assert "ocp-apim-subscription-key" not in ping.headers


def test_failed_poll_does_not_ping(db, tmp_path):
    pings = PingRecorder()
    collector = make_monitored_collector(
        db, tmp_path, lambda request: httpx.Response(429, headers={"Retry-After": "45"}), pings
    )

    collector.poll_once()

    assert pings.requests == []


def test_pings_at_most_once_per_interval(db, tmp_path):
    pings = PingRecorder()
    collector = make_monitored_collector(
        db, tmp_path, lambda request: httpx.Response(200, content=FIXTURE), pings
    )

    collector.poll_once()
    collector.last_marker = None  # treat the same fixture as a new snapshot
    collector.poll_once()
    assert len(pings.requests) == 1

    collector._last_ping -= 61  # pretend a minute has passed
    collector.last_marker = None
    collector.poll_once()
    assert len(pings.requests) == 2


def test_ping_failure_does_not_stop_recording(db, tmp_path):
    def broken_monitor(request):
        raise httpx.ConnectError("monitor unreachable", request=request)

    collector = make_monitored_collector(
        db, tmp_path, lambda request: httpx.Response(200, content=FIXTURE), broken_monitor
    )

    assert collector.poll_once() == 0

    assert polls(db) == [(200, False, 11_527, None)]


VEHICLES_FIXTURE = (Path(__file__).parent / "fixtures" / "vehicles_evening.json").read_bytes()


def make_vehicles_collector(db, tmp_path, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return FeedCollector(VEHICLES, client, db, tmp_path)


def vehicles_polls(db):
    return db.execute(
        "SELECT feed, http_status, skipped_unchanged, row_count, no_fix_rows, error"
        " FROM poll ORDER BY poll_id"
    ).fetchall()


def test_vehicles_snapshot_is_recorded_and_saved(db, tmp_path):
    collector = make_vehicles_collector(
        db, tmp_path, lambda request: httpx.Response(200, content=VEHICLES_FIXTURE)
    )

    collector.poll_once()

    assert vehicles_polls(db) == [("vehicles", 200, False, 361, 5, None)]
    [saved] = tmp_path.rglob("*.json.gz")
    assert saved.parent.parent.name == "vehicles"
    assert gzip.decompress(saved.read_bytes()) == VEHICLES_FIXTURE


def test_unchanged_vehicles_snapshot_is_skipped(db, tmp_path):
    collector = make_vehicles_collector(
        db, tmp_path, lambda request: httpx.Response(200, content=VEHICLES_FIXTURE)
    )

    collector.poll_once()
    collector.poll_once()

    assert vehicles_polls(db)[1] == ("vehicles", 200, True, None, None, None)


def test_invalid_vehicles_json_is_logged(db, tmp_path):
    collector = make_vehicles_collector(
        db, tmp_path, lambda request: httpx.Response(200, content=b"<html>error</html>")
    )

    collector.poll_once()

    [(feed, status, _, row_count, _, error)] = vehicles_polls(db)
    assert (feed, status, row_count) == ("vehicles", 200, None)
    assert error.startswith("JSONDecodeError")
