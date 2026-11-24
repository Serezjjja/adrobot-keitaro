import logging

from alembic import context
from sqlalchemy import create_engine, pool

from adrobot.config import Settings
from adrobot.db import Base

logging.basicConfig(level=logging.INFO)
database_url = Settings().database_url


def run_migrations() -> None:
    if context.is_offline_mode():
        context.configure(url=database_url, target_metadata=Base.metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
