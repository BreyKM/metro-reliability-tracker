import os
import sys
import time
from datetime import UTC, datetime

import httpx
from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2

FEED_URL = "https://api.ridemetro.org/GtfsRealtime/TripUpdates"
INTERVAL_SECONDS = 10
DURATION_SECONDS = 300


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("METRO_GTFSRT_KEY")
    if not api_key:
        sys.exit("METRO_GTFSRT_KEY is not set; copy .env.example to .env and fill it in")

    last_header = None
    deadline = time.monotonic() +  DURATION_SECONDS
    headers = {"Ocp-Apim-Subscription-Key": api_key}

    with httpx.Client(headers=headers, timeout=15.0) as client:
        while time.monotonic() < deadline:
            fetched_at = datetime.now(UTC)
            resp = client.get(FEED_URL)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("retry-after", "60"))
                print(f"{fetched_at:%H:%M:%S}  429  retry-after={retry_after}s  {resp.text[:200]}")
                time.sleep(retry_after)
                continue
            resp.raise_for_status()


            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(resp.content)
            header = feed.header.timestamp
            age = fetched_at.timestamp() - header
            marker = "NEW" if header != last_header else ""

            print(
                f"{fetched_at:%H:%M:%S}  "
                f"header {datetime.fromtimestamp(header, UTC):%H:%M:%S}  "
                f"age {age:5.1f}s  {len(resp.content):>9,} bytes  {marker}"
            )
            last_header = header
            time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()