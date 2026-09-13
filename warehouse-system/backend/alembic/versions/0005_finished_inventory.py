"""复用成品库存，支持每成品汇总和单据业务日期/类型/外部单号。"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('stock_documents', sa.Column('business_date', sa.Date(), nullable=True))
    op.add_column('stock_documents', sa.Column('business_type', sa.String(20), nullable=True))
    op.add_column('stock_documents', sa.Column('reference_no', sa.String(120), nullable=True))
    op.create_check_constraint('ck_stock_documents_valid_business_type', 'stock_documents',
                               "business_type IS NULL OR business_type IN ('normal','production')")
    op.alter_column('finished_batches', 'location_id', existing_type=sa.Integer(), nullable=True)
    op.add_column('finished_batches', sa.Column('stock_scope', sa.String(16), nullable=True))
    op.create_unique_constraint('uq_finished_batches_product_scope', 'finished_batches', ['product_id', 'stock_scope'])
    op.create_check_constraint('ck_finished_batches_valid_scope', 'finished_batches',
                               "(stock_scope IS NULL AND location_id IS NOT NULL) OR "
                               "(stock_scope IS NOT NULL AND stock_scope = 'aggregate' AND location_id IS NULL AND batch_no = '__aggregate__' "
                               "AND production_date IS NULL AND expiry_date IS NULL)")


def downgrade():
    # 不为降级丢弃成品余额/历史。需要人工导出和迁移已有汇总记录后才能降级。
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM finished_batches WHERE location_id IS NULL")).scalar():
        raise RuntimeError('存在汇总成品库存记录，禁止直接降级；请先制定数据迁移方案')
    op.drop_constraint('ck_finished_batches_valid_scope', 'finished_batches', type_='check')
    op.drop_constraint('uq_finished_batches_product_scope', 'finished_batches', type_='unique')
    op.drop_column('finished_batches', 'stock_scope')
    op.alter_column('finished_batches', 'location_id', existing_type=sa.Integer(), nullable=False)
    op.drop_constraint('ck_stock_documents_valid_business_type', 'stock_documents', type_='check')
    for name in ('reference_no', 'business_type', 'business_date'):
        op.drop_column('stock_documents', name)
