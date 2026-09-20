"""Preserve audit actor names and searchable business descriptions."""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('operation_logs', sa.Column('username_snapshot', sa.String(64), nullable=True))
    op.add_column('operation_logs', sa.Column('description', sa.String(500), nullable=True))
    op.execute("UPDATE operation_logs l LEFT JOIN users u ON u.id=l.user_id "
               "SET l.username_snapshot=u.username, l.description=CONCAT(l.action, ' · ', l.entity_type, ' #', COALESCE(l.entity_id,''))")


def downgrade():
    op.drop_column('operation_logs', 'description')
    op.drop_column('operation_logs', 'username_snapshot')
