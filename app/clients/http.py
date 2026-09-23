"""进程级共享的 HTTP 客户端与超时口径。

为什么必须共享：一条消息要调 2 次 Jev，50 条就是 100 次请求；生成层同理。每次调用都
`with httpx.Client(...)` 新建，等于每请求重做一遍 TCP + TLS 握手（实测一次 ~200ms），
而且完全吃不到 keep-alive。这里把两个上游的客户端做成进程内单例，按 host 复用连接池。

为什么**不**在「保存配置」时关掉它们：客户端里只有连接池，不含 base_url / 密钥（那是
每次请求带上的），所以换了配置没有陈旧状态可言；而配置保存可能与一个跑了半分钟的整段
分析并发，这时把客户端关掉会直接把那次分析打断。旧 host 的空闲连接由 httpx 的
keepalive_expiry 自行回收，不需要我们插手。
"""
from __future__ import annotations

import atexit
import threading

import httpx

# 分项超时：连接要短（TCP 建连失败不该干等 45 秒），读要长（模型推理本来就慢）。
CONNECT_TIMEOUT = 5.0
WRITE_TIMEOUT = 10.0
POOL_TIMEOUT = 10.0

_CLIENTS: dict[str, httpx.Client] = {}
_GUARD = threading.Lock()


def shared_client(name: str, *, max_connections: int = 8) -> httpx.Client:
    """按名字取进程级共享客户端（不存在就建一个）。

    httpx.Client 本身是线程安全的，可以被并发请求共用——这正是这里要的效果。
    """
    with _GUARD:
        client = _CLIENTS.get(name)
        if client is None or client.is_closed:
            client = httpx.Client(
                limits=httpx.Limits(max_connections=max_connections,
                                    max_keepalive_connections=max_connections),
                timeout=httpx.Timeout(connect=CONNECT_TIMEOUT, read=60.0,
                                      write=WRITE_TIMEOUT, pool=POOL_TIMEOUT),
            )
            _CLIENTS[name] = client
        return client


def read_timeout(seconds: float) -> httpx.Timeout:
    """单次请求的超时：读超时按调用方给的预算走，其余分项保持默认。

    为什么要覆盖读超时：单次 Jev 调用实测 13-15s，但重试总时长必须有上限（见 clients/jev.py
    的 TOTAL_BUDGET_SECONDS），所以每次尝试的读超时要按剩余预算收窄。
    """
    return httpx.Timeout(connect=CONNECT_TIMEOUT, read=seconds,
                         write=WRITE_TIMEOUT, pool=POOL_TIMEOUT)


def close_shared_clients() -> None:
    """关掉所有共享客户端（进程退出时自动调用；测试里也可手动重置）。"""
    with _GUARD:
        for client in _CLIENTS.values():
            try:
                client.close()
            except Exception:  # noqa: BLE001, S110 - 收尾动作，关不掉也不该影响退出
                pass
        _CLIENTS.clear()


atexit.register(close_shared_clients)
