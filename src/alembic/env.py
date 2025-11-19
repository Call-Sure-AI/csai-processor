from logging.config import fileConfig
import sys
import os
from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context
from database.config import Base
from database import models as app_models

current_path = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.abspath(os.path.join(current_path, ".."))
sys.path.insert(0, src_path)

from dotenv import load_dotenv
root_path = os.path.abspath(os.path.join(src_path, ".."))
env_path = os.path.join(root_path, ".env")
load_dotenv(env_path)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

ALEMBIC_KWARGS = dict(
    target_metadata=target_metadata,
    compare_type=True,
    compare_server_default=True,
)

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **ALEMBIC_KWARGS,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,   # QueuePool also ok; NullPool is fine for migrations
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, **ALEMBIC_KWARGS)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
