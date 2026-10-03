"""`sr-db` command line: apply migrations and show their status."""

import argparse
import sys
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import sql

from sr_db.migrate import MIGRATIONS_DIR, MigrationError, applied_migrations, discover, migrate
from sr_db.settings import DatabaseSettings, ReaderSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sr-db", description="SR-Agent database tools.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply pending migrations")
    commands.add_parser("status", help="list migrations and whether each is applied")
    commands.add_parser(
        "create-reader",
        help="create or update the MCP server's login role (from MCP_DATABASE_URL) as a member "
        "of the read-only sr_reader role",
    )
    args = parser.parse_args(argv)

    url = DatabaseSettings().url
    if args.command == "create-reader":
        return _create_reader(url)
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


def _create_reader(admin_url: str) -> int:
    reader = urlsplit(ReaderSettings().url)
    user, password = unquote(reader.username or ""), unquote(reader.password or "")
    if not user or not password:
        print("sr-db: MCP_DATABASE_URL must include a user name and password", file=sys.stderr)
        return 1
    with psycopg.connect(admin_url, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'sr_reader'").fetchone() is None:
            print(
                "sr-db: role sr_reader does not exist; run `sr-db migrate` first", file=sys.stderr
            )
            return 1
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL("{} ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                sql.SQL(verb), sql.Identifier(user), sql.Literal(password)
            )
        )
        conn.execute(sql.SQL("GRANT sr_reader TO {}").format(sql.Identifier(user)))
    print(f"{'updated' if exists else 'created'} login role {user} (member of sr_reader)")
    return 0
