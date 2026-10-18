"""Turn a METRO Transit Data Vehicles response into vehicle position rows."""

import json
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class VehiclePosition:
    vehicle_id: str
    reported_at: datetime
    trip_id: str
    route_id: str
    latitude: float
    longitude: float
    delay_seconds: int | None
    is_monitored: bool


@dataclass(frozen=True)
class ParsedVehicles:
    entity_count: int
    positions: list[VehiclePosition]
    newest_report: datetime | None
    skipped_rows: int
    no_fix_rows: int
    duplicate_rows: int
    truncated: bool


def parse_vehicles(raw: bytes) -> ParsedVehicles:
    """Parse a Vehicles response. Raises ValueError if it isn't an OData list."""
    body = json.loads(raw)
    if not isinstance(body, dict) or not isinstance(body.get("value"), list):
        raise ValueError("response has no 'value' list")
    rows = body["value"]

    by_key: dict[tuple[str, datetime, str], VehiclePosition] = {}
    skipped_rows = 0
    no_fix_rows = 0
    duplicate_rows = 0

    for row in rows:
        position = _position(row)
        if position is None:
            skipped_rows += 1
            continue
        if position.latitude == 0 and position.longitude == 0:
            no_fix_rows += 1
            continue
        key = (position.vehicle_id, position.reported_at, position.trip_id)
        if key in by_key:
            duplicate_rows += 1
        by_key[key] = position

    positions = list(by_key.values())
    return ParsedVehicles(
        entity_count=len(rows),
        positions=positions,
        newest_report=max((p.reported_at for p in positions), default=None),
        skipped_rows=skipped_rows,
        no_fix_rows=no_fix_rows,
        duplicate_rows=duplicate_rows,
        truncated="@odata.nextLink" in body,
    )


def _position(row: dict) -> VehiclePosition | None:
    """Build a position from one row, or None if a required field is missing or malformed.

    Only VehicleReportTime is read for time. The *Local fields carry a Z suffix
    but are Houston time (FEED_NOTES), so they're never used.
    """
    try:
        vehicle_id = row["VehicleId"]
        trip_id = row["TripId"]
        route_id = row["RouteId"]
        reported_at = datetime.fromisoformat(row["VehicleReportTime"])
        latitude = float(row["Latitude"])
        longitude = float(row["Longitude"])
        delay = row.get("Delayseconds")
        delay_seconds = int(delay) if delay is not None else None
    except (KeyError, TypeError, ValueError):
        return None
    if not (vehicle_id and trip_id and route_id) or reported_at.tzinfo is None:
        return None
    return VehiclePosition(
        vehicle_id=str(vehicle_id),
        reported_at=reported_at,
        trip_id=str(trip_id),
        route_id=str(route_id),
        latitude=latitude,
        longitude=longitude,
        delay_seconds=delay_seconds,
        is_monitored=bool(row.get("IsMonitored")),
    )
