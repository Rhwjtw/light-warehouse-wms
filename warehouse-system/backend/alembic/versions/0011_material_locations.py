"""Stage raw receipts and track location transfers and counts.

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

KINDS = "'material_receipt','material_outbound','issue','supplement','return','finished_receipt','finished_outbound','putaway','transfer','count_gain','count_loss','count_match','adjustment'"
OLD_KINDS = "'material_receipt','material_outbound','issue','supplement','return','finished_receipt','finished_outbound'"


def upgrade():
    def inspector():
        return sa.inspect(op.get_bind())

    def column(table, name):
        return next((item for item in inspector().get_columns(table) if item['name'] == name), None)

    def check(table, name):
        return any(item['name'] == name for item in inspector().get_check_constraints(table))

    def foreign_key(table, name):
        return any(item['name'] == name for item in inspector().get_foreign_keys(table))

    if not column('material_batches', 'location_id')['nullable']:
        op.alter_column('material_batches', 'location_id', existing_type=sa.Integer(), nullable=True)
    if not column('material_batches', 'stock_status'):
        op.add_column('material_batches', sa.Column('stock_status', sa.String(16), nullable=False, server_default='shelved'))
    if not check('material_batches', 'ck_material_batches_valid_stock_status'):
        op.create_check_constraint('valid_stock_status', 'material_batches',
                                   "(stock_status = 'pending' AND location_id IS NULL) OR (stock_status = 'shelved' AND location_id IS NOT NULL)")
    if not column('material_movements', 'shelved_quantity'):
        op.add_column('material_movements', sa.Column('shelved_quantity', sa.Numeric(20, 6), nullable=True))
    # Existing receipts were posted directly to their selected real location.
    op.execute("UPDATE material_movements m JOIN stock_documents d ON d.id=m.document_id "
               "SET m.shelved_quantity=m.quantity WHERE d.kind='material_receipt'")
    if not check('material_movements', 'ck_material_movements_valid_shelved_quantity'):
        op.create_check_constraint('valid_shelved_quantity', 'material_movements',
                                   'shelved_quantity IS NULL OR (shelved_quantity >= 0 AND shelved_quantity <= quantity)')
    for table in ('stock_documents', 'inventory_transactions'):
        name = f'ck_{table}_valid_kind'
        existing = next((item for item in inspector().get_check_constraints(table) if item['name'] == name), None)
        if existing and 'adjustment' not in existing['sqltext']:
            op.drop_constraint(op.f(name), table, type_='check')
        if not check(table, name):
            op.create_check_constraint('valid_kind', table, f'kind IN ({KINDS})')
    if not column('inventory_transactions', 'source_location_id'):
        op.add_column('inventory_transactions', sa.Column('source_location_id', sa.Integer(), nullable=True))
    if not column('inventory_transactions', 'target_location_id'):
        op.add_column('inventory_transactions', sa.Column('target_location_id', sa.Integer(), nullable=True))
    if not foreign_key('inventory_transactions', 'fk_inventory_transactions_source_location_id_locations'):
        op.create_foreign_key(op.f('fk_inventory_transactions_source_location_id_locations'), 'inventory_transactions', 'locations', ['source_location_id'], ['id'])
    if not foreign_key('inventory_transactions', 'fk_inventory_transactions_target_location_id_locations'):
        op.create_foreign_key(op.f('fk_inventory_transactions_target_location_id_locations'), 'inventory_transactions', 'locations', ['target_location_id'], ['id'])
    op.execute("UPDATE inventory_transactions t JOIN material_batches b ON b.id=t.material_batch_id "
               "SET t.source_location_id=CASE WHEN t.change_quantity<0 THEN b.location_id ELSE NULL END, "
               "t.target_location_id=CASE WHEN t.change_quantity>0 THEN b.location_id ELSE NULL END")
    if 'material_transfers' not in inspector().get_table_names():
        op.create_table('material_transfers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('document_id', sa.Integer(), sa.ForeignKey('stock_documents.id'), nullable=False, unique=True),
        sa.Column('source_batch_id', sa.Integer(), sa.ForeignKey('material_batches.id'), nullable=False),
        sa.Column('target_batch_id', sa.Integer(), sa.ForeignKey('material_batches.id'), nullable=False),
        sa.Column('receipt_movement_id', sa.Integer(), sa.ForeignKey('material_movements.id'), nullable=True),
        sa.Column('quantity', sa.Numeric(20, 6), nullable=False),
        sa.CheckConstraint('quantity > 0', name=op.f('ck_material_transfers_positive_quantity')))
    if 'material_counts' not in inspector().get_table_names():
        op.create_table('material_counts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('document_id', sa.Integer(), sa.ForeignKey('stock_documents.id'), nullable=False, unique=True),
        sa.Column('batch_id', sa.Integer(), sa.ForeignKey('material_batches.id'), nullable=False),
        sa.Column('book_quantity', sa.Numeric(20, 6), nullable=False),
        sa.Column('actual_quantity', sa.Numeric(20, 6), nullable=False),
        sa.Column('difference', sa.Numeric(20, 6), nullable=False),
        sa.Column('counted_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('counted_at', sa.DateTime(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.CheckConstraint('book_quantity >= 0 AND actual_quantity >= 0 AND difference = actual_quantity - book_quantity', name=op.f('ck_material_counts_valid_count')))
    if 'material_adjustments' not in inspector().get_table_names():
        op.create_table('material_adjustments',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('document_id', sa.Integer(), sa.ForeignKey('stock_documents.id'), nullable=False, unique=True),
            sa.Column('batch_id', sa.Integer(), sa.ForeignKey('material_batches.id'), nullable=False),
            sa.Column('before_quantity', sa.Numeric(20, 6), nullable=False),
            sa.Column('change_quantity', sa.Numeric(20, 6), nullable=False),
            sa.Column('after_quantity', sa.Numeric(20, 6), nullable=False),
            sa.Column('operator_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('reason', sa.Text(), nullable=False),
            sa.CheckConstraint('before_quantity >= 0 AND after_quantity >= 0 AND change_quantity <> 0 AND after_quantity = before_quantity + change_quantity', name=op.f('ck_material_adjustments_valid_adjustment')))


def downgrade():
    connection = op.get_bind()
    new_documents = connection.execute(sa.text("SELECT COUNT(*) FROM stock_documents WHERE kind IN ('putaway','transfer','count_gain','count_loss','count_match','adjustment')")).scalar_one()
    pending = connection.execute(sa.text('SELECT COUNT(*) FROM material_batches WHERE location_id IS NULL')).scalar_one()
    if new_documents or pending:
        raise RuntimeError('Location business data exists; downgrade would discard inventory history')
    op.drop_table('material_adjustments')
    op.drop_table('material_counts')
    op.drop_table('material_transfers')
    op.drop_constraint(op.f('fk_inventory_transactions_target_location_id_locations'), 'inventory_transactions', type_='foreignkey')
    op.drop_constraint(op.f('fk_inventory_transactions_source_location_id_locations'), 'inventory_transactions', type_='foreignkey')
    op.drop_column('inventory_transactions', 'target_location_id')
    op.drop_column('inventory_transactions', 'source_location_id')
    op.drop_constraint(op.f('ck_inventory_transactions_valid_kind'), 'inventory_transactions', type_='check')
    op.create_check_constraint('valid_kind', 'inventory_transactions', f'kind IN ({OLD_KINDS})')
    op.drop_constraint(op.f('ck_stock_documents_valid_kind'), 'stock_documents', type_='check')
    op.create_check_constraint('valid_kind', 'stock_documents', f'kind IN ({OLD_KINDS})')
    op.drop_constraint(op.f('ck_material_movements_valid_shelved_quantity'), 'material_movements', type_='check')
    op.drop_column('material_movements', 'shelved_quantity')
    op.drop_constraint(op.f('ck_material_batches_valid_stock_status'), 'material_batches', type_='check')
    op.drop_column('material_batches', 'stock_status')
    op.alter_column('material_batches', 'location_id', existing_type=sa.Integer(), nullable=False)
