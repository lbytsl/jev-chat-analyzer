"""依赖注入：把服务实例的生命周期收在这里。

服务本身无状态（请求级数据靠传参），所以进程内共享单例即可；测试用
`app.dependency_overrides[get_pipeline] = lambda: FakePipeline()` 替换掉真实上游。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.services.classifier import ClassifierService
from app.services.pipeline import PipelineService
from app.services.sessions import SessionService
from app.services.settings import SettingsService


@lru_cache(maxsize=1)
def get_pipeline() -> PipelineService:
    return PipelineService()


@lru_cache(maxsize=1)
def get_classifier() -> ClassifierService:
    return ClassifierService()


@lru_cache(maxsize=1)
def get_session_service() -> SessionService:
    return SessionService()


@lru_cache(maxsize=1)
def get_settings_service() -> SettingsService:
    return SettingsService()


def reset_service_caches() -> None:
    """可视化配置保存后调用：丢掉服务实例，让下一次请求按新的 .env 重新构造。

    不重启进程就生效的关键——只清缓存不重启，避免「文件改了但还在用旧配置」。
    """
    get_pipeline.cache_clear()
    get_classifier.cache_clear()
    get_session_service.cache_clear()
    get_settings_service.cache_clear()


PipelineDep = Annotated[PipelineService, Depends(get_pipeline)]
ClassifierDep = Annotated[ClassifierService, Depends(get_classifier)]
SessionDep = Annotated[SessionService, Depends(get_session_service)]
SettingsDep = Annotated[SettingsService, Depends(get_settings_service)]
