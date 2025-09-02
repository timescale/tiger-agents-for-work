import asyncio
import os
import re
from pathlib import Path

import logfire
from semver import Version
from psycopg import AsyncConnection, AsyncCursor

from tiger_agent import __version__

SHARED_LOCK_KEY = 31321898691465844
MAX_LOCK_ATTEMPTS = 10
LOCK_SLEEP_SECONDS = 10


@logfire.instrument("try_migration_lock", extract_args=False)
async def try_migration_lock(cur: AsyncCursor) -> bool:
    """Attempt to acquire transaction-level advisory lock for migration"""
    await cur.execute(
        "select pg_try_advisory_xact_lock(%s::bigint)",
        (SHARED_LOCK_KEY,)
    )
    row = await cur.fetchone()
    if not row:
        raise Exception("attempting to get an advisory lock for migration failed to return a row")
    return bool(row[0])


@logfire.instrument("run_init", extract_args=False)
async def run_init(cur: AsyncCursor) -> None:
    """Initialize migration infrastructure tables"""
    sql = Path(__file__).parent.joinpath("sql", "init.sql").read_text()
    await cur.execute(sql)


async def get_db_version(cur: AsyncCursor) -> Version:
    """Get current database version"""
    await cur.execute("select version from agent.version")
    row = await cur.fetchone()
    assert row is not None
    ver = Version.parse(str(row[0]))
    return ver


@logfire.instrument("is_migration_required", extract_args=["target_version"])
async def is_migration_required(cur: AsyncCursor, target_version: Version) -> bool:
    """Check if migration is required"""
    db_version = await get_db_version(cur)
    if target_version < db_version:
        logfire.error(f"target version ({target_version}) is older than the database ({db_version})! aborting")
        raise ValueError(f"Cannot downgrade from version {db_version} to {target_version}")
    return target_version > db_version


def sql_file_number(path: Path) -> int:
    """Extract number from SQL filename"""
    pattern = r"^(\d{3})-[a-z][a-z-]*\.sql$"
    match = re.match(pattern, path.name)
    if not match:
        logfire.error(f"{path} file name does not match the pattern {pattern}")
        raise ValueError(f"Invalid filename pattern: {path.name}")
    return int(match.group(1))


def check_sql_file_order(paths: list[Path]) -> None:
    """Verify SQL files are in sequential order"""
    prev = -1
    for path in paths:
        this = sql_file_number(path)
        if this == 999:
            break
        if this != prev + 1:
            logfire.error(f"sql files must be strictly ordered: {path.name}")
            raise ValueError(f"SQL files not in sequential order at {path.name}")
        prev = this


async def run_incremental(cur: AsyncCursor, target_version: Version) -> None:
    """Run incremental migrations"""
    migration_template = Path(__file__).parent.joinpath("sql", "migration.sql").read_text()
    incremental = Path(__file__).parent.joinpath("incremental")
    paths = [path for path in incremental.glob("*.sql")]
    paths.sort()
    check_sql_file_order(paths)

    for path in paths:
        with logfire.span(f"incremental_sql", script=path.name):
            sql = migration_template.format(
                migration_name=path.name,
                migration_body=path.read_text(),
                version=str(target_version),
            )
            await cur.execute(sql)


async def run_idempotent(cur: AsyncCursor) -> None:
    """Run idempotent SQL that can run multiple times"""
    idempotent = Path(__file__).parent.joinpath("idempotent")
    paths = [path for path in idempotent.glob("*.sql")]
    paths.sort()
    check_sql_file_order(paths)

    for path in paths:
        with logfire.span("idempotent_sql", script=path.name):
            sql = path.read_text()
            await cur.execute(sql)


@logfire.instrument("set_version", extract_args=["version"])
async def set_version(cur: AsyncCursor, version: Version) -> None:
    """Update database version"""
    await cur.execute("update agent.version set version = %s, at = clock_timestamp()", (str(version),))


@logfire.instrument("migrate_db", extract_args=False)
async def migrate_db(con: AsyncConnection) -> None:
    """Run database migrations"""
    target_version = Version.parse(__version__)
    async with (
        con.cursor() as cur,
        con.transaction() as _,
    ):
        # Try to acquire migration lock
        for i in range(1, MAX_LOCK_ATTEMPTS + 1):
            locked = await try_migration_lock(cur)
            if locked:
                break
            if i == MAX_LOCK_ATTEMPTS:
                logfire.error(f"failed to get an advisory lock to check database version after {i} attempts")
                raise RuntimeError("Could not acquire migration lock")
            logfire.info(f"sleeping {LOCK_SLEEP_SECONDS} seconds before another lock attempt")
            await asyncio.sleep(LOCK_SLEEP_SECONDS)

        # Initialize migration infrastructure
        await run_init(cur)

        # Check if migration is required
        if not await is_migration_required(cur, target_version):
            logfire.info("no migration required. app and db are compatible.")
            return

        logfire.info(f"database migration to version {target_version} required...")

        # Run migrations
        await run_incremental(cur, target_version)
        await run_idempotent(cur)
        await set_version(cur, target_version)

    logfire.info(f"database migration to version {target_version} complete")


async def main():
    """Run database migrations CLI"""
    from dotenv import load_dotenv, find_dotenv

    # Load environment variables
    load_dotenv(dotenv_path=find_dotenv(usecwd=True))

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required")

    logfire.info("Starting database migration...")

    async with AsyncConnection.connect(database_url) as con:
        await migrate_db(con)

    logfire.info("Database migration completed successfully")


if __name__ == "__main__":
    asyncio.run(main())
