"""静态资源托管：把 Vue 构建产物（frontend/dist）挂到本地服务上。

只从 dist 目录读文件，并按扩展名给 Content-Type；页面本身是纯前端应用，
不需要 SPA 深链回退（没有路由），未知地址仍然回 JSON 错误，保持旧契约。
"""
from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Response

from app.api.responses import Utf8JSONResponse
from app.core.config import BUILD_HINT, FRONTEND_ASSETS, FRONTEND_INDEX

router = APIRouter(tags=['页面'])
# 兜底路由必须最后注册（见 main.py 的 include 顺序），否则会把真实接口/页面一起吞掉。
fallback_router = APIRouter(include_in_schema=False)

mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('text/css', '.css')


def _missing_build() -> Utf8JSONResponse:
    return Utf8JSONResponse(status_code=503, content={'error': BUILD_HINT, 'code': 'FRONTEND_NOT_BUILT'})


@router.get('/', include_in_schema=False)
def index() -> Response:
    if not FRONTEND_INDEX.is_file():
        return _missing_build()
    return Response(content=FRONTEND_INDEX.read_bytes(), media_type='text/html; charset=utf-8')


@router.get('/assets/{asset_path:path}', include_in_schema=False)
def asset(asset_path: str) -> Response:
    """逐请求从磁盘读，改完前端重新构建即可生效，不必重启服务。"""
    root = FRONTEND_ASSETS.resolve()
    target = (root / asset_path).resolve()
    if not target.is_file() or root not in target.parents:
        return Utf8JSONResponse(status_code=404, content={'error': '资源不存在'})
    media_type, _ = mimetypes.guess_type(target.name)
    return Response(content=target.read_bytes(), media_type=media_type or 'application/octet-stream')


@router.get('/favicon.ico', include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@fallback_router.get('/{full_path:path}', include_in_schema=False)
def page_not_found(full_path: str) -> Utf8JSONResponse:
    return Utf8JSONResponse(status_code=404, content={'error': '页面不存在'})


@fallback_router.post('/{full_path:path}', include_in_schema=False)
def endpoint_not_found(full_path: str) -> Utf8JSONResponse:
    return Utf8JSONResponse(status_code=404, content={'error': '接口不存在'})
