"""库存请求指纹，检测重复请求的内容冲突。"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('stock_documents', sa.Column('request_hash', sa.String(64), nullable=True))


def downgrade():
    op.drop_column('stock_documents', 'request_hash')
