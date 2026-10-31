"""Check explicitly configured test databases without creating containers or databases."""

import argparse
import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool


def test_database_url(name: str) -> URL:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"Set {name} to an existing PostgreSQL database ending in _test")
    try:
        url = make_url(value)
        if (
            url.get_backend_name() != "postgresql"
            or not url.host
            or not (url.database or "").endswith("_test")
        ):
            raise ValueError
    except Exception:
        raise ValueError(
            f"{name} must name a dedicated PostgreSQL database ending in _test"
        ) from None
    return url.set(drivername="postgresql+psycopg")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="+", choices=["TEST_DATABASE_URL", "E2E_DATABASE_URL"])
    args = parser.parse_args()
    try:
        # Validate every name before opening any connection. Never print connection strings.
        urls = [(name, test_database_url(name)) for name in args.names]
    except ValueError as error:
        raise SystemExit(str(error)) from None
    checked: set[URL] = set()
    for name, url in urls:
        if url not in checked:
            engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 5})
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
            except Exception:
                raise SystemExit(
                    f"Cannot connect using {name}. Check that PostgreSQL is running and the "
                    "test database exists, with a login that can create its schema and tables."
                ) from None
            finally:
                engine.dispose()
            checked.add(url)
        print(f"{name}: test database reachable")


if __name__ == "__main__":
    main()
