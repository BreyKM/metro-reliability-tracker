"""Read-only HTTP API for the leave-by query (decision #30)."""

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Literal, Self

import psycopg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from metro.leave_by import (
    STOP_NAMES,
    TripOption,
    clock,
    late_minutes,
    parse_clock,
    recommend,
    trip_options,
)

# Every query runs read-only and is cancelled after 5 seconds, so the API can't write or
# hold up the collectors that share the database.
CONNECTION_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=5s"

SEARCH_STOPS = """
    SELECT stop_id, name
    FROM gtfs_stop
    WHERE feed_id = gtfs_feed_for((now() AT TIME ZONE 'America/Chicago')::date)
        AND name ILIKE '%%' || %(q)s || '%%'
    ORDER BY name, stop_id
    LIMIT 20
"""

# Routes with a trip that stops at one and then the other, so only the direction they run.
ROUTES_BETWEEN = """
    SELECT DISTINCT r.route_id, r.short_name, r.long_name
    FROM gtfs_trip t
    JOIN gtfs_route r ON r.feed_id = t.feed_id AND r.route_id = t.route_id
    JOIN gtfs_stop_time o ON o.feed_id = t.feed_id AND o.trip_id = t.trip_id
    JOIN gtfs_stop_time d ON d.feed_id = t.feed_id AND d.trip_id = t.trip_id
    WHERE t.feed_id = gtfs_feed_for((now() AT TIME ZONE 'America/Chicago')::date)
        AND o.stop_id = %(from_stop)s
        AND d.stop_id = %(to_stop)s
        AND o.stop_sequence < d.stop_sequence
    ORDER BY r.route_id
"""

DayType = Literal["weekday", "saturday", "sunday"]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set; copy .env.example to .env and fill it in")
    with ConnectionPool(
        url,
        min_size=1,
        max_size=4,
        kwargs={"autocommit": True, "options": CONNECTION_OPTIONS},
        check=ConnectionPool.check_connection,
    ) as pool:
        app.state.pool = pool
        yield


app = FastAPI(title="Houston METRO Reliability Tracker", lifespan=lifespan)


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    with request.app.state.pool.connection() as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(get_conn)]


class Health(BaseModel):
    status: str
    observations_through: date | None


class Stop(BaseModel):
    stop_id: str
    name: str


class Route(BaseModel):
    route_id: str
    short_name: str | None
    long_name: str | None


class Trip(BaseModel):
    """One scheduled trip. Arrivals round up and boarding times round down (decision #29)."""

    leaves: str
    arrives: str
    be_at_stop: str
    late_p50_min: int | None
    late_p90_min: int | None
    late_worst_min: int | None
    arrives_p90: str | None
    days: int
    low_data: bool

    @classmethod
    def of(cls, option: TripOption) -> Self:
        p90 = option.p90_arrival
        return cls(
            leaves=clock(option.leave_from),
            arrives=clock(option.arrive_at),
            be_at_stop=clock(option.be_at_stop),
            late_p50_min=late_minutes(option.p50_delay),
            late_p90_min=late_minutes(option.p90_delay),
            late_worst_min=late_minutes(option.worst_delay),
            arrives_p90=None if p90 is None else clock(p90, round_up=True),
            days=option.arrival_n,
            low_data=option.low_data,
        )


class LeaveBy(BaseModel):
    route_id: str
    day: DayType
    arrive_by: str
    origin: Stop
    destination: Stop
    trips: list[Trip]
    recommended: Trip | None


@app.get("/health")
def health(conn: Conn) -> Health:
    built = conn.execute("SELECT max(service_date) FROM schedule_match").fetchone()[0]
    return Health(status="ok", observations_through=built)


@app.get("/stops")
def search_stops(conn: Conn, q: Annotated[str, Query(min_length=2)]) -> list[Stop]:
    rows = conn.execute(SEARCH_STOPS, {"q": q})
    return [Stop(stop_id=stop_id, name=name) for stop_id, name in rows]


@app.get("/routes")
def routes_between(
    conn: Conn,
    from_stop: Annotated[str, Query(alias="from")],
    to_stop: Annotated[str, Query(alias="to")],
) -> list[Route]:
    rows = conn.execute(ROUTES_BETWEEN, {"from_stop": from_stop, "to_stop": to_stop})
    return [Route(route_id=r, short_name=s, long_name=n) for r, s, n in rows]


@app.get("/leave-by")
def leave_by(
    conn: Conn,
    route: str,
    from_stop: Annotated[str, Query(alias="from")],
    to_stop: Annotated[str, Query(alias="to")],
    arrive_by: Annotated[str, Query(pattern=r"^\d{1,2}:[0-5]\d$")],
    day: DayType = "weekday",
) -> LeaveBy:
    names = dict(conn.execute(STOP_NAMES, {"from_stop": from_stop, "to_stop": to_stop}))
    for stop in (from_stop, to_stop):
        if stop not in names:
            raise HTTPException(404, f"stop {stop} isn't in the current schedule")

    deadline = parse_clock(arrive_by)
    options = trip_options(conn, route, from_stop, to_stop, day, deadline)
    best = recommend(options, deadline)
    return LeaveBy(
        route_id=route,
        day=day,
        arrive_by=clock(deadline),
        origin=Stop(stop_id=from_stop, name=names[from_stop]),
        destination=Stop(stop_id=to_stop, name=names[to_stop]),
        trips=[Trip.of(o) for o in options],
        recommended=None if best is None else Trip.of(best),
    )
