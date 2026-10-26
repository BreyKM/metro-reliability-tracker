import psycopg

from metro.migrate import MIGRATIONS_DIR, apply_migrations


def test_all_migrations_are_recorded(db):
    expected = sorted(p.name.split("_", 1)[0] for p in MIGRATIONS_DIR.glob("*.sql"))

    versions = [row[0] for row in db.execute("SELECT version FROM schema_migration ORDER BY 1")]

    assert versions == expected


def test_migrations_create_tables(db):
    rows = db.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")

    tables = {row[0] for row in rows}

    assert {
        "schema_migration",
        "poll",
        "stop_prediction",
        "vehicle_position",
        "gtfs_feed",
        "gtfs_stop_time",
    } <= tables


def test_rerunning_migrations_applies_nothing(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        assert apply_migrations(conn) == []
