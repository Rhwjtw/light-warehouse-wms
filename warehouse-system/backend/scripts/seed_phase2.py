"""初始化本机账户与演示档案；重跑不重置密码、不覆盖现有资料、不创建库存。"""
import secrets
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.auth import audit, hash_password
from app.database import SessionLocal
from app.models import FinishedProduct, Location, RawMaterial, Role, Supplier, User

output = Path(__file__).resolve().parents[2] / '.runtime' / 'phase-2-accounts.txt'


def main():
    output.parent.mkdir(exist_ok=True)
    created = []
    with SessionLocal.begin() as session:
        for username, name, role_code in [('admin', '系统管理员', 'admin'), ('warehouse', '仓库演示账号', 'warehouse'), ('workshop', '车间演示账号', 'workshop')]:
            if session.scalar(select(User).where(User.username == username)):
                continue
            password = secrets.token_urlsafe(15)
            role = session.scalar(select(Role).where(Role.code == role_code))
            if role is None:
                raise RuntimeError('请先执行 Alembic 迁移')
            row = User(username=username, display_name=name, role_id=role.id, password_hash=hash_password(password))
            session.add(row); session.flush()
            audit(session, row.id, 'initialize_account', 'users', row.id)
            created.append(f'{name}\n用户名：{username}\n初始密码：{password}\n')
        admin = session.scalar(select(User).where(User.username == 'admin'))
        samples = [(Supplier, {'code': 'DEMO-SUP', 'name': '测试供应商'}), (Location, {'code': 'DEMO-A01', 'name': '测试库位 A-01'}),
                   (FinishedProduct, {'code': 'DEMO-FG', 'name': '测试面霜', 'unit': '盒'}),
                   *[(RawMaterial, {'code': f'DEMO-RAW-{letter}', 'name': f'原料{letter}', 'unit': 'kg'}) for letter in 'ABC']]
        for model, data in samples:
            if not session.scalar(select(model).where(model.code == data['code'])):
                row = model(**data, notes='演示基础资料；尚无入库、BOM 或生产记录')
                session.add(row); session.flush()
                audit(session, admin.id, 'initialize_demo', model.__tablename__, row.id)
        # 文件写失败会回滚初始化事务，避免生成无法取得密码的账户。
        if created:
            with output.open('a', encoding='utf-8') as file:
                file.write('本地初始化账户，请妥善保管。修改密码后此处初始密码不再有效。\n\n' + '\n'.join(created) + '\n')
    print(f'初始化完成；新增 {len(created)} 个账户。凭据已保存在项目 .runtime/phase-2-accounts.txt，未输出密码。')


if __name__ == '__main__':
    main()
