import anyio
from alembic import command
from sqlalchemy import text

from app.db.session import build_engine
from tests.conftest import alembic_config

ISHAARA_TABLES = {
    "ishaara_keys",
    "ishaara_roles",
    "ishaara_actions",
    "ishaara_game_players",
    "ishaara_player_day",
}


async def _read_tables() -> set[str]:
    engine = build_engine(statement_timeout_ms=10000)
    async with engine.connect() as conn:
        names = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        found = set(names.scalars())
    await engine.dispose()
    return found


def _tables() -> set[str]:
    return anyio.run(_read_tables)


def test_every_migration_downgrades_and_upgrades_cleanly() -> None:
    config = alembic_config()

    command.downgrade(config, "base")
    command.upgrade(config, "head")


def test_the_ishaara_migration_adds_and_drops_its_five_tables() -> None:
    config = alembic_config()

    command.downgrade(config, "0017")
    after_down = _tables()
    command.upgrade(config, "head")

    assert not ISHAARA_TABLES & after_down
    assert _tables() >= ISHAARA_TABLES


def test_the_seat_invites_migration_adds_and_drops_its_table() -> None:
    config = alembic_config()

    command.downgrade(config, "0018")
    after_down = _tables()
    command.upgrade(config, "head")

    assert "seat_invites" not in after_down
    assert "seat_invites" in _tables()


SPADES_TABLES = {
    "spades_seats",
    "spades_table_seats",
    "spades_hands",
    "spades_actions",
    "spades_game_players",
}


def test_the_spades_migration_adds_and_drops_its_five_tables() -> None:
    config = alembic_config()

    command.downgrade(config, "0020")
    after_down = _tables()
    command.upgrade(config, "head")

    assert not SPADES_TABLES & after_down
    assert _tables() >= SPADES_TABLES


GAME_TABLES = ("achievements", "activity", "closed_periods")


async def _read_game_columns() -> set[str]:
    engine = build_engine(statement_timeout_ms=10000)
    async with engine.connect() as conn:
        names = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'game'"
            )
        )
        found = set(names.scalars())
    await engine.dispose()
    return found


def test_the_game_migration_adds_and_drops_the_game_columns() -> None:
    config = alembic_config()

    command.downgrade(config, "0021")
    after_down = anyio.run(_read_game_columns)
    command.upgrade(config, "head")

    assert not set(GAME_TABLES) & after_down
    assert anyio.run(_read_game_columns) >= set(GAME_TABLES)
