"""Extend only the permitted reversal kinds; preserve all historical facts."""
from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None
OLD = "'material_outbound','finished_outbound'"
NEW = OLD + ",'material_receipt','putaway','transfer','finished_receipt','adjustment'"


def expression(kinds):
    return ("(kind <> 'reversal' AND reverses_document_id IS NULL AND reverses_kind IS NULL "
            "AND original_document_no_snapshot IS NULL AND reversal_reason IS NULL) OR "
            "(kind = 'reversal' AND reverses_document_id IS NOT NULL AND reverses_kind IS NOT NULL "
            f"AND reverses_kind IN ({kinds}) AND original_document_no_snapshot IS NOT NULL "
            "AND reversal_reason IS NOT NULL AND CHAR_LENGTH(TRIM(reversal_reason)) > 0)")


def replace_check(kinds):
    # One ALTER avoids a committed interval with the CHECK absent on MySQL.
    op.execute(sa.text("ALTER TABLE stock_documents DROP CHECK ck_stock_documents_valid_reversal, "
                       "ADD CONSTRAINT ck_stock_documents_valid_reversal CHECK (" + expression(kinds) + ")"))


def upgrade():
    replace_check(NEW)


def downgrade():
    count = op.get_bind().execute(sa.text(
        "SELECT COUNT(*) FROM stock_documents WHERE kind='reversal' "
        f"AND reverses_kind NOT IN ({OLD})")).scalar_one()
    if count:
        raise RuntimeError('Second-batch reversal history exists; downgrade would invalidate immutable facts')
    replace_check(OLD)
