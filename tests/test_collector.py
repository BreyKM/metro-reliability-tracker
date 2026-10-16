import gzip
from pathlib import Path

import httpx

from metro.collector import TripUpdatesCollector, next_tick

FIXTURE = (Path(__file__).parent / "fixtures" / "tripupdates_night.pb").read_bytes()


def make_collector(db, tmp_path, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return TripUpdatesCollector(client, db, tmp_path)


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
