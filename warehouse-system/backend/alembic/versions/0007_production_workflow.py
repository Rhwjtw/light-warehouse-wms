"""production orders, BOM snapshots and material posting summaries

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('production_orders', sa.Column('product_name_snapshot', sa.String(length=120), nullable=True))
    op.add_column('production_orders', sa.Column('bom_version', sa.Integer(), nullable=True))
    op.add_column('production_orders', sa.Column('planned_date', sa.Date(), nullable=True))
    op.execute(sa.text(
        "UPDATE production_orders po "
        "JOIN finished_products fp ON fp.id = po.product_id "
        "JOIN bom_headers bh ON bh.id = po.bom_id "
        "SET po.product_name_snapshot = fp.name, po.bom_version = bh.version"
    ))
    op.alter_column('production_orders', 'product_name_snapshot', existing_type=sa.String(length=120), nullable=False)
    op.alter_column('production_orders', 'bom_version', existing_type=sa.Integer(), nullable=False)
    op.execute(sa.text("UPDATE production_orders SET status = 'ready' WHERE status = 'released'"))
    op.drop_constraint(op.f('ck_production_orders_valid_status'), 'production_orders', type_='check')
    op.create_check_constraint(
        op.f('ck_production_orders_valid_status'), 'production_orders',
        "status IN ('draft','ready','in_progress','completed','received','cancelled')",
    )

    op.add_column('production_order_materials', sa.Column('material_code_snapshot', sa.String(length=40), nullable=True))
    op.add_column('production_order_materials', sa.Column('material_name_snapshot', sa.String(length=120), nullable=True))
    op.add_column('production_order_materials', sa.Column('base_unit', sa.String(length=16), nullable=True))
    op.add_column('production_order_materials', sa.Column('issued_quantity', sa.Numeric(20, 6), nullable=False, server_default='0'))
    op.add_column('production_order_materials', sa.Column('supplemented_quantity', sa.Numeric(20, 6), nullable=False, server_default='0'))
    op.add_column('production_order_materials', sa.Column('returned_quantity', sa.Numeric(20, 6), nullable=False, server_default='0'))
    op.add_column('production_order_materials', sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))
    op.add_column('production_order_materials', sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')))
    op.execute(sa.text(
        "UPDATE production_order_materials pom "
        "JOIN raw_materials rm ON rm.id = pom.material_id "
        "SET pom.material_code_snapshot = rm.code, pom.material_name_snapshot = rm.name, pom.base_unit = rm.unit"
    ))
    op.alter_column('production_order_materials', 'material_code_snapshot', existing_type=sa.String(length=40), nullable=False)
    op.alter_column('production_order_materials', 'material_name_snapshot', existing_type=sa.String(length=120), nullable=False)
    op.alter_column('production_order_materials', 'base_unit', existing_type=sa.String(length=16), nullable=False)
    op.create_foreign_key(op.f('fk_production_order_materials_base_unit_units'), 'production_order_materials', 'units', ['base_unit'], ['code'])
    op.drop_constraint(op.f('ck_production_order_materials_valid_quantities'), 'production_order_materials', type_='check')
    op.create_check_constraint(
        op.f('ck_production_order_materials_valid_quantities'), 'production_order_materials',
        'quantity_per_unit > 0 AND planned_theoretical_quantity > 0 '
        'AND issued_quantity >= 0 AND supplemented_quantity >= 0 AND returned_quantity >= 0 '
        'AND returned_quantity <= issued_quantity + supplemented_quantity '
        'AND (final_theoretical_quantity IS NULL OR final_theoretical_quantity >= 0)',
    )


def downgrade():
    op.drop_constraint(op.f('ck_production_order_materials_valid_quantities'), 'production_order_materials', type_='check')
    op.create_check_constraint(
        op.f('ck_production_order_materials_valid_quantities'), 'production_order_materials',
        'quantity_per_unit > 0 AND planned_theoretical_quantity > 0 '
        'AND (final_theoretical_quantity IS NULL OR final_theoretical_quantity >= 0)',
    )
    op.drop_constraint(op.f('fk_production_order_materials_base_unit_units'), 'production_order_materials', type_='foreignkey')
    for column in ('updated_at', 'created_at', 'returned_quantity', 'supplemented_quantity',
                   'issued_quantity', 'base_unit', 'material_name_snapshot', 'material_code_snapshot'):
        op.drop_column('production_order_materials', column)

    op.execute(sa.text("UPDATE production_orders SET status = 'released' WHERE status = 'ready'"))
    op.drop_constraint(op.f('ck_production_orders_valid_status'), 'production_orders', type_='check')
    op.create_check_constraint(
        op.f('ck_production_orders_valid_status'), 'production_orders',
        "status IN ('draft','released','in_progress','completed','received','cancelled')",
    )
    op.drop_column('production_orders', 'planned_date')
    op.drop_column('production_orders', 'bom_version')
    op.drop_column('production_orders', 'product_name_snapshot')
