"""健康检查：启动脚本靠它判断端口上跑的是哪一版、key 有没有读到。"""
from __future__ import annotations

from fastapi import APIRouter

from app.core.config import VERSION, get_settings
from app.schemas.common import HealthResponse

router = APIRouter(tags=['基础'])


@router.get('/health', response_model=HealthResponse, summary='服务与配置状态')
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        ok=settings.jev_configured,
        api_key='已配置' if settings.jev_configured else '缺失',
        general_llm='已配置' if settings.generation_configured else '缺失',
        model=settings.typesafe_default_model,
        gen_model=settings.deepseek_model,
        version=VERSION,
    )
