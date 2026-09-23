"""异常 → 响应内容的统一映射。

普通端点（一次性 JSON）的异常处理器与流式端点的 `error` 事件都从这里取说法：
同一个异常在两条路径上必须给出一模一样的中文提示，否则流式与非流式会分叉。
"""
from __future__ import annotations

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    GeneralLLMError,
    InvalidRequest,
    JevAPIError,
    JevConfigurationError,
    JevConnectionError,
    JevResponseError,
    NotFoundError,
)

MISSING_KEY_MESSAGE = '本地服务没有读到 Jev API key。请确认项目根目录 .env 里有 TYPESAFE_API_KEY，然后重启服务。'
INVALID_BODY_MESSAGE = '请求格式不正确。'
JEV_CONNECTION_MESSAGE = 'Jev 服务暂时连接不上，已自动重试。请过几秒再点一次「开始解读」。'
JEV_RESPONSE_MESSAGE = 'Jev 已返回结果，但格式和当前项目不兼容，请查看服务日志。'
UNEXPECTED_MESSAGE = '这次没有取得 Jev 结果，请稍后重试。'


def jev_api_error_message(status: int) -> str:
    """把上游状态码翻译成「用户下一步该做什么」。"""
    if status in (401, 403):
        return ('Jev 拒绝了请求（HTTP {}）。API Key 已读取，但当前密钥、账号或模型权限不可用，'
                '请按 TYPESAFE_BASE_URL 指向的平台（TypeSafe 或 OpenRouter）核对密钥与模型权限后重试。'
                .format(status))
    if status == 429:
        return 'Jev 请求过于频繁或额度暂时受限（HTTP 429），请稍后再试，或减少一次分析的消息数量。'
    if status == 400:
        return 'Jev 无法接受这次请求（HTTP 400），请减少聊天长度后重试。'
    return 'Jev 上游返回 HTTP {}，请稍后重试。'.format(status)


def describe_error(exc: Exception) -> tuple[int, dict]:
    """异常 → (HTTP 状态码, 错误体)。判断顺序与原来的异常处理器一致。"""
    if isinstance(exc, NotFoundError):
        return 404, {'error': str(exc)}
    if isinstance(exc, RequestValidationError):
        # 请求体不是合法 JSON / 字段类型不对：保持「error 里是一句中文」的旧契约。
        return 400, {'error': INVALID_BODY_MESSAGE}
    if isinstance(exc, InvalidRequest):
        return 400, {'error': str(exc)}
    if isinstance(exc, JevConfigurationError):
        return 500, {'error': MISSING_KEY_MESSAGE}
    if isinstance(exc, JevAPIError):
        return 502, {'error': jev_api_error_message(exc.status),
                     'code': 'JEV_HTTP_{}'.format(exc.status)}
    if isinstance(exc, JevConnectionError):
        return 503, {'error': JEV_CONNECTION_MESSAGE}
    if isinstance(exc, JevResponseError):
        return 502, {'error': JEV_RESPONSE_MESSAGE, 'code': 'JEV_INVALID_RESPONSE'}
    if isinstance(exc, GeneralLLMError):
        # 生成层失败在业务里已被降级处理（gen_failed），走到这里说明是意料外的路径。
        return 502, {'error': str(exc)}
    if isinstance(exc, StarletteHTTPException):
        return exc.status_code, {'error': exc.detail}
    return 502, {'error': UNEXPECTED_MESSAGE}


def error_event(exc: Exception) -> dict:
    """流式响应里的错误事件。

    SSE 一旦开始推事件，HTTP 状态码就已经是 200 了，所以状态码随事件一起带上（便于排查），
    前端按「业务错误」处理：消息显示在抽屉状态栏，而不是顶部提示条。
    """
    status, body = describe_error(exc)
    return {'type': 'error', 'status': status, 'message': body.get('error') or UNEXPECTED_MESSAGE,
            'code': body.get('code')}
