"""`sr-db` command line: apply migrations, show their status, and create login roles."""

import argparse
import sys
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import sql

from sr_db.migrate import MIGRATIONS_DIR, MigrationError, applied_migrations, discover, migrate
from sr_db.settings import AuditorSettings, DatabaseSettings, ReaderSettings


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
    commands.add_parser(
        "create-auditor",
        help="create or update the agent's audit login (from SRA_AUDIT_DATABASE_URL) as a member "
        "of the sr_auditor role",
    )
    args = parser.parse_args(argv)

    url = DatabaseSettings().url
    if args.command == "create-reader":
        return _create_login(url, ReaderSettings().url, "sr_reader", "MCP_DATABASE_URL")
    if args.command == "create-auditor":
        return _create_login(url, AuditorSettings().url, "sr_auditor", "SRA_AUDIT_DATABASE_URL")
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


def _create_login(admin_url: str, login_url: str, group: str, variable: str) -> int:
    """Create or update the login in `login_url` as a member of the group role `group`."""
    login = urlsplit(login_url)
    user, password = unquote(login.username or ""), unquote(login.password or "")
    if not user or not password:
        print(f"sr-db: {variable} must include a user name and password", file=sys.stderr)
        return 1
    with psycopg.connect(admin_url, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (group,)).fetchone() is None:
            print(f"sr-db: role {group} does not exist; run `sr-db migrate` first", file=sys.stderr)
            return 1
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL("{} ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                sql.SQL(verb), sql.Identifier(user), sql.Literal(password)
            )
        )
        conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(user)))
    print(f"{'updated' if exists else 'created'} login role {user} (member of {group})")
    return 0
