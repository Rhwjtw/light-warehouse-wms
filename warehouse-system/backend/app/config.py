from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / '.env', env_file_encoding='utf-8', extra='ignore')
    app_name: str = '轻量级仓库管理与生产领料系统'
    app_env: Literal['development', 'production'] = 'development'
    mysql_host: str = '127.0.0.1'
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: str = 'warehouse_system'
    mysql_user: str = 'warehouse_app'
    mysql_password: SecretStr = SecretStr('')
    mysql_migration_user: str = 'warehouse_migrator'
    mysql_migration_password: SecretStr = SecretStr('')
    cors_origins: list[str] = ['http://127.0.0.1:5173', 'http://localhost:5173']
    business_timezone: str = 'Asia/Shanghai'
    auth_secret: SecretStr = SecretStr('')
    auth_token_hours: int = Field(default=8, ge=1, le=24)
    api_docs_enabled: bool = True
    serve_frontend: bool = False
    frontend_dist: Path = Path('../frontend/dist')
    log_level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] = 'INFO'

    @field_validator('business_timezone')
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator('app_env', mode='before')
    @classmethod
    def normalize_environment(cls, value: str) -> str:
        return str(value).strip().lower()

    @field_validator('log_level', mode='before')
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return str(value).strip().upper()

    @model_validator(mode='after')
    def validate_production_secrets(self):
        if self.app_env != 'production':
            return self
        if len(self.auth_secret.get_secret_value()) < 32:
            raise ValueError('生产环境 AUTH_SECRET 必须是至少 32 字符的随机值')
        if not self.mysql_password.get_secret_value() or not self.mysql_migration_password.get_secret_value():
            raise ValueError('生产环境必须配置应用和迁移数据库密码')
        if self.mysql_user.lower() == 'root' or self.mysql_migration_user.lower() == 'root':
            raise ValueError('生产环境禁止使用 root 作为应用或迁移账号')
        if self.mysql_user == self.mysql_migration_user:
            raise ValueError('生产环境应用账号和迁移账号必须分离')
        if '*' in self.cors_origins:
            raise ValueError('生产环境 CORS_ORIGINS 禁止使用通配符')
        return self

    @property
    def database_url(self) -> URL:
        # URL.create 正确处理密码中的 @、#、: 等字符，无须手动转义。
        return URL.create('mysql+pymysql', username=self.mysql_user,
                          password=self.mysql_password.get_secret_value(), host=self.mysql_host,
                          port=self.mysql_port, database=self.mysql_database,
                          query={'charset': 'utf8mb4'})

    @property
    def frontend_directory(self) -> Path:
        path = self.frontend_dist
        return path.resolve() if path.is_absolute() else (BACKEND_DIR / path).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
