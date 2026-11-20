"""Store confirmed execution for versioned shop-floor batches."""

from alembic import op

from aps.product.tables import executions


revision = "0002_execution"
down_revision = "0001_workbench"
branch_labels = None
depends_on = None


def upgrade():
    executions.create(op.get_bind())


def downgrade():
    executions.drop(op.get_bind())
