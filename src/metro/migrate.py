"""Apply SQL migrations from the migrations/ directory in version order."""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def applied_versions(conn: psycopg.Connection) -> set[str]:
    if conn.execute("SELECT to_regclass('schema_migration')").fetchone()[0] is None:
        return set()
    return {row[0] for row in conn.execute("SELECT version FROM schema_migration")}


def apply_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Run each migration file whose version isn't recorded yet. Returns the files applied.

    Each file manages its own transaction (BEGIN ... COMMIT) and records its own
    version, so the connection must be in autocommit mode.
    """
    if not conn.autocommit:
        raise ValueError("apply_migrations needs an autocommit connection")

    applied = applied_versions(conn)
    newly_applied = []
    for path in sorted(directory.glob("*.sql")):
        version = path.name.split("_", 1)[0]
        if version in applied:
            continue
        conn.execute(path.read_text(encoding="utf-8"))
        if version not in applied_versions(conn):
            raise RuntimeError(f"{path.name} ran but did not record version {version!r}")
        newly_applied.append(path.name)
    return newly_applied


def main() -> None:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set; copy .env.example to .env and fill it in")

    with psycopg.connect(url, autocommit=True) as conn:
        newly_applied = apply_migrations(conn)

    if newly_applied:
        for name in newly_applied:
            print(f"applied {name}")
    else:
        print("database is up to date")


if __name__ == "__main__":
    main()