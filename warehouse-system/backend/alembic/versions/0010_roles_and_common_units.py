"""Add the requested fixed roles and common unit choices.

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE roles SET name = '仓库主管' WHERE code = 'warehouse'")
    op.execute("UPDATE roles SET name = '车间班长' WHERE code = 'workshop'")
    roles = sa.table('roles', sa.column('code', sa.String), sa.column('name', sa.String))
    for code, name in [('workshop_director', '车间主任'), ('accountant', '会计')]:
        op.execute(roles.insert().prefix_with('IGNORE').values(code=code, name=name))
    units = sa.table('units', sa.column('code', sa.String), sa.column('dimension', sa.String),
                     sa.column('factor', sa.Numeric))
    for code in ('袋', '箱'):
        op.execute(units.insert().prefix_with('IGNORE').values(code=code, dimension='u:' + code, factor=1))


def downgrade():
    op.execute("DELETE FROM units WHERE code IN ('袋', '箱')")
    op.execute("DELETE FROM roles WHERE code IN ('workshop_director', 'accountant')")
    op.execute("UPDATE roles SET name = '仓库人员' WHERE code = 'warehouse'")
    op.execute("UPDATE roles SET name = '车间人员' WHERE code = 'workshop'")
