"""SSE（text/event-stream）极简封装：把一个同步生成器变成流式响应。

不引额外依赖：每条事件就是「data: 一行 JSON + 空行」。同步生成器会由 Starlette 放到
线程池里迭代（`iterate_in_threadpool`），这正好是本项目需要的——分类与生成都是阻塞 IO，
直接在事件循环里跑会把整个服务卡住。

约定的事件形状（前端只认 `type`；预览事件另带 `kind` 标出属于哪一类）：

    {'type': 'start', ...}                      这次要做什么（含 messages / 要跑的序号）
    {'type': 'delta', 'index': 3, 'kind': 'interpretation', 'text': '…'}
                                                潜台词这次新写出来的片段（增量，前端往后接）
    {'type': 'item', 'index': 3, 'kind': 'suggestions',
     'suggestion': {'label': '…', 'text': '…'}} 新写完整的一条建议
    {'type': 'reset', 'index': 3, 'kind': '…'}  这一类的这一条在重试，清掉它上一轮预览
    {'type': 'message', 'item': {...}}          某条消息分类完了（整段分析）
    {'type': 'retrying' | 'failed', 'index': 3} 断连补跑中 / 最终失败
    {'type': 'done', ...}                       收尾：完整结果（含落库后的 session_id）
    {'type': 'error', 'message': '…'}           中途出错（HTTP 头早已发出，只能靠事件告知）

`kind` 不能省：右键「一键生成」会在同一条消息上并发跑两类生成，前端要按 kind 各留一个槽位，
否则一类的 `reset` 会把另一类已经写出来的预览抹掉（`kind` 缺失时前端只能整条清空）。
"""
from __future__ import annotations

import json
from typing import Iterable, Iterator

from fastapi.responses import StreamingResponse

from app.api.errors import error_event
from app.core.config import VERSION

SSE_HEADERS = {
    'Cache-Control': 'no-store',
    # 走反向代理时别攒着一次吐；本地直连虽用不到，但留着更保险。
    'X-Accel-Buffering': 'no',
    'X-Jev': VERSION,
}


def sse_event(payload: dict) -> str:
    return 'data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'


def sse_response(events: Iterable[dict]) -> StreamingResponse:
    """把事件生成器包成 SSE 响应；生成器中途抛错也转成一条 `error` 事件。

    注意：响应头在第一个事件之前就发出去了（状态码固定 200），所以「业务错误」只能靠
    `error` 事件告诉前端——describe_error 的映射与普通端点完全一致。
    """

    def stream() -> Iterator[str]:
        try:
            for event in events:
                yield sse_event(event)
        except Exception as exc:  # noqa: BLE001 - 任何异常都要变成一条事件，不能只留半截流
            yield sse_event(error_event(exc))

    return StreamingResponse(stream(), media_type='text/event-stream', headers=SSE_HEADERS)
