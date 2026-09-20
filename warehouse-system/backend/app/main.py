import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from starlette.exceptions import HTTPException

from app.config import get_settings
from app.database import engine
from app.schemas import Envelope
from app import auth, bom, catalog, users, stock, finished, production, losses, overview
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.config import BACKEND_DIR

settings = get_settings()
expected_revision = ScriptDirectory.from_config(Config(str(BACKEND_DIR / 'alembic.ini'))).get_current_head()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger('warehouse')
app = FastAPI(title=settings.app_name, version='1.0.0', description='V1.0：仓库管理、生产领补退与损耗追溯。')
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                   allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'], allow_headers=['Content-Type', 'Authorization', 'X-Request-ID'])


app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(users.router)
app.include_router(stock.router)
app.include_router(finished.router)
app.include_router(bom.router)
app.include_router(production.router)
app.include_router(losses.router)
app.include_router(overview.router)


def error(status: int, code: str, message: str):
    return JSONResponse(status_code=status, content={'code': code, 'message': message, 'data': None})


@app.middleware('http')
async def request_log(request: Request, call_next):
    request_id = str(uuid4())
    started = time.perf_counter()
    response = await call_next(request)
    response.headers['X-Request-ID'] = request_id
    logger.info('%s %s %s %.1fms request_id=%s', request.method, request.url.path,
                response.status_code, (time.perf_counter() - started) * 1000, request_id)
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return error(422, 'VALIDATION_ERROR', '参数格式不正确，请检查输入')


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return error(exc.status_code, 'HTTP_ERROR', str(exc.detail))


@app.exception_handler(IntegrityError)
async def integrity_error(request, exc):
    return error(409, 'DATA_CONFLICT', '编码或用户名已使用（包含已删除记录），或数据存在引用冲突')


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    # 不将 SQL、连接信息或用户输入写入响应/日志。
    logger.error('Unhandled error type=%s path=%s', type(exc).__name__, request.url.path)
    return error(500, 'INTERNAL_ERROR', '服务暂时不可用，请稍后重试')


@app.get('/api/health', response_model=Envelope, tags=['系统状态'])
def health():
    return Envelope(data={'status': 'ok', 'version': '1.0.0', 'phase': 9,
                          'time': datetime.now(timezone.utc).isoformat(), 'timezone': settings.business_timezone})


@app.get('/api/health/db', response_model=Envelope, responses={503: {'model': Envelope}}, tags=['系统状态'])
def database_health():
    try:
        with engine.connect() as connection:
            version = connection.execute(text('SELECT VERSION()')).scalar_one()
            revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        if revision != expected_revision:
            return error(503, 'MIGRATION_REQUIRED', '数据库版本落后，请先运行 Alembic 迁移')
        return Envelope(data={'status': 'ok', 'engine': 'MySQL', 'version': version, 'migration': revision})
    except SQLAlchemyError as exc:
        logger.warning('Database readiness failed type=%s', type(exc).__name__)
        return error(503, 'DATABASE_NOT_READY', '数据库连接或迁移尚未就绪，请检查配置并运行迁移')
