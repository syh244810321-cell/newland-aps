import os

from alembic import context
from sqlalchemy import create_engine

from aps.product.tables import metadata


url = os.environ.get("APS_PRODUCT_DATABASE")
if not url:
    raise RuntimeError("Set APS_PRODUCT_DATABASE before running database migrations.")

if context.is_offline_mode():
    context.configure(url=url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url).connect() as conn:
        context.configure(connection=conn, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
