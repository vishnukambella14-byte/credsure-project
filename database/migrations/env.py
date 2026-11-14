"""Alembic environment for CredSure.

The database URL comes from ``app.core.config`` (``CREDSURE_DATABASE_URL`` / backend/.env), and
online migrations reuse the application engine, so the SQLite connection configuration
(``foreign_keys``, ``recursive_triggers``, explicit ``BEGIN``; ADR-019) applies during migrations.

Programmatic use (tests): pass an open connection in ``config.attributes["connection"]`` to
migrate a specific database, and set ``config.attributes["configure_logger"] = False`` so the
ini logging configuration does not replace the caller's logging.

Run Alembic from the ``backend`` directory, e.g. ``alembic upgrade head``.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

# Make the backend package importable regardless of the current working directory.
BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.database import triggers  # noqa: E402
from app.database.session import engine  # noqa: E402
from app.database.types import UTCDateTime  # noqa: E402
from app.models import Base  # noqa: E402  (imports all models into Base.metadata)

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# Indexes created by raw SQL in migrations (data_dictionary §6), invisible to the models.
_MANAGED_INDEXES = frozenset(triggers.CURRENT_INDEXES)


def render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Render app column types without importing application code into migration files."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime()"
    return False


def include_object(obj: object, name: str | None, type_: str, reflected: bool, compare_to: object) -> bool:
    """Keep autogenerate from proposing to drop the raw-SQL indexes (they are not in the models)."""
    if type_ == "index" and name in _MANAGED_INDEXES:
        return False
    return True


def _configure(**kwargs: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE operations
        render_item=render_item,
        include_object=include_object,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection."""
    _configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_online(connection: object) -> None:
    _configure(connection=connection, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a caller-supplied connection or the configured database."""
    supplied = config.attributes.get("connection")
    if supplied is not None:
        _run_online(supplied)
        return
    with engine.connect() as connection:
        _run_online(connection)
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
