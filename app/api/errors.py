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


def auth_hint(api_key: str, base_url: str) -> str:
    """401/403 时按「密钥前缀 + 接口地址」给一句能落地的排查方向（纯函数，便于测试）。

    分类层有两个平台，密钥不能混用、模型名写法也不同，配串了的表现就是 401：
    OpenRouter 的密钥以 `sk-or-` 开头、地址写完整端点；TypeSafe 原生网关用自带的密钥。
    """
    key = (api_key or '').strip()
    base = (base_url or '').strip() or 'https://api.typesafe.ai'
    on_openrouter = 'openrouter.ai' in base
    if key.startswith('sk-or-') and not on_openrouter:
        return ('当前密钥是 OpenRouter 的（sk-or- 开头），但接口地址指向 {base}，两者不配套。'
                '走 OpenRouter 请把地址改成完整端点 https://openrouter.ai/api/alpha/decisions、'
                '模型名写成 typesafe/jev-1.13；走 TypeSafe 原生网关则要换成 TypeSafe 自己的密钥。'
                .format(base=base))
    if on_openrouter and not key.startswith('sk-or-'):
        return ('接口地址指向 OpenRouter，但当前密钥不是 OpenRouter 的（应以 sk-or- 开头）。'
                '请填 OpenRouter 的密钥，并把模型名写成 typesafe/jev-1.13。')
    return ('请按接口地址指向的平台核对密钥、账号与模型权限：'
            'TypeSafe 原生网关用 TypeSafe 的密钥 + 模型 jev-1.13.0；'
            'OpenRouter 用 sk-or- 开头的密钥 + 模型 typesafe/jev-1.13。')


def jev_api_error_message(status: int, api_key: str | None = None,
                          base_url: str | None = None) -> str:
    """把上游状态码翻译成「用户下一步该做什么」。

    api_key / base_url 只在调用方手上有「比进程当前配置更新的值」时才传：连通性自检
    探测的是界面上还没保存的那把密钥 / 那个地址（见 services/settings.py），拿进程里的
    旧值去提示会说反。都不传就沿用当前配置，一次性端点的行为不变。
    """
    if status in (401, 403):
        if api_key is None or base_url is None:
            from app.core.config import get_settings
            settings = get_settings()
            api_key = settings.typesafe_api_key if api_key is None else api_key
            base_url = settings.typesafe_base_url if base_url is None else base_url
        return 'Jev 拒绝了请求（HTTP {}）。API Key 已读到，但上游不认：{}'.format(
            status, auth_hint(api_key, base_url))
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
