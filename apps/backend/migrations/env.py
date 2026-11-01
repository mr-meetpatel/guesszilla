from alembic import context
from sqlalchemy import text

from wordle_bot.repositories.models import SCHEMA, Base

connection = context.config.attributes["connection"]
connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
connection.commit()
context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    version_table_schema=SCHEMA,
    include_schemas=True,
)
with context.begin_transaction():
    context.run_migrations()
