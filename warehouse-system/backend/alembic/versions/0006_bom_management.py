"""complete BOM management structure

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(op.f('ck_bom_headers_valid_status'), 'bom_headers', type_='check')
    op.create_check_constraint(
        op.f('ck_bom_headers_valid_status'), 'bom_headers',
        "status IN ('draft','active','inactive','archived')",
    )
    op.add_column('bom_headers', sa.Column(
        'active_product_id', sa.Integer(),
        sa.Computed("CASE WHEN status = 'active' THEN product_id ELSE NULL END", persisted=True),
        nullable=True,
    ))
    op.create_unique_constraint(op.f('uq_bom_headers_active_product'), 'bom_headers', ['active_product_id'])

    op.add_column('bom_items', sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('bom_items', sa.Column('notes', sa.Text(), nullable=True))
    op.add_column('bom_items', sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))
    op.add_column('bom_items', sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))


def downgrade():
    op.drop_column('bom_items', 'updated_at')
    op.drop_column('bom_items', 'created_at')
    op.drop_column('bom_items', 'notes')
    op.drop_column('bom_items', 'sort_order')
    op.drop_constraint(op.f('uq_bom_headers_active_product'), 'bom_headers', type_='unique')
    op.drop_column('bom_headers', 'active_product_id')
    op.drop_constraint(op.f('ck_bom_headers_valid_status'), 'bom_headers', type_='check')
    op.create_check_constraint(
        op.f('ck_bom_headers_valid_status'), 'bom_headers',
        "status IN ('draft','active','archived')",
    )
