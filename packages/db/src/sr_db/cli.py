"""`sr-db` command line: apply migrations and show their status."""

import argparse
import sys

import psycopg

from sr_db.migrate import MIGRATIONS_DIR, MigrationError, applied_migrations, discover, migrate
from sr_db.settings import DatabaseSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sr-db", description="SR-Agent database tools.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply pending migrations")
    commands.add_parser("status", help="list migrations and whether each is applied")
    args = parser.parse_args(argv)

    url = DatabaseSettings().url
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            if args.command == "migrate":
                applied = migrate(conn, MIGRATIONS_DIR)
                for migration in applied:
                    print(f"applied {migration.name}")
                if not applied:
                    print("nothing to apply")
            else:
                done = applied_migrations(conn)
                for migration in discover(MIGRATIONS_DIR):
                    mark = "x" if migration.name in done else " "
                    print(f"[{mark}] {migration.name}")
    except MigrationError as error:
        print(f"sr-db: {error}", file=sys.stderr)
        return 1
    return 0
