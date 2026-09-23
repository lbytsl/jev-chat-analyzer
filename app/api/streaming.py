"""SSE（text/event-stream）极简封装：把一个同步生成器变成流式响应。

不引额外依赖：每条事件就是「data: 一行 JSON + 空行」。同步生成器由线程池逐条取
（`run_in_threadpool`）——分类与生成都是阻塞 IO，直接在事件循环里跑会把整个服务卡住。

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

另外还有一层「客户端走了就别再算了」：取事件的间隙检查 `request.is_disconnected()`，断开就把
`cancel` 置位并停下来。没有这一步的话，用户关掉页面 / 切到别的会话之后，后端仍会把剩下几十条
消息跑完（每条 2 次 Jev 调用，是真金白银）。
"""
from __future__ import annotations

import json
import threading
from typing import Iterable, Iterator

from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.errors import error_event
from app.core.config import VERSION
from app.core.logging import get_logger

logger = get_logger('stream')

SSE_HEADERS = {
    'Cache-Control': 'no-store',
    # 走反向代理时别攒着一次吐；本地直连虽用不到，但留着更保险。
    'X-Accel-Buffering': 'no',
    'X-Jev': VERSION,
}

# 「生成器取完了」的哨兵：StopIteration 不能跨线程/协程边界乱抛，用哨兵代替。
_END = object()


def sse_event(payload: dict) -> str:
    return 'data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'


def _next_event(iterator: Iterator[dict]):
    return next(iterator, _END)


def sse_response(events: Iterable[dict], request: Request | None = None,
                 cancel: threading.Event | None = None) -> StreamingResponse:
    """把事件生成器包成 SSE 响应；生成器中途抛错也转成一条 `error` 事件。

    注意：响应头在第一个事件之前就发出去了（状态码固定 200），所以「业务错误」只能靠
    `error` 事件告诉前端——describe_error 的映射与普通端点完全一致。

    传了 `request` + `cancel` 时启用断连检测（两个都要给：没有 cancel 就没法把「别算了」
    告诉流水线，只检测不做事的断连检查等于没写）。
    """

    async def stream():
        iterator = iter(events)
        try:
            while True:
                # 阻塞取事件交给线程池：分类与生成都是阻塞 IO，不能占着事件循环。
                event = await run_in_threadpool(_next_event, iterator)
                if event is _END:
                    break
                if request is not None and cancel is not None and await request.is_disconnected():
                    logger.info('客户端已断开，停止推送并取消后续分析')
                    cancel.set()
                    break
                yield sse_event(event)
        except Exception as exc:  # noqa: BLE001 - 任何异常都要变成一条事件，不能只留半截流
            yield sse_event(error_event(exc))
        finally:
            # 让流水线在 yield 点收到 GeneratorExit：它会停在下一个检查点，
            # 而不是把剩下的消息继续算完。
            close = getattr(iterator, 'close', None)
            if close is not None:
                close()

    return StreamingResponse(stream(), media_type='text/event-stream', headers=SSE_HEADERS)
