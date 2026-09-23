"""共享线程池：按名字复用、并发度变了换池（但**不**打断在途任务）。"""
from __future__ import annotations

from app.core.executors import shared_pool


def test_pool_is_reused_by_name():
    """线程池要复用：每请求新建一个池，也就丢掉了「池」的意义。"""
    first = shared_pool('test-reuse', 2)
    assert shared_pool('test-reuse', 2) is first


def test_pool_is_replaced_when_the_size_changes():
    """并发度是可配置的（JEV_MAX_WORKERS / GEN_MAX_WORKERS），改了要生效。"""
    original = shared_pool('test-resize', 2)
    resized = shared_pool('test-resize', 3)
    assert resized is not original
    assert shared_pool('test-resize', 3) is resized
