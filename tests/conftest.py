import os

import psycopg
import pytest
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict

from metro.migrate import apply_migrations

load_dotenv()


@pytest.fixture(scope="session")
def database_url():
    """A freshly migrated test database, reset once per test run."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("TEST_DATABASE_URL is not set; see .env.example")
    if not conninfo_to_dict(url).get("dbname", "").endswith("_test"):
        pytest.fail("refusing to reset a database whose name doesn't end in _test")

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        apply_migrations(conn)
    return url


@pytest.fixture
def db(database_url):
    """A connection to the test database with every table emptied."""
    with psycopg.connect(database_url) as conn:
        conn.execute("TRUNCATE poll, stop_prediction, vehicle_position RESTART IDENTITY")
        conn.commit()
        yield conn
