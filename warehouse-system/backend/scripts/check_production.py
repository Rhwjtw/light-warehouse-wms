"""生产启动前只读检查；不输出任何密码、密钥或数据库连接串。"""
import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import dotenv_values
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BACKEND_DIR, get_settings
from app.database import engine


ENV = BACKEND_DIR / '.env'


def environment_value(values, key: str) -> str:
    return os.environ[key] if key in os.environ else str(values.get(key) or '')


def main() -> None:
    values = dotenv_values(ENV)
    settings = get_settings()
    problems: list[str] = []
    if settings.app_env != 'production':
        problems.append('APP_ENV 必须为 production')
    if not settings.serve_frontend:
        problems.append('当前 Windows 一体化启动要求 SERVE_FRONTEND=true')
    if environment_value(values, 'MYSQL_ADMIN_PASSWORD'):
        problems.append('MYSQL_ADMIN_PASSWORD 初始化后必须清空')
    if environment_value(values, 'INITIAL_ADMIN_PASSWORD'):
        problems.append('INITIAL_ADMIN_PASSWORD 创建管理员后必须清空')
    if not settings.api_docs_enabled:
        docs_status = '关闭'
    else:
        docs_status = '开启（仅应在受控网络内使用）'
    frontend_index = settings.frontend_directory / 'index.html'
    if not frontend_index.is_file():
        problems.append('未找到 frontend/dist/index.html，请先执行前端生产构建')
    else:
        for asset in settings.frontend_directory.rglob('*'):
            if asset.is_file() and asset.suffix.lower() in {'.html', '.js', '.css'}:
                if b'127.0.0.1:8000' in asset.read_bytes():
                    problems.append(f'前端构建包含写死的 127.0.0.1:8000：{asset.name}')
                    break

    expected = ScriptDirectory.from_config(Config(str(BACKEND_DIR / 'alembic.ini'))).get_current_head()
    revision = None
    try:
        with engine.connect() as connection:
            revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
    except Exception as exc:
        problems.append(f'数据库连接或迁移检查失败（{type(exc).__name__}）')
    if revision is not None and revision != expected:
        problems.append(f'数据库迁移为 {revision}，当前代码 head 为 {expected}')
    if problems:
        for problem in problems:
            print(f'[失败] {problem}', file=sys.stderr)
        raise SystemExit(1)
    print(f'生产配置检查通过：数据库迁移 {revision}，Swagger {docs_status}，前端同源 /api。')


if __name__ == '__main__':
    main()
