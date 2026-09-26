import logging
import time
from datetime import datetime, timezone
from pathlib import Path
import traceback
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from starlette.exceptions import HTTPException

from app.config import get_settings
from app.database import engine
from app.schemas import Envelope
from app import auth, bom, catalog, users, stock, finished, production, losses, overview, reversals
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.config import BACKEND_DIR

settings = get_settings()
expected_revision = ScriptDirectory.from_config(Config(str(BACKEND_DIR / 'alembic.ini'))).get_current_head()
logging.basicConfig(level=getattr(logging, settings.log_level),
                    format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger('warehouse')
logger.setLevel(getattr(logging, settings.log_level))
app = FastAPI(title=settings.app_name, version='1.0.0',
              description='V1.0：仓库管理、生产领补退与损耗追溯。',
              docs_url='/docs' if settings.api_docs_enabled else None,
              redoc_url=None,
              openapi_url='/openapi.json' if settings.api_docs_enabled else None)
if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'],
                       allow_headers=['Content-Type', 'Authorization', 'X-Request-ID'])


app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(users.router)
app.include_router(stock.router)
app.include_router(finished.router)
app.include_router(bom.router)
app.include_router(production.router)
app.include_router(losses.router)
app.include_router(overview.router)
app.include_router(reversals.router)


def error(status: int, code: str, message: str):
    return JSONResponse(status_code=status, content={'code': code, 'message': message, 'data': None})


@app.middleware('http')
async def request_log(request: Request, call_next):
    request_id = str(uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    response.headers['X-Request-ID'] = request_id
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    logger.info('%s %s %s %.1fms request_id=%s', request.method, request.url.path,
                response.status_code, (time.perf_counter() - started) * 1000, request_id)
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return error(422, 'VALIDATION_ERROR', '参数格式不正确，请检查输入')


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return error(exc.status_code, getattr(exc, 'code', 'HTTP_ERROR'), str(exc.detail))


@app.exception_handler(IntegrityError)
async def integrity_error(request, exc):
    # 数据库唯一键/外键冲突统一对外隐藏 SQL 和参数；请求事务会由 SessionDep 自动回滚。
    return error(409, 'DATA_CONFLICT', '编码或用户名已使用（包含已删除记录），或数据存在引用冲突')


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    # 只记录代码位置，不记录异常正文、SQL 参数、密码或用户输入。
    frames = traceback.extract_tb(exc.__traceback__)
    location = ' <- '.join(f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}' for frame in frames[-6:])
    logger.error('Unhandled error type=%s path=%s request_id=%s stack=%s', type(exc).__name__,
                 request.url.path, getattr(request.state, 'request_id', '-'), location or '-')
    return error(500, 'INTERNAL_ERROR', '服务暂时不可用，请稍后重试')


@app.get('/api/health', response_model=Envelope, tags=['系统状态'])
def health():
    return Envelope(data={'status': 'ok', 'version': '1.0.0', 'phase': 10,
                          'time': datetime.now(timezone.utc).isoformat(), 'timezone': settings.business_timezone})


@app.get('/api/health/db', response_model=Envelope, responses={503: {'model': Envelope}}, tags=['系统状态'])
def database_health():
    try:
        with engine.connect() as connection:
            version = connection.execute(text('SELECT VERSION()')).scalar_one()
            revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        if revision != expected_revision:
            # 连接成功不等于可接流量，迁移版本落后时明确返回未就绪。
            return error(503, 'MIGRATION_REQUIRED', '数据库版本落后，请先运行 Alembic 迁移')
        return Envelope(data={'status': 'ok', 'engine': 'MySQL', 'version': version, 'migration': revision})
    except SQLAlchemyError as exc:
        logger.warning('Database readiness failed type=%s', type(exc).__name__)
        return error(503, 'DATABASE_NOT_READY', '数据库连接或迁移尚未就绪，请检查配置并运行迁移')


if settings.serve_frontend:
    frontend_dir = settings.frontend_directory
    frontend_index = frontend_dir / 'index.html'
    if not frontend_index.is_file():
        raise RuntimeError('未找到前端生产构建，请先在 frontend 执行 npm run build')

    @app.get('/{full_path:path}', include_in_schema=False)
    def production_frontend(full_path: str):
        # API 不存在时仍返回 JSON 404，不能被 Vue history 回退吞掉。
        if full_path == 'api' or full_path.startswith('api/'):
            raise HTTPException(404, '接口不存在')
        if not settings.api_docs_enabled and full_path in {'docs', 'redoc', 'openapi.json'}:
            raise HTTPException(404, '页面不存在')
        candidate = (frontend_dir / full_path).resolve()
        try:
            candidate.relative_to(frontend_dir)
        except ValueError:
            raise HTTPException(404, '页面不存在') from None
        if candidate.is_file():
            cache = 'public, max-age=31536000, immutable' if full_path.startswith('assets/') else 'no-cache'
            return FileResponse(candidate, headers={'Cache-Control': cache})
        return FileResponse(frontend_index, headers={'Cache-Control': 'no-cache'})
