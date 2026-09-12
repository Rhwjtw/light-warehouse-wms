"""基础角色、单位与预警规则。

Revision ID: 0002
Revises: 0001
"""
from datetime import datetime, timezone
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def insert_reference(table, rows):
    # MySQL DDL 隐式提交；初始化中断后可安全重试，不覆盖现有配置。
    for row in rows:
        op.execute(table.insert().prefix_with('IGNORE').values(**row))


def upgrade():
    insert_reference(sa.table('roles', sa.column('id', sa.Integer), sa.column('code', sa.String), sa.column('name', sa.String)), [
        {'id': 1, 'code': 'admin', 'name': '管理员'},
        {'id': 2, 'code': 'warehouse', 'name': '仓库人员'},
        {'id': 3, 'code': 'workshop', 'name': '车间人员'},
    ])
    insert_reference(sa.table('units', sa.column('code', sa.String), sa.column('dimension', sa.String), sa.column('factor', sa.Numeric)), [
        {'code': 'g', 'dimension': 'mass', 'factor': 1},
        {'code': 'kg', 'dimension': 'mass', 'factor': 1000},
        {'code': 'mL', 'dimension': 'volume', 'factor': 1},
        {'code': 'L', 'dimension': 'volume', 'factor': 1000},
        *[{'code': unit, 'dimension': 'count_' + str(i), 'factor': 1} for i, unit in enumerate(['个', '盒', '瓶', '支', '套'])],
    ])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    insert_reference(sa.table('warning_rules', sa.column('id', sa.Integer), sa.column('code', sa.String),
                           sa.column('name', sa.String), sa.column('days', sa.Integer),
                           sa.column('is_active', sa.Boolean), sa.column('created_at', sa.DateTime), sa.column('updated_at', sa.DateTime)), [
        {'id': 1, 'code': 'expiry_near', 'name': '近期到期', 'days': 30, 'is_active': True, 'created_at': now, 'updated_at': now},
        {'id': 2, 'code': 'expiry_early', 'name': '提前预警', 'days': 90, 'is_active': True, 'created_at': now, 'updated_at': now},
    ])


def downgrade():
    op.execute("DELETE FROM warning_rules WHERE code IN ('expiry_near', 'expiry_early')")
    op.execute("DELETE FROM units WHERE code IN ('g','kg','mL','L','个','盒','瓶','支','套')")
    op.execute("DELETE FROM roles WHERE code IN ('admin','warehouse','workshop')")
