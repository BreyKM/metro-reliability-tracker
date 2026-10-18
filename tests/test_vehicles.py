import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from metro.vehicles import parse_vehicles

FIXTURES = Path(__file__).parent / "fixtures"


def make_row(**overrides):
    row = {
        "VehicleId": "2450",
        "TripId": "Ho414_4620_12062917",
        "RouteId": "Ho414_4620_073",
        "VehicleReportTime": "2026-09-25T02:07:58Z",
        "TripStartTimeLocal": "2026-09-24T20:54:00Z",
        "Latitude": 29.6662502288818,
        "Longitude": -95.3209533691406,
        "Delayseconds": -18,
        "IsMonitored": True,
    }
    row.update(overrides)
    return row


def make_body(*rows, next_link=False):
    body = {"@odata.context": "https://example/$metadata#Vehicles", "value": list(rows)}
    if next_link:
        body["@odata.nextLink"] = "https://example/Vehicles?$skip=100"
    return json.dumps(body).encode()


def test_parses_a_row():
    [p] = parse_vehicles(make_body(make_row())).positions

    assert p.vehicle_id == "2450"
    assert p.trip_id == "Ho414_4620_12062917"
    assert p.reported_at == datetime(2026, 9, 25, 2, 7, 58, tzinfo=UTC)
    assert p.latitude == pytest.approx(29.66625)
    assert p.delay_seconds == -18
    assert p.is_monitored is True


def test_keeps_both_trips_for_one_bus():
    current = make_row(TripId="Ho414_4620_12062917")
    upcoming = make_row(TripId="Ho414_4620_12063014")

    parsed = parse_vehicles(make_body(current, upcoming))

    assert {p.trip_id for p in parsed.positions} == {"Ho414_4620_12062917", "Ho414_4620_12063014"}


def test_skips_row_without_trip():
    parsed = parse_vehicles(make_body(make_row(TripId=None)))

    assert parsed.positions == []
    assert parsed.skipped_rows == 1


def test_skips_row_with_malformed_time():
    parsed = parse_vehicles(make_body(make_row(VehicleReportTime="9/25/2026 2:07:58 AM")))

    assert parsed.skipped_rows == 1


def test_skips_row_with_time_but_no_timezone():
    parsed = parse_vehicles(make_body(make_row(VehicleReportTime="2026-09-25T02:07:58")))

    assert parsed.skipped_rows == 1


def test_counts_rows_without_a_gps_fix():
    parsed = parse_vehicles(make_body(make_row(Latitude=0, Longitude=0)))

    assert parsed.positions == []
    assert parsed.no_fix_rows == 1
    assert parsed.skipped_rows == 0


def test_missing_delay_is_none():
    [p] = parse_vehicles(make_body(make_row(Delayseconds=None))).positions

    assert p.delay_seconds is None


def test_counts_duplicate_rows():
    parsed = parse_vehicles(make_body(make_row(), make_row()))

    assert len(parsed.positions) == 1
    assert parsed.duplicate_rows == 1


def test_flags_a_paginated_response():
    assert parse_vehicles(make_body(make_row(), next_link=True)).truncated is True


def test_rejects_a_response_without_a_value_list():
    with pytest.raises(ValueError):
        parse_vehicles(b'{"error": "something went wrong"}')


def test_parses_real_snapshot():
    parsed = parse_vehicles((FIXTURES / "vehicles_evening.json").read_bytes())

    assert parsed.entity_count == 366
    assert len(parsed.positions) == 361
    assert parsed.no_fix_rows == 5
    assert parsed.skipped_rows == 0
    assert parsed.duplicate_rows == 0
    assert parsed.newest_report == datetime(2026, 9, 26, 1, 26, 22, tzinfo=UTC)
    assert parsed.truncated is False
