"""Add append-only links for the first stock document reversals.

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None

KINDS = "'material_receipt','material_outbound','issue','supplement','return','finished_receipt','finished_outbound','putaway','transfer','count_gain','count_loss','count_match','adjustment','reversal'"
OLD_KINDS = KINDS.removesuffix(",'reversal'")


def upgrade():
    def columns(table):
        return {row['name'] for row in sa.inspect(op.get_bind()).get_columns(table)}

    def uniques(table):
        return {row['name'] for row in sa.inspect(op.get_bind()).get_unique_constraints(table)}

    def foreign_keys(table):
        return {row['name'] for row in sa.inspect(op.get_bind()).get_foreign_keys(table)}

    def checks(table):
        return {row['name']: row['sqltext'] for row in sa.inspect(op.get_bind()).get_check_constraints(table)}

    for name, type_ in [('reverses_document_id', sa.Integer()), ('reverses_kind', sa.String(32)),
                        ('original_document_no_snapshot', sa.String(48)), ('reversal_reason', sa.Text())]:
        if name not in columns('stock_documents'):
            op.add_column('stock_documents', sa.Column(name, type_, nullable=True))
    if 'uq_stock_documents_id_kind' not in uniques('stock_documents'):
        op.create_unique_constraint('uq_stock_documents_id_kind', 'stock_documents', ['id', 'kind'])
    if 'uq_stock_documents_reverses_document_id' not in uniques('stock_documents'):
        op.create_unique_constraint('uq_stock_documents_reverses_document_id', 'stock_documents', ['reverses_document_id'])
    if 'fk_stock_documents_original_kind' not in foreign_keys('stock_documents'):
        op.create_foreign_key('fk_stock_documents_original_kind', 'stock_documents', 'stock_documents',
                              ['reverses_document_id', 'reverses_kind'], ['id', 'kind'])
    kind_check = checks('stock_documents').get('ck_stock_documents_valid_kind')
    if kind_check and 'reversal' not in kind_check:
        op.drop_constraint(op.f('ck_stock_documents_valid_kind'), 'stock_documents', type_='check')
    if 'ck_stock_documents_valid_kind' not in checks('stock_documents'):
        op.create_check_constraint('valid_kind', 'stock_documents', f'kind IN ({KINDS})')
    if 'ck_stock_documents_valid_reversal' not in checks('stock_documents'):
        op.create_check_constraint('valid_reversal', 'stock_documents',
        "(kind <> 'reversal' AND reverses_document_id IS NULL AND reverses_kind IS NULL "
        "AND original_document_no_snapshot IS NULL AND reversal_reason IS NULL) OR "
        "(kind = 'reversal' AND reverses_document_id IS NOT NULL AND reverses_kind IS NOT NULL "
        "AND reverses_kind IN ('material_outbound','finished_outbound') "
        "AND original_document_no_snapshot IS NOT NULL "
        "AND reversal_reason IS NOT NULL AND CHAR_LENGTH(TRIM(reversal_reason)) > 0)")

    if 'reverses_transaction_id' not in columns('inventory_transactions'):
        op.add_column('inventory_transactions', sa.Column('reverses_transaction_id', sa.Integer(), nullable=True))
    if 'uq_inventory_transactions_reverses_transaction_id' not in uniques('inventory_transactions'):
        op.create_unique_constraint('uq_inventory_transactions_reverses_transaction_id',
                                    'inventory_transactions', ['reverses_transaction_id'])
    if 'fk_inventory_transactions_reverses_transaction_id' not in foreign_keys('inventory_transactions'):
        op.create_foreign_key('fk_inventory_transactions_reverses_transaction_id',
                              'inventory_transactions', 'inventory_transactions',
                              ['reverses_transaction_id'], ['id'])
    kind_check = checks('inventory_transactions').get('ck_inventory_transactions_valid_kind')
    if kind_check and 'reversal' not in kind_check:
        op.drop_constraint(op.f('ck_inventory_transactions_valid_kind'), 'inventory_transactions', type_='check')
    if 'ck_inventory_transactions_valid_kind' not in checks('inventory_transactions'):
        op.create_check_constraint('valid_kind', 'inventory_transactions', f'kind IN ({KINDS})')
    if 'ck_inventory_transactions_valid_reversal' not in checks('inventory_transactions'):
        op.create_check_constraint('valid_reversal', 'inventory_transactions',
            "(kind = 'reversal' AND reverses_transaction_id IS NOT NULL) "
            "OR (kind <> 'reversal' AND reverses_transaction_id IS NULL)")


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM stock_documents WHERE kind='reversal'")).scalar_one():
        raise RuntimeError('Reversal history exists; downgrade would discard immutable links')
    op.drop_constraint(op.f('ck_inventory_transactions_valid_reversal'), 'inventory_transactions', type_='check')
    op.drop_constraint(op.f('ck_inventory_transactions_valid_kind'), 'inventory_transactions', type_='check')
    op.create_check_constraint('valid_kind', 'inventory_transactions', f'kind IN ({OLD_KINDS})')
    op.drop_constraint('fk_inventory_transactions_reverses_transaction_id', 'inventory_transactions', type_='foreignkey')
    op.drop_constraint('uq_inventory_transactions_reverses_transaction_id', 'inventory_transactions', type_='unique')
    op.drop_column('inventory_transactions', 'reverses_transaction_id')
    op.drop_constraint(op.f('ck_stock_documents_valid_reversal'), 'stock_documents', type_='check')
    op.drop_constraint(op.f('ck_stock_documents_valid_kind'), 'stock_documents', type_='check')
    op.create_check_constraint('valid_kind', 'stock_documents', f'kind IN ({OLD_KINDS})')
    op.drop_constraint('fk_stock_documents_original_kind', 'stock_documents', type_='foreignkey')
    op.drop_constraint('uq_stock_documents_reverses_document_id', 'stock_documents', type_='unique')
    op.drop_constraint('uq_stock_documents_id_kind', 'stock_documents', type_='unique')
    for column in ('reversal_reason', 'original_document_no_snapshot', 'reverses_kind', 'reverses_document_id'):
        op.drop_column('stock_documents', column)
