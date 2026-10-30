from datetime import date

import pytest
from fastapi.testclient import TestClient
from test_gtfs import make_zip
from test_leave_by import CALENDAR, STOP_TIMES, TRIPS, WEEKDAYS, observe

from metro.api import app, get_conn
from metro.gtfs import load_feed


@pytest.fixture
def client(db):
    raw = make_zip(trips=TRIPS, stop_times=STOP_TIMES, calendar=CALENDAR)
    load_feed(db, raw, "test.zip", date(2026, 10, 8))
    app.dependency_overrides[get_conn] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


def leave_by(client, **params):
    query = {"route": "076", "from": "100", "to": "200", "arrive_by": "08:00", **params}
    return client.get("/leave-by", params=query)


def test_leave_by_answers_in_clock_times_and_minutes(client, db):
    for day in WEEKDAYS:
        observe(db, day, "B", 2, 510)

    response = leave_by(client)

    assert response.status_code == 200
    body = response.json()
    assert body["origin"] == {"stop_id": "100", "name": "Transit Center"}
    assert [t["leaves"] for t in body["trips"]] == ["07:00", "07:30"]
    assert body["recommended"] == {
        "leaves": "07:30",
        "arrives": "07:50",
        "be_at_stop": "07:30",
        "late_p50_min": 9,
        "late_p90_min": 9,
        "late_worst_min": 9,
        "arrives_p90": "07:59",
        "days": 10,
        "low_data": True,
    }


def test_unknown_stop_is_not_found(client):
    assert leave_by(client, to="999").status_code == 404


@pytest.mark.parametrize("arrive_by", ["8:75", "soon", "08:00:00"])
def test_bad_arrive_by_is_rejected(client, arrive_by):
    assert leave_by(client, arrive_by=arrive_by).status_code == 422


def test_searches_stops_by_name(client):
    assert client.get("/stops", params={"q": "transit"}).json() == [
        {"stop_id": "100", "name": "Transit Center"}
    ]
    assert client.get("/stops", params={"q": "t"}).status_code == 422


def test_lists_routes_only_in_the_direction_they_run(client):
    forward = client.get("/routes", params={"from": "100", "to": "200"}).json()
    backward = client.get("/routes", params={"from": "200", "to": "100"}).json()

    assert forward == [{"route_id": "076", "short_name": "076", "long_name": "Evergreen"}]
    assert backward == []


def test_health_with_nothing_built_yet(client):
    assert client.get("/health").json() == {"status": "ok", "observations_through": None}
