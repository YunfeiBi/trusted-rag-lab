from alembic import context
from sqlalchemy import create_engine

from backend.app.config import Settings
from backend.app.models import Base

config = context.config


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=Settings.load().database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    existing = config.attributes.get('connection')
    if existing is not None:
        run(existing)
    else:
        engine = create_engine(Settings.load().database_url, hide_parameters=True)
        with engine.connect() as connection:
            run(connection)
        engine.dispose()
