from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / '.env', env_file_encoding='utf-8', extra='ignore')
    app_name: str = '轻量级仓库管理与生产领料系统'
    app_env: str = 'development'
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

    @field_validator('business_timezone')
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @property
    def database_url(self) -> URL:
        # URL.create 正确处理密码中的 @、#、: 等字符，无须手动转义。
        return URL.create('mysql+pymysql', username=self.mysql_user,
                          password=self.mysql_password.get_secret_value(), host=self.mysql_host,
                          port=self.mysql_port, database=self.mysql_database,
                          query={'charset': 'utf8mb4'})


@lru_cache
def get_settings() -> Settings:
    return Settings()
