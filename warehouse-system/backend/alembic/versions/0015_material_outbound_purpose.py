"""Material supplier return and scrap purpose; no historical rewrite."""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = depends_on = None

CHECK = ("material_outbound_purpose IS NULL OR (kind = 'material_outbound' "
         "AND material_outbound_purpose IN ('normal','supplier_return','scrap') "
         "AND (material_outbound_purpose = 'normal' OR "
         "(notes IS NOT NULL AND CHAR_LENGTH(TRIM(notes)) > 0)))")


def upgrade():
    op.add_column('stock_documents', sa.Column('material_outbound_purpose', sa.String(20), nullable=True))
    op.create_check_constraint(op.f('ck_stock_documents_valid_material_outbound_purpose'), 'stock_documents', CHECK)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM stock_documents WHERE "
            "material_outbound_purpose IN ('supplier_return','scrap') LIMIT 1")).first():
        raise RuntimeError('Cannot downgrade 0015: immutable facts contain supplier return or scrap purposes')
    op.drop_constraint(op.f('ck_stock_documents_valid_material_outbound_purpose'), 'stock_documents', type_='check')
    op.drop_column('stock_documents', 'material_outbound_purpose')
