"""Collector settings, reads from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    gtfsrt_key: str
    poll_interval_seconds: float = 30.0
    request_timeout_seconds: float = 15.0
    raw_dir: Path = Path("data/raw")


def load_settings() -> Settings:
    missing = [name for name in ("DATABASE_URL", "METRO_GTFSRT_KEY") if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"missing environment variables: {', '.join(missing)}")
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        gtfsrt_key=os.environ["METRO_GTFSRT_KEY"],
        poll_interval_seconds=float(os.environ.get("POLL_INTERVAL_SECONDS", 30)),
        request_timeout_seconds=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", 15)),
        raw_dir=Path(os.environ.get("RAW_DIR", "data/raw")),
    )
