"""进程级共享的线程池。

原来每个请求都 `with ThreadPoolExecutor(...)` 现场建池：一次整段分析会建两个池、约 8 个
线程，请求一多就是线程放大。线程池的意义本来就是复用，这里按用途（Jev 分类 / 生成层）
各留一个，配置里的并发度变了就换一个新的。
"""
from __future__ import annotations

import atexit
import threading
from concurrent.futures import ThreadPoolExecutor

_POOLS: dict[str, ThreadPoolExecutor] = {}
_SIZES: dict[str, int] = {}
_RETIRED: list[ThreadPoolExecutor] = []
_GUARD = threading.Lock()


def shared_pool(name: str, max_workers: int) -> ThreadPoolExecutor:
    """按名字取共享线程池；并发度与配置不一致时换一个新的。

    旧的池只**退休**、不立刻 `shutdown`：一次整段分析可能正在用它（要跑几十秒），
    这时候把池关掉会直接把它打断（后续 `submit` 会抛 `cannot schedule new futures
    after shutdown`）。退休的池在进程退出时统一收尾——并发度属于启动期配置，
    真被反复改动的概率很低。
    """
    workers = max(1, int(max_workers))
    with _GUARD:
        pool = _POOLS.get(name)
        if pool is not None and _SIZES.get(name) == workers:
            return pool
        if pool is not None:
            _RETIRED.append(pool)
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
        _POOLS[name] = pool
        _SIZES[name] = workers
        return pool


def shutdown_pools() -> None:
    """收尾所有线程池（进程退出时自动调用；测试里也可手动重置）。"""
    with _GUARD:
        for pool in [*_POOLS.values(), *_RETIRED]:
            pool.shutdown(wait=False)
        _POOLS.clear()
        _SIZES.clear()
        _RETIRED.clear()


atexit.register(shutdown_pools)
