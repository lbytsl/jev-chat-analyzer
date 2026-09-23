"""FastAPI 应用装配：中间件、异常映射、路由挂载。

异常映射是这一版的关键：业务异常 → 用户能看懂的中文提示 + 恰当的 HTTP 状态，
所以 services 层只管抛领域异常，不用关心 HTTP。对应关系（与原单文件版一致）：

    InvalidRequest        400  入参/业务校验失败，消息直接给用户看
    JevConfigurationError 500  本地没读到 key
    JevAPIError           502  上游明确报错（401/403/429/400/其他分别给不同提示）
    JevResponseError      502  上游返回结构不兼容
    JevConnectionError    503  上游连不上（已自动重试过）
    其他未知异常           502  兜底，不把堆栈泄漏给前端，只写日志
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.errors import describe_error
from app.api.responses import Utf8JSONResponse
from app.api.routes import analysis, config, health, pages, sessions
from app.core.config import ALLOWED_ORIGIN_REGEX, VERSION
from app.core.exceptions import (
    GeneralLLMError,
    InvalidRequest,
    JevAPIError,
    JevConfigurationError,
    JevConnectionError,
    JevResponseError,
    NotFoundError,
)
from app.core.logging import configure_logging, get_logger

logger = get_logger('api')

# 意料内的业务异常：不打印堆栈（它们天天见），只记一行。
KNOWN_ERRORS = (InvalidRequest, NotFoundError, JevConfigurationError, JevAPIError,
                JevConnectionError, JevResponseError, GeneralLLMError, RequestValidationError)


class ResponseHeadersMiddleware(BaseHTTPMiddleware):
    """统一响应头：版本标识 + 禁止缓存 + 禁止嗅探内容类型。

    身份头 X-Jev 供调试与同源页面识别；跨域时必须显式 expose，否则浏览器会过滤掉。
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers['X-Jev'] = VERSION
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response


def error_response(status: int, content: dict) -> Utf8JSONResponse:
    """统一错误响应。

    这里显式带版本头与禁缓存头：兜底异常由最外层的 ServerErrorMiddleware 产出，
    走不到 ResponseHeadersMiddleware，只靠中间件会让错误响应缺 X-Jev。
    """
    return Utf8JSONResponse(status_code=status, content=content,
                            headers={'X-Jev': VERSION, 'Cache-Control': 'no-store',
                                     'X-Content-Type-Options': 'nosniff'})


def register_exception_handlers(app: FastAPI) -> None:
    """异常 → 响应：映射统一在 app/api/errors.py，这里只负责日志与响应封装。

    流式端点用同一套映射（见 app/api/streaming.py 的 error_event），两边说法不会分叉。
    """

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException):
        return error_response(exc.status_code, {'error': exc.detail})

    @app.exception_handler(RequestValidationError)
    async def _invalid_body(request: Request, exc: RequestValidationError):
        # FastAPI 自带 422 处理器，会盖住下面的兜底 handler；这里显式接管，
        # 保住「error 字段是一句中文」的老契约（前端直接显示在状态栏）。
        status, body = describe_error(exc)
        return error_response(status, body)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        status, body = describe_error(exc)
        if type(exc) not in KNOWN_ERRORS:
            # 意外异常打完整堆栈；业务异常（400/404/502/503）天天见，不必污染日志。
            # 走 logger.exception 而不是 traceback.print_exc()：后者直接写 stderr，
            # 绕过 logging 的格式/去向/级别配置，同一个错误还会被记两遍。
            logger.exception('未预期的异常：%s', type(exc).__name__)
        elif status >= 500:
            logger.error('%s：%s', type(exc).__name__, exc)
        return error_response(status, body)


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title='恋爱·职场聊天神器 API',
        version=VERSION,
        description='本地聊天记录分析：Jev 做意图/情绪分类，OpenAI 兼容端点做潜台词与回复建议。',
        docs_url='/docs',
        redoc_url=None,
        default_response_class=Utf8JSONResponse,
    )
    # 允许的页面来源：本机任意端口（内置预览、本地静态服务）+ file:// 打开的页面（Origin: null）。
    # 仍然只绑 127.0.0.1，外部网络进不来；代价是本机任意页面都能调用本接口（会消耗 API 额度）。
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=ALLOWED_ORIGIN_REGEX,
        # PATCH / DELETE 是会话接口要用的：跨源（vite dev 5173）时漏了它们，预检会直接被拒。
        allow_methods=['GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS'],
        allow_headers=['Content-Type'],
        expose_headers=['X-Jev'],
        max_age=600,
    )
    app.add_middleware(ResponseHeadersMiddleware)
    register_exception_handlers(app)

    app.include_router(pages.router)
    app.include_router(health.router)
    app.include_router(analysis.router)
    app.include_router(sessions.router)
    app.include_router(config.router)
    # 兜底路由必须最后挂，否则会把上面所有接口一起吞掉。
    app.include_router(pages.fallback_router)
    return app


app = create_app()
