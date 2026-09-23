"""请求守卫：来源白名单、Content-Type 与体积上限。

对应旧版 `Handler.do_POST` 开头的三行检查——顺序与提示语都保持一致，前端与
任何本机脚本看到的响应不变。
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.core.config import ALLOWED_ORIGIN, MAX_BODY_BYTES
from app.core.exceptions import InvalidRequest


def _is_json_content_type(value: str | None) -> bool:
    # 旧版要求正好是 application/json；这里放宽到允许带 charset，避免
    # 一些客户端（curl -d 默认会给 charset）被 403 挡在门外。
    return bool(value) and value.split(';')[0].strip().lower() == 'application/json'


def _assert_origin(request: Request) -> None:
    origin = request.headers.get('origin')
    if origin is not None and not ALLOWED_ORIGIN.match(origin):
        raise HTTPException(status_code=403, detail='请求来源不正确')


def guard_origin(request: Request) -> None:
    """只校验来源的写操作（DELETE 这类没有请求体的接口）。

    体积检查在这里不适用：`DELETE /sessions/{id}` 不带请求体，
    Content-Length 为 0 或缺失，用下面那条规则会被判成「输入过长或为空」。
    """
    _assert_origin(request)


def guard_json_post(request: Request) -> None:
    """挂在所有带 JSON 请求体的写接口上的依赖。"""
    _assert_origin(request)
    if not _is_json_content_type(request.headers.get('content-type')):
        raise HTTPException(status_code=403, detail='请求来源不正确')
    try:
        length = int(request.headers.get('content-length') or 0)
    except ValueError:
        length = 0
    if not 0 < length <= MAX_BODY_BYTES:
        raise InvalidRequest('输入过长或为空')
