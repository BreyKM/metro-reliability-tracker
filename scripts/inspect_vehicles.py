import os
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

VEHICLES_URL = "https://api.ridemetro.org/data/Vehicles"


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("METRO_TRANSIT_DATA_KEY")
    if not api_key:
        sys.exit("METRO_TRANSIT_DATA_KEY is not set; copy .env.example to .env and fill it in")

    resp = httpx.get(VEHICLES_URL, headers={"Ocp-Apim-Subscription-Key": api_key}, timeout=15.0)
    resp.raise_for_status()

    out = Path("data/raw") / f"vehicles_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(resp.content)
    print(f"saved {out}\n")

    body = resp.json()
    rows = body["value"]
    server_time = parsedate_to_datetime(resp.headers["date"])

    print(f"bytes:              {len(resp.content):,}")
    print(f"rows:               {len(rows):,}")
    print(f"next page link:     {body.get('@odata.nextLink', 'none')}")
    print(f"unique vehicles:    {len({r['VehicleId'] for r in rows}):,}")
    print(f"unique trips:       {len({r['TripId'] for r in rows}):,}")
    print(f"IsMonitored:        {dict(Counter(r['IsMonitored'] for r in rows))}")

    rows_per_vehicle = Counter(r["VehicleId"] for r in rows)
    print(f"rows per vehicle:   {dict(sorted(Counter(rows_per_vehicle.values()).items()))}")

    ages = [
        (server_time - datetime.fromisoformat(r["VehicleReportTime"])).total_seconds()
        for r in rows
        if r["VehicleReportTime"]
    ]
    if ages:
        print(
            f"report age (s):     min {min(ages):.0f}, "
            f"median {statistics.median(ages):.0f}, max {max(ages):.0f}"
        )

    prefixes = Counter("_".join((r["TripId"] or "").split("_")[:2]) for r in rows)
    print(f"TripId prefixes:    {dict(prefixes)}")

    delays = [r["Delayseconds"] for r in rows if r["Delayseconds"] is not None]
    print(f"nonzero delays:     {sum(1 for d in delays if d != 0):,} of {len(delays):,}")


if __name__ == "__main__":
    main()
