"""Real finished batches and unfinished material reversals; no historical rewrite."""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = depends_on = None

OLD_SCOPE = ("(stock_scope IS NULL AND location_id IS NOT NULL) OR "
             "(stock_scope IS NOT NULL AND stock_scope = 'aggregate' AND location_id IS NULL "
             "AND batch_no = '__aggregate__' AND production_date IS NULL AND expiry_date IS NULL)")
NEW_SCOPE = OLD_SCOPE + (" OR (stock_scope IS NOT NULL AND stock_scope = 'batch' AND location_id IS NULL AND production_date IS NOT NULL "
                         "AND CHAR_LENGTH(TRIM(batch_no)) > 0 AND batch_no <> '__aggregate__')")
OLD_KINDS = "'material_outbound','finished_outbound','material_receipt','putaway','transfer','finished_receipt','adjustment'"


def reversal_check(kinds):
    return ("(kind <> 'reversal' AND reverses_document_id IS NULL AND reverses_kind IS NULL "
            "AND original_document_no_snapshot IS NULL AND reversal_reason IS NULL) OR "
            "(kind = 'reversal' AND reverses_document_id IS NOT NULL AND reverses_kind IS NOT NULL "
            f"AND reverses_kind IN ({kinds}) AND original_document_no_snapshot IS NOT NULL "
            "AND reversal_reason IS NOT NULL AND CHAR_LENGTH(TRIM(reversal_reason)) > 0)")


def replace(table, name, expression):
    existing = [c['name'] for c in sa.inspect(op.get_bind()).get_check_constraints(table)
                if c['name'].endswith(name)]
    if len(existing) != 1:
        raise RuntimeError(f'Expected one {name}, found {existing}')
    op.execute(sa.text(f'ALTER TABLE {table} DROP CHECK {existing[0]}, ADD CONSTRAINT {name} CHECK ({expression})'))


def upgrade():
    # MySQL foreign keys need a covering index BEFORE removing the old unique index.
    for column in ('document_id', 'production_order_id'):
        op.create_index('ix_finished_receipts_' + column, 'finished_receipts', [column])
        op.drop_constraint('uq_finished_receipts_' + column, 'finished_receipts', type_='unique')
    op.create_unique_constraint('uq_finished_receipts_order_batch', 'finished_receipts', ['production_order_id', 'batch_id'])
    op.add_column('finished_batches', sa.Column('aggregate_product_id', sa.Integer(), sa.Computed(
        "CASE WHEN stock_scope = 'aggregate' THEN product_id ELSE NULL END", persisted=True)))
    op.add_column('finished_batches', sa.Column('real_batch_no', sa.String(80), sa.Computed(
        "CASE WHEN stock_scope = 'batch' THEN batch_no ELSE NULL END", persisted=True)))
    op.create_unique_constraint('uq_finished_batches_aggregate_product', 'finished_batches', ['aggregate_product_id'])
    op.create_unique_constraint('uq_finished_batches_real_batch', 'finished_batches', ['product_id', 'real_batch_no'])
    op.drop_constraint('uq_finished_batches_product_scope', 'finished_batches', type_='unique')
    replace('finished_batches', 'ck_finished_batches_valid_scope', NEW_SCOPE)
    replace('stock_documents', 'ck_stock_documents_valid_reversal', reversal_check(OLD_KINDS + ",'issue','supplement','return'"))


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM finished_batches WHERE stock_scope='batch'")).scalar_one() or bind.execute(sa.text(
            "SELECT COUNT(*) FROM stock_documents WHERE kind='reversal' AND reverses_kind IN ('issue','supplement','return')")).scalar_one():
        raise RuntimeError('0014 immutable facts exist; downgrade cannot discard real batches or material reversals')
    for column in ('document_id', 'production_order_id'):
        if bind.execute(sa.text(f'SELECT COUNT(*) FROM (SELECT {column} FROM finished_receipts WHERE {column} IS NOT NULL '
                                f'GROUP BY {column} HAVING COUNT(*) > 1) duplicates')).scalar_one():
            raise RuntimeError('Multi-row receipts exist; downgrade would invalidate history')
    replace('stock_documents', 'ck_stock_documents_valid_reversal', reversal_check(OLD_KINDS))
    replace('finished_batches', 'ck_finished_batches_valid_scope', OLD_SCOPE)
    op.create_unique_constraint('uq_finished_batches_product_scope', 'finished_batches', ['product_id', 'stock_scope'])
    for name in ('uq_finished_batches_aggregate_product', 'uq_finished_batches_real_batch'):
        op.drop_constraint(name, 'finished_batches', type_='unique')
    for name in ('aggregate_product_id', 'real_batch_no'):
        op.drop_column('finished_batches', name)
    for column in ('document_id', 'production_order_id'):
        op.create_unique_constraint('uq_finished_receipts_' + column, 'finished_receipts', [column])
        op.drop_index('ix_finished_receipts_' + column, table_name='finished_receipts')
    op.drop_constraint('uq_finished_receipts_order_batch', 'finished_receipts', type_='unique')
