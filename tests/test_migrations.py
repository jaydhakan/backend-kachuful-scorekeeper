from alembic import command

from tests.conftest import alembic_config


def test_every_migration_downgrades_and_upgrades_cleanly() -> None:
    config = alembic_config()

    command.downgrade(config, "base")
    command.upgrade(config, "head")
