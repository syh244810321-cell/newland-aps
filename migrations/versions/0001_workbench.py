"""Initial enterprise workbench schema."""

from alembic import op

from aps.product.tables import audit, datasets, jobs, metadata, plans, plants, sessions, users


revision = "0001_workbench"
down_revision = None
branch_labels = None
depends_on = None
INITIAL_TABLES = (plants, users, sessions, datasets, jobs, plans, audit)


def upgrade():
    metadata.create_all(op.get_bind(), tables=INITIAL_TABLES, checkfirst=False)


def downgrade():
    metadata.drop_all(op.get_bind(), tables=INITIAL_TABLES, checkfirst=False)
