"""重试退避口径：两个上游客户端共用一份。

原来两边口径不一样、还都写死在各自文件里（Jev 用 1.2 的倍数、生成层用 1.5 的倍数），
改一处忘一处。这里收敛成一条：`BASE_DELAY_SECONDS * (attempt + 1)`，尊重上游的
`Retry-After`，上限 `MAX_DELAY_SECONDS`，再加抖动。

抖动不是装饰：一批 4-6 个工作线程会在同一时刻一起失败、一起重试，整齐的间隔会让上游
在同一秒再挨一次同样的冲击。这里只**往下**抖（0.8~1.0 倍），所以实际等待不会超过上限。
"""
from __future__ import annotations

import random

BASE_DELAY_SECONDS = 1.2
MAX_DELAY_SECONDS = 8.0
JITTER_RATIO = 0.2


def backoff_delay(attempt: int, retry_after: float | None = None) -> float:
    """第 `attempt` 次（从 0 起）失败之后该等多久。

    `retry_after` 是上游 `Retry-After` 头解析出来的秒数（拿不到就传 None）——它比我们的
    猜测权威，所以取两者较大的那个，但仍受 `MAX_DELAY_SECONDS` 限制。
    """
    delay = BASE_DELAY_SECONDS * (attempt + 1)
    if retry_after is not None:
        delay = max(delay, retry_after)
    delay = min(delay, MAX_DELAY_SECONDS)
    return delay * (1 - JITTER_RATIO * random.random())


def parse_retry_after(value: str | None) -> float | None:
    """解析 `Retry-After` 头。只认秒数；HTTP-date 形式（少见于这些端点）与脏值都返回 None。

    解析失败不能抛：一个畸形的响应头不该把整条重试链路打断。
    """
    try:
        seconds = float((value or '').strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None
