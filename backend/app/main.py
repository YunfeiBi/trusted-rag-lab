import logging
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException

from .config import ROOT, Settings
from .routes import router
from .documents import router as documents_router
from .search import router as search_router
from .model_client import EmbeddingClient, RerankClient

logger = logging.getLogger('trusted_rag.requests')
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)


def create_app(settings=None, engine=None):
    owns_engine = engine is None

    @asynccontextmanager
    async def lifespan(application):
        yield
        if owns_engine:
            application.state.engine.dispose()

    application = FastAPI(title='可信 RAG 知识服务', version='0.1.0', lifespan=lifespan)
    settings = settings or Settings.load()
    application.state.settings = settings
    application.state.embedding_client = EmbeddingClient(timeout=15)
    application.state.rerank_client = RerankClient()
    application.state.engine = engine or create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
    application.state.session_factory = sessionmaker(application.state.engine, expire_on_commit=False)
    application.state.migration_head = ScriptDirectory.from_config(Config(str(ROOT / 'alembic.ini'))).get_current_head()

    def error(request, status, code, message, headers=None):
        return JSONResponse(status_code=status, content={'error': {'code': code, 'message': message}, 'trace_id': getattr(request.state, 'trace_id', '')}, headers=headers)

    @application.middleware('http')
    async def trace(request: Request, call_next):
        request.state.trace_id = uuid4().hex
        start = perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error('unhandled_error type=%s trace_id=%s', type(exc).__name__, request.state.trace_id)
            response = error(request, 500, 'INTERNAL_ERROR', '服务内部错误，请凭追踪编号反馈')
        response.headers['X-Request-ID'] = request.state.trace_id
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Do not log request bodies, query strings, credentials or exception text.
        logger.info('method=%s path=%s status=%s duration_ms=%.2f trace_id=%s', request.method, request.url.path, response.status_code, (perf_counter()-start)*1000, request.state.trace_id)
        return response

    @application.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(request, exc.status_code, f'HTTP_{exc.status_code}', str(exc.detail), exc.headers)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Default validation details include submitted password values; omit them.
        return error(request, 422, 'INVALID_INPUT', '输入格式不正确，请检查必填项和字段长度')

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        logger.error('database_error type=%s trace_id=%s', type(exc).__name__, request.state.trace_id)
        return error(request, 503, 'DATABASE_UNAVAILABLE', '数据库暂不可用或尚未迁移')

    application.include_router(router)
    application.include_router(documents_router)
    application.include_router(search_router)
    return application


app = create_app()
