"""Persist final production settlement on existing material snapshots."""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('actual_used_quantity', 'loss_quantity'):
        op.add_column('production_order_materials', sa.Column(name, sa.Numeric(20, 6), nullable=True))
    op.add_column('production_order_materials', sa.Column('loss_rate', sa.Numeric(38, 12), nullable=True))


def downgrade():
    for name in ('loss_rate', 'loss_quantity', 'actual_used_quantity'):
        op.drop_column('production_order_materials', name)
