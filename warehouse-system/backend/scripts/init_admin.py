"""正式环境首个管理员初始化；可安全重跑，不覆盖已有账号或密码。"""
import os
import re
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import audit, hash_password
from app.database import SessionLocal
from app.models import Role, User


ENV = Path(__file__).resolve().parents[1] / '.env'
USERNAME_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$')


def ensure_initial_admin(session, *, username: str, display_name: str, password: str) -> bool:
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError('管理员用户名需为 3～64 位字母、数字、点、下划线或连字符')
    if not 1 <= len(display_name) <= 80:
        raise ValueError('管理员显示名称需为 1～80 个字符')
    if not 14 <= len(password) <= 128:
        raise ValueError('初始管理员密码需为 14～128 个字符')
    if password.lower() in {'password', 'admin123456789', '12345678901234'} or username.lower() in password.lower():
        raise ValueError('初始管理员密码过于简单，且不能包含用户名')

    role = session.scalar(select(Role).where(Role.code == 'admin').with_for_update())
    if role is None:
        raise RuntimeError('未找到管理员角色，请先执行 Alembic 迁移')
    existing = session.scalar(select(User).where(User.username == username).with_for_update())
    if existing is not None:
        if existing.role_id != role.id or existing.deleted_at is not None or not existing.is_active:
            raise RuntimeError('同名账号已存在但不是有效管理员，请人工核对，脚本不会覆盖')
        return False

    row = User(username=username, display_name=display_name, role_id=role.id,
               password_hash=hash_password(password), is_active=True)
    session.add(row)
    session.flush()
    audit(session, row.id, 'initialize_account', 'users', row.id)
    return True


def environment_value(values, key: str, default: str = '') -> str:
    return os.environ[key] if key in os.environ else str(values.get(key) or default)


def clear_initial_password(values) -> None:
    if 'INITIAL_ADMIN_PASSWORD' in os.environ:
        os.environ['INITIAL_ADMIN_PASSWORD'] = ''
    if 'INITIAL_ADMIN_PASSWORD' in values:
        set_key(str(ENV), 'INITIAL_ADMIN_PASSWORD', '', quote_mode='never')


def main() -> None:
    values = dotenv_values(ENV)
    if environment_value(values, 'APP_ENV', 'development').strip().lower() != 'production':
        raise ValueError('init_admin.py 仅用于 APP_ENV=production；开发演示数据继续使用 seed_phase2.py')
    password = environment_value(values, 'INITIAL_ADMIN_PASSWORD')
    if not password:
        raise ValueError('请先通过环境变量或 .env 设置 INITIAL_ADMIN_PASSWORD')
    username = environment_value(values, 'INITIAL_ADMIN_USERNAME', 'admin').strip()
    display_name = environment_value(values, 'INITIAL_ADMIN_DISPLAY_NAME', '系统管理员').strip()
    with SessionLocal.begin() as session:
        created = ensure_initial_admin(session, username=username, display_name=display_name, password=password)
    clear_initial_password(values)
    if created:
        print(f'初始管理员 {username} 已创建；INITIAL_ADMIN_PASSWORD 已清空。')
    else:
        print(f'管理员 {username} 已存在，未修改账号或密码；INITIAL_ADMIN_PASSWORD 已清空。')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'管理员初始化失败 ({type(exc).__name__})：{exc}', file=sys.stderr)
        sys.exit(1)
