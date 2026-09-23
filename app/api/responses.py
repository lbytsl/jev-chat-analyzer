"""响应类。

FastAPI 默认的 JSONResponse 只声明 `application/json`，而旧版 stdlib 服务发的是
`application/json; charset=utf-8`。语义上等价（JSON 按 RFC 8259 就是 UTF-8），
但既然对外契约要「换框架不改行为」，就把 charset 显式带上，别给下游留下判断分叉。
"""
from __future__ import annotations

from starlette.responses import JSONResponse


class Utf8JSONResponse(JSONResponse):
    media_type = 'application/json; charset=utf-8'
