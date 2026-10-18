"""Collector settings, reads from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path

# Which environment variables hold each feed's API key and health check URL.
FEED_ENV = {
    "trip_updates": ("METRO_GTFSRT_KEY", "HEALTHCHECK_URL"),
}


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_key: str
    poll_interval_seconds: float = 30.0
    request_timeout_seconds: float = 15.0
    raw_dir: Path = Path("data/raw")
    healthcheck_url: str | None = None


def load_settings(feed: str = "trip_updates") -> Settings:
    key_var, healthcheck_var = FEED_ENV[feed]
    missing = [name for name in ("DATABASE_URL", key_var) if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"missing environment variables: {', '.join(missing)}")
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        api_key=os.environ[key_var],
        poll_interval_seconds=float(os.environ.get("POLL_INTERVAL_SECONDS", 30)),
        request_timeout_seconds=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", 15)),
        raw_dir=Path(os.environ.get("RAW_DIR", "data/raw")),
        healthcheck_url=os.environ.get(healthcheck_var) or None,
    )
